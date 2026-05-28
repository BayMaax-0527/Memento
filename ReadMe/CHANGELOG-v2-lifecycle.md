# v2 记忆生命周期管理系统 — 升级记录

> 版本：v2 Lifecycle | 实施日期：2026-05-28 | 代码量：~951 行（lifecycle.py）+ 41 行（SQL 迁移）+ 78 行（YAML 配置）

---

## 一、改造前 vs 改造后的对比

| 维度 | 改造前（v2 base + v3 版本管理） | 改造后（v2 Lifecycle） |
|------|-------------------------------|----------------------|
| 状态体系 | `active` / `downgraded` / `superseded` | 新增 `stale` / `archived`，形成完整五态：`active → stale → archived` 和 `active → downgraded → superseded` |
| 排序依据 | `weight` + `session_count` + 时间衰减（粗略） | `sliding_hit_count * 5` + `hit_count * 1` + `freshness_score * 10 * (weight/100)` — **时效感知排序** |
| 去重机制 | `soft_dedup()` 字符串相似度对比（SequenceMatcher, 阈值 0.8） | **语义去重**：基于 2560 维 Embedding 余弦相似度，≥0.85 合并，0.65~0.85+否定词 supersede |
| 冲突检测 | 无 | 基于否定词白名单 + 语义相似度的自动冲突检测 |
| 时间衰减 | 硬编码的粗略 weight 衰减（-2/周期, -5/30天） | `freshness_score` 精确计算：按 `lifecycle_category` 区分衰减窗口（30d / 180d / INF） |
| 引用追踪 | 无 | `hit_count` / `sliding_hit_count` / `last_ref_session` 全生命周期追踪 |
| 深度归档 | 无 | `compact` 命令：自动归档 freshness=0 且 60 天 stale 的条目 |
| 恢复机制 | 无 | `recover --id N` 和 `recover --query Q` 两种恢复方式 |
| 健康报告 | 无 | `compact` 输出状态分布饼图 + top-5 最久未命中 |
| 配置 | 参数硬编码在代码中 | 全部集中到 `config/lifecycle.yaml` |
| 关联表 | `memory_links` 无跳转字段 | 新增 `redirect_to` 字段，支持跳转追踪 |

---

## 二、新增的机制

### 2.1 语义去重 + 冲突检测（步骤 A）

**原理**：对新写入的 L0 条目，调用 Embedding API（qwen3-embedding-4b-mxfp8）生成向量，与 profile 库中现有 active/downgraded 条目逐条计算余弦相似度。

**阈值体系**：
- **≥ 0.85**（merge_threshold）：内容高度一致 → **合并**到旧条目（weight+5, hit_count+1, freshness=1.0），继承 memory_links，新条目标记 superseded
- **0.65 ~ 0.85**（conflict_threshold）：语义相关但不完全相同
  - 含否定词 → **Supersede**：旧条目→superseded，links 继承到新条目
  - 无否定词 → 创建 `conflict_similar` 关联链接
- **< 0.65**：不相关 → 正常写入新条目

**否定词白名单**：25 个中文+英文否定词（lifecycle.yaml 中完整定义），作为辅助信号使用

**边界情况**：
- 目标条目为 `downgraded`（二击保险中）时跳过合并
- Embedding 生成失败时跳过语义比对，条目作为新条目写入
- 含否定词的条目优先比对（排在列表最前）

### 2.2 超时衰减（步骤 B）

**freshness_score 计算公式**：
```
freshness_score = max(0.0, 1.0 - 距离最后命中的天数 / 衰减窗口)
```

**三档衰减窗口**：

| lifecycle_category | 衰减窗口 | 适用内容 |
|-------------------|---------|---------|
| `daily`（日常） | 30 天 | 默认分类，日常分析发现 |
| `arch`（架构） | 180 天 | 架构/设计/选型/重构/框架决策 |
| `rule`（规则） | 永不衰减（-1 → INF） | 流程/规范/规则/政策/标准 |

分类通过关键词自动匹配（关键词列表在 lifecycle.yaml 中配置），不硬编码。

**边界情况**：
- `last_accessed_at` 为 NULL → freshness=1.0（新条目默认最高分）
- `lifecycle_category` 未设置时自动调用 `_classify_l0()` 依据 abstract 文本关键词判定
- `decay_window_rule: -1` 在代码中转为 `float('inf')`，freshness 始终为 1.0

### 2.3 时效标签判定（步骤 C）

**状态转移规则**：
```
active / downgraded
  └─ freshness < 0.2（stale_freshness_threshold） → stale

stale
  └─ freshness = 0.0 AND hit_count = 0 → archived
```

**参数控制**（来自 lifecycle.yaml）：
- `stale_freshness_threshold: 0.2` — freshness 低于此值触发 stale 候选
- `stale_miss_count: 2` — 连续 N 次注入零命中确认 stale
- `archive_freshness_zero_days: 60` — compact 时 freshness=0 持续 60 天 + stale → archive

**边界情况**：
- superseded 和 archived 条目跳过判定
- `to_archive` 在 `step_c_label()` 中是严格条件（freshness=0 AND hit_count=0），独立于 compact 中的深度归档
- 转入 stale 时不会自动触发 archive，archive 主要靠 compact 命令批量执行

### 2.4 引用计数更新（步骤 D）

**更新内容**：
- Profile 库：`hit_count + 1`, `sliding_hit_count + 1`, `last_accessed_at`, `last_ref_session`
- 全局库：通过 id_map 转换后同步更新，额外同步 `freshness_score`

**边界情况**：
- 引用计数 = profile 库本地 memory_links 引用数 + 全局库内存（通过 global ID 查询）
- `referenced_ids` 为空时不执行
- 全局库用 `IFNULL(hit_count, 0) + 1` 兼容旧数据

### 2.5 同步 Profile → 全局库

**`_sync_to_global()`** 机制：
1. 精确匹配：`SELECT ... WHERE abstract = ?`
2. 模糊匹配兜底：`WHERE abstract LIKE ?`（前 30 字符 + `%`）
3. 同步 7 个字段：status, superseded_by_id, freshness_score, hit_count, sliding_hit_count, lifecycle_category, weight
4. 维护 profile_id → global_id 的 id_map（用于引用计数跨库查询）

**边界情况**：同步失败（全局库无匹配）时记录 warn 日志，不阻塞后续流程

### 2.6 定期压缩（compact 命令）

`auto.py compact` → `lifecycle.compact()`

**操作流程**：
1. 统计当前状态分布（总数 / active / stale / superseded / archived）
2. 深度归档：`status='stale' AND freshness_score=0.0 AND last_accessed_at < 60天前` → archived
3. 清理孤立链接：全局库中 > 90 天的 `related` 链接，且目标已不存在
4. 输出健康报告（ASCII 表格 + top-5 最久未命中）

**边界情况**：
- 只清理 `relation='related'` 的旧链接，保留 `tag_overlap` / `inferred` / `redirected`
- 同步更新全局库的 status

### 2.7 恢复命令（recover）

**两种恢复方式**：
- **`recover --id N`**：精确恢复指定 ID，将 status 设为 active，freshness=1.0，同步全局库，解除所有指向此条目的 `superseded_by_id` 指针
- **`recover --query Q`**：语义搜索匹配，对所有 archived 条目计算余弦相似度，恢复 top-3 中相似度 > 0.5 的条目

**辅助命令**：
- **`list-archived`**：列出所有 archived 条目（ID + 摘要片段 + 创建时间）
- **`lifecycle.py --mode=status`**：生命周期状态分布概览

---

## 三、数据库改动

### 3.1 abstracts 表新增字段

| 列名 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `last_ref_session` | TEXT | NULL | 最后引用的会话标识 |
| `hit_count` | INTEGER | 0 | 历史总命中数 |
| `sliding_hit_count` | INTEGER | 0 | 滑动窗口内命中数 |
| `freshness_score` | REAL | 1.0 | 时效系数 [0, 1] |
| `lifecycle_category` | TEXT | 'daily' | 生命周期分类：daily / arch / rule |

### 3.2 memory_links 表新增字段

| 列名 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `redirect_to` | INTEGER | NULL | 跳转目标 ID（合并/supersede 时的重定向） |

### 3.3 迁移 SQL（Engine/migrations/v2_lifecycle.sql）

```sql
-- 所有 ALTER TABLE 语句
ALTER TABLE abstracts ADD COLUMN last_ref_session TEXT;
ALTER TABLE abstracts ADD COLUMN hit_count INTEGER DEFAULT 0;
ALTER TABLE abstracts ADD COLUMN sliding_hit_count INTEGER DEFAULT 0;
ALTER TABLE abstracts ADD COLUMN freshness_score REAL DEFAULT 1.0;
ALTER TABLE abstracts ADD COLUMN lifecycle_category TEXT DEFAULT 'daily';
ALTER TABLE memory_links ADD COLUMN redirect_to INTEGER;

-- 冷启动初始化：为现有数据设置基线
UPDATE abstracts SET
  last_accessed_at   = COALESCE(last_accessed_at, datetime('now','localtime')),
  hit_count          = 1,
  sliding_hit_count  = 1,
  freshness_score    = 1.0,
  status             = COALESCE(NULLIF(status, ''), 'active'),
  lifecycle_category = CASE
    WHEN abstract LIKE '%架构%' OR ... THEN 'arch'
    WHEN abstract LIKE '%流程%' OR ... THEN 'rule'
    ELSE 'daily'
  END
WHERE source_type = 'memory';
```

**需执行迁移的数据库**：
- Profile 库：`~/.hermes/profiles/<name>/memory-vault/abstracts.db`
- 全局库：`~/workspace/Memento/Memory/abstracts.db`

**知识库不需要执行此迁移**（生命周期管理仅针对记忆，知识库无 freshness 和状态转移）。

---

## 四、代码改动

### 4.1 新增文件

| 文件 | 行数 | 职责 |
|------|------|------|
| `Engine/hooks/lifecycle.py` | 951 行 | 生命周期管理器（核心） |
| `Engine/config/lifecycle.yaml` | 78 行 | 生命周期参数配置 |
| `Engine/migrations/v2_lifecycle.sql` | 41 行 | 数据库迁移脚本 |

### 4.2 改动文件

| 文件 | 改动 | 说明 |
|------|------|------|
| `Engine/hooks/remember.py` | 末尾追加生命周期调用 | 注入完成后调用 `lifecycle.run()` |
| `Engine/src/retriever.py` | 检索过滤+排序公式更新 | `WHERE status IN ('active','downgraded','stale')`，排序含 freshness_score |
| `Engine/hooks/auto.py` | 新增 recover / compact / list-archived 命令 | 通过 `--source` 分发到 lifecycle 模块 |

### 4.3 生命周期入口（remember.py 第 1134-1146 行）

```python
try:
    from lifecycle import run as lifecycle_run
    new_abstracts_dict = dict(zip(abstract_ids, l0))
    lifecycle_run(
        profile=profile,
        new_abstracts=new_abstracts_dict,
        new_global_ids=global_ids,
        referenced_ids=abstract_ids,
    )
    logger.info("生命周期管理完成")
except Exception as e:
    logger.warning("生命周期管理跳过（非致命）: %s", e)
```

### 4.4 检索过滤更新（retriever.py search() 第 141 行）

```python
# 改造前：
AND a.status IN ('active', 'downgraded')

# 改造后：
AND a.status IN ('active', 'downgraded', 'stale')
```

### 4.5 排序公式更新（retriever.py search() 第 143-144 行）

```python
ORDER BY (
    (COALESCE(a.sliding_hit_count,0) * 5) +
    (COALESCE(a.hit_count,0) * 1) +
    (COALESCE(a.freshness_score,1.0) * 10 * CAST(a.weight AS REAL) / 100.0)
) DESC
```

### 4.6 auto.py 新增命令

`auto.py main()` 新增分支（第 132-149 行）：
- `auto.py recover --id N` → `lifecycle.recover_by_id()`
- `auto.py recover --query Q` → `lifecycle.recover_by_query()`
- `auto.py recover` / `auto.py list-archived` → `lifecycle.list_archived()`
- `auto.py compact` → `lifecycle.compact()`

---

## 五、配置改动

### 5.1 新增配置文件

**`Engine/config/lifecycle.yaml`** — 所有生命周期参数集中管理：

```yaml
# ── 语义去重 ──
merge_threshold: 0.85            # ≥ 0.85 → 合并
conflict_threshold: 0.65         # 0.65~0.85 → 冲突检测

# ── 否定词白名单 ──
negation_keywords: [ ... 25 个关键词 ... ]

# ── 超时衰减（天） ──
decay_window_daily: 30           # 日常分析发现
decay_window_arch: 180           # 架构/设计决策
decay_window_rule: -1            # -1 = INF（永不衰减）

# ── 时效标签判定 ──
stale_freshness_threshold: 0.2   # freshness 低于此值 → stale 候选
stale_miss_count: 2              # 连续 N 次注入零命中 → stale
archive_freshness_zero_days: 60  # freshness=0 持续 60 天 + stale → archive

# ── 排序公式权重 ──
sort_sliding_weight: 5
sort_history_weight: 1
sort_freshness_weight: 10

# ── L0 分类关键词 ──
classify_arch_keywords: [架构, 设计, 方案, ...]
classify_rule_keywords: [流程, 规范, 规则, ...]
```

### 5.2 Memory/config.yaml 未变

现有 `Memory/config.yaml` 不需要修改 — 生命周期配置完全独立在 `lifecycle.yaml` 中。

---

## 六、执行流程总览

### 注入时（remember.py main()）

```
1. 读取会话 / 文档
2. DeepSeek 压缩 → L2
3. LM Studio 提取 → L0 + L1 + 结构化字段 + decisions
4. 写 profile 库（L0 + memory_meta + L1 + L2）
5. 同步全局库（L0 + memory_meta + categories/ + decisions）
6. Embedding 生成（存入全局库 embeddings 表）
7. 关联解析（memory_links + 传递闭包推理）
8. 权重更新
9. 生命周期管理 ← 增量追加
   ├─ 步骤 A: 语义去重 + 冲突检测（需要全局库 embeddings）
   ├─ 步骤 B: 超时衰减（遍历 active/downgraded）
   ├─ 步骤 C: 时效标签判定（active→stale→archived）
   └─ 步骤 D: 引用计数更新（命中+1）
   └─ 同步 profile → 全局库
```

### 检索时（retriever.py search()）

```
1. 过滤条件：WHERE status IN ('active', 'downgraded', 'stale')
2. 排序公式：(sliding_hit_count * 5) + (hit_count * 1) + (freshness_score * 10 * weight/100)
3. 输出：abstract + storage_path + version + status + score
```

### 手动维护

```
auto.py compact        → 深度归档 + 健康报告
auto.py recover --id N → 按 ID 恢复
auto.py recover --query Q → 按语义恢复
auto.py list-archived  → 列出归档条目
```

---

## 七、已知约束和边界

### 7.1 生命周期仅覆盖记忆，不覆盖知识库

- 生命周期管理仅在 `remember.py --source session`（记忆注入）时触发
- 知识注入（`--source doc`）不执行 `lifecycle.run()`
- 知识库 DB 没有 `freshness_score` / `hit_count` / `lifecycle_category` 等字段
- 知识库的 status 仅用于版本管理（active / downgraded / superseded），无 stale / archived

### 7.2 语义去重的局限性

- **依赖 Embedding 质量**：Embedding API 不可用（LM Studio 离线/超时）时跳过语义比对
- **O(n²) 复杂度**：新条目数与已有条目数逐条比对，profile 库条目增多时可能变慢
- **逐条比对无索引**：不支持向量索引（ANN），全量遍历
- **仅 Profile 库层面去重**：语义去重作用于 profile 库，全局库通过同步映射间接更新

### 7.3 排序公式的在线配置限制

- `sort_sliding_weight` / `sort_history_weight` / `sort_freshness_weight` 在 lifecycle.yaml 中定义
- **但排序公式硬编码在 retriever.py 的 SQL 中**，不是从配置文件动态读取
- 变更公式需改代码，或手动同步 YAML 值和 SQL

### 7.4 冷启动数据兼容

- 已有数据的 `lifecycle_category` 通过迁移 SQL 的关键词匹配初始化
- `hit_count` / `sliding_hit_count` 冷启动设为 1（而非 0），避免新数据压倒旧数据
- `freshness_score` 冷启动设为 1.0
- 旧数据的 `last_accessed_at` 保持原始值（非冷启动覆盖）

### 7.5 恢复操作的约束

- `recover --query` 仅比较 archived 条目，不比较 stale / active / superseded
- 恢复操作会同步全局库，但不会自动重建 `superseded_by_id` 链
- 恢复操作不会回滚 `memory_links` 中的 `redirected` 或 `inferred` 链接

### 7.6 compact 深度归档的约束

- 仅在 `compact` 命令时执行深度归档（freshness=0 且 60 天 stale），`step_c_label()` 的 archive 条件更严格（freshness=0 AND hit_count=0）
- 超过 90 天的孤立 `related` 链接被清理，但 `tag_overlap` / `inferred` / `redirected` 类型不被清理
- 知识库不受 compact 影响

### 7.7 同步的精度

- Profile → 全局同步使用 LIKE 模糊匹配兜底（前 30 字符），非 exact match
- 当 profile 库和全局库的 abstract 文本不完全一致时可能匹配失败
- 同步失败仅记录 warn 日志，不阻塞后续流程

### 7.8 与现有版本管理（v3）的关系

- lifecycle 的五态管理与 v3 的二击保险共存但不冲突：
  - v3 控制 `active → downgraded → superseded`（版本覆盖链）
  - lifecycle 控制 `active → stale → archived`（时效链）
  - superseded 条目被 lifecycle 跳过（不参与衰减和标签判定）
- 两种状态转移独立运行，互不干扰

---

## 八、文件清单（v2 Lifecycle 新增/改动）

```
Engine/
├── config/
│   └── lifecycle.yaml                        # [新增] 生命周期参数配置
├── migrations/
│   └── v2_lifecycle.sql                      # [新增] 数据库迁移脚本
├── hooks/
│   ├── lifecycle.py                          # [新增] 生命周期管理器
│   ├── remember.py                           # [改动] 末尾追加 lifecycle.run()
│   └── auto.py                               # [改动] 新增 recover/compact/list-archived
├── src/
│   └── retriever.py                          # [改动] 过滤+排序公式
```

新增代码行数：~1070 行（lifecycle.py 951 + lifecycle.yaml 78 + v2_lifecycle.sql 41）
改动代码行数：~30 行（remember.py ~15 + auto.py ~10 + retriever.py ~5）
