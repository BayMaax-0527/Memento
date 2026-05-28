# Memento — 记忆 + 知识库系统

## 一句话

你说"注入记忆" → 压缩当前会话；你说"注入知识" → 压缩文档；然后统一检索。

---

## 核心原则

- **零额外依赖**：SQLite3 + Python requests + yaml，不需要新服务
- **手动触发**：没有自动钩子，全凭你说"注入记忆"、"注入知识"、"查XXX"、"查知识库XXX"
- **双模型分工**：DeepSeek 做压缩（付费可靠），LM Studio 做本机提取和向量化
- **Provider 抽象**：统一 `call_llm()` / `call_embedding()`，支持 LM Studio 本地 / DeepSeek / OpenAI 三种后端，config.yaml 中配置
- **全相对路径**：所有路径相对于 Engine/ 目录自动解析，任意位置解压即用
- **三级存储**：L0 索引(FTS5) / L1 摘要(overviews) / L2 全文(sessions 或 docs)
- **双源隔离**：记忆用 `source_type='memory'`，知识用 `source_type='knowledge'`，检索时互不干扰
- **独立运行**：不嵌入 Hermes plugin，升级互不影响

---

## 三库架构

Memento 使用三个独立的 SQLite 数据库，各自承担不同职责：

| 维度 | 全局记忆库 | 全局知识库 | 专属 Profile Vault |
|------|-----------|-----------|-------------------|
| 来源 | 对话产生的决策/踩坑/经验 | 你给的文档/报告/制度 | 当前 profile 对话的记忆 |
| 存放 | `Memory/abstracts.db`（source_type='memory'） | `Knowledge/abstracts.db`（source_type='knowledge'） + `raw/` + `storage/docs/` + `overviews/docs/` | `~/.hermes/profiles/<name>/memory-vault/` |
| 注入方式 | 自动双写（先专属后全局） | "注入知识" | 注入时自动同步 |
| 检索方式 | `--global-db` / 默认 | `--domain knowledge` | 默认检索范围 |
| 跨 profile | 全局索引可见 | 全局，所有 profile 都能查 | 不跨 |

### 数据库路径

- **Profile DB**: `~/.hermes/profiles/<name>/memory-vault/abstracts.db`
- **Global DB**: `~/workspace/Memento/Memory/abstracts.db`
- **Knowledge DB**: `~/workspace/Memento/Knowledge/abstracts.db`

---

## 注入管线

### 记忆注入（"注入记忆"）

```
你说"注入记忆"
  │
  ▼
hooks/remember.py --source session
  │
  ├─ ① 读取 session JSON（全部消息，无截断）
  │   跳过 system / tool 角色，保留 user + assistant
  │
  ├─ ② deepseek-v4-flash 压缩 → storage/sessions/<日期>_<id>.md (L2)
  │   "保留关键决策、踩坑记录、技术选型、结论和待办事项"
  │
  ├─ ③ LM Studio qwen3.6-35b-a3b-mlx 提取结构化内容
  │   ├─ L0: 3-5 条一句话摘要 → abstracts.db
  │   │   ├─ 写 profile 库时：soft_dedup() 内容去重 + topic 版本覆盖
  │   │   ├─ 写全局库时：soft_dedup() 内容去重 + topic 版本覆盖
  │   │   └─ source_type 隔离：记忆和知识各自独立去重
  │   ├─ L1: Markdown 文件 → overviews/sessions/<日期>_sess_<id>.md
  │   │   含：核心主题、关键决策、踩坑记录、结论与待办、技术细节
  │   ├─ decisions：与已有决策语义对比，新增的 slug 写入 decisions 表
  │   │   （全局唯一，slug UNIQUE 兜底，仅 session 注入时产生）
  │   │   决策匹配不做跨源对比：L0 软去重按 source_type 隔离
  │   └─ structured fields：类别/标签/profile_tag/project/project_type/
  │       decision_type/importance/topic 通过 regex 解析
  │
  ├─ ④ Embedding 生成（2560 维 qwen3-embedding-4b-mxfp8）
  │
  ├─ ⑤ 关联解析：memory_links（关联 slug + tag 重叠 + 传递闭包推理）
  │
  ├─ ⑥ 权重更新（session_count +1, weight 增益/衰减）
  │
  └─ ⑦ 生命周期管理（增量追加）→ hooks/lifecycle.py run()
      ├─ 步骤 A: 语义去重 + 冲突检测
      ├─ 步骤 B: 超时衰减
      ├─ 步骤 C: 时效标签判定
      └─ 步骤 D: 引用计数更新
```

### 知识注入（"注入知识"）

```
你发文档 → 我转 .md → 我执行 remember_doc.py
  │
  ▼
hooks/remember_doc.py <路径>
  │  → hooks/remember.py --source doc --file <路径>
  │
  ├─ ① 保存原始文件到 Knowledge/raw/（重名自动加时间戳）
  │
  ├─ ② deepseek-v4-flash 压缩 → storage/docs/<日期>_<slug>.md (L2)
  │   "保留核心概念、技术原理、架构决策、数据流、关键步骤"
  │
  ├─ ③ LM Studio qwen3.6-35b-a3b-mlx 提取结构化内容
  │   ├─ L0: 3-5 条 → abstracts.db (source_type='knowledge')
  │   └─ L1: → overviews/docs/<日期>_doc_<slug>.md
  │   └─ knowledge_meta: 品牌/品牌档次/类目/人群/话题/关系
  │
  ├─ ④ knowledge_links：传递闭包推理（2 跳 BFS）
  │
  └─ ⑤ 输出 <memory-context>（带分类标签）
```

---

## 三级存储

| 层级 | 存什么 | 存储 | 字符量 | 来源 |
|------|--------|------|--------|------|
| L0 | 一句话摘要 + source_type 标签 | SQLite FTS5 | ~20 tokens/条 | 记忆/知识共用 |
| L1 | 结构化摘要 | overviews/sessions/*.md 或 overviews/docs/*.md | ~200-250 tokens/篇 | 注入时最多 5 篇 |
| L2（会话） | 全量压缩对话 | storage/sessions/*.md | ~440 tokens/篇 | 记忆专用 |
| L2（知识） | 全量压缩文档 | storage/docs/*.md | 视文档大小 | 知识库专用 |
| decisions | 全局唯一决策/结论 | abstracts.db 的 decisions 表 | ~50 tokens 全量 | 记忆注入时产生 |

---

## 数据模型

### abstracts 表（主表，三库共用同一结构）

```sql
CREATE TABLE abstracts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type TEXT NOT NULL,          -- 'memory' / 'knowledge'
    abstract TEXT NOT NULL,              -- L0 摘要
    category TEXT,                       -- 大类（项目开发/薪酬分析/...）
    tags TEXT,                           -- 逗号分隔标签
    weight INTEGER DEFAULT 100,         -- 排序权重
    global_hit_ratio REAL DEFAULT 0.5,  -- 长期命中率
    session_count INTEGER DEFAULT 0,    -- 经历的会话周期数
    last_accessed_at TEXT,              -- 最后命中时间
    created_at TEXT DEFAULT (datetime('now','localtime')),
    storage_path TEXT,                   -- 指向 L1/L2 文件路径
    version INTEGER DEFAULT 1,          -- topic 版本号
    status TEXT DEFAULT 'active',        -- active / downgraded / superseded / stale / archived
    superseded_by_id INTEGER,            -- 被哪个新版本覆盖
    -- v2 生命周期字段：
    last_ref_session TEXT,               -- 最后引用会话
    hit_count INTEGER DEFAULT 0,         -- 历史总命中数
    sliding_hit_count INTEGER DEFAULT 0, -- 滑动窗口内命中数
    freshness_score REAL DEFAULT 1.0,    -- 时效系数 [0, 1]
    lifecycle_category TEXT DEFAULT 'daily' -- daily(30天) / arch(180天) / rule(永不衰减)
);
```

### FTS5 全文索引

```sql
CREATE VIRTUAL TABLE abstracts_fts USING fts5(
    abstract, category, tags,
    content='abstracts', content_rowid='id'
);
-- 三个触发器（AFTER INSERT / AFTER DELETE / AFTER UPDATE）自动同步
```

### memory_meta（记忆域扩展，source_type='memory' 时有值）

```sql
CREATE TABLE memory_meta (
    abstract_id INTEGER PRIMARY KEY,
    profile TEXT,                    -- default / work / private
    profile_tag TEXT,                -- 工作 / 个人
    project TEXT,                    -- hr-efficiency / payroll / ...
    project_type TEXT,               -- 系统开发 / 工具配置 / ...
    decision_type TEXT,              -- 决策 / 踩坑 / 经验 / 待办
    importance TEXT,                 -- high / medium / low
    topic TEXT DEFAULT '',           -- 版本覆盖主题键
    FOREIGN KEY (abstract_id) REFERENCES abstracts(id) ON DELETE CASCADE
);
```

### knowledge_meta（知识域扩展，source_type='knowledge' 时有值）

```sql
CREATE TABLE knowledge_meta (
    abstract_id INTEGER PRIMARY KEY,
    brand_name TEXT,                 -- 珀莱雅 / 欧莱雅
    brand_tier TEXT,                 -- 国货美妆 / 国际大牌
    category_l1 TEXT,                -- 美妆护肤 / 食品饮料
    category_l2 TEXT,                -- 面霜 / 乳制品
    target_audience TEXT,            -- 敏感肌 / 减脂人群
    audience_tag TEXT,               -- 成分党 / 职场白领
    topic TEXT DEFAULT '',           -- 话题键
    relation_to_prev TEXT DEFAULT '',-- replaces / refines / supplements / independent
    FOREIGN KEY (abstract_id) REFERENCES abstracts(id) ON DELETE CASCADE
);
```

### memory_links（关联表）

```sql
CREATE TABLE memory_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL,
    target_abstract_id INTEGER NOT NULL,
    relation TEXT DEFAULT 'related',  -- related / duplicate / superseded / tag_overlap / inferred / conflict_similar / redirected
    created_at TEXT DEFAULT (datetime('now','localtime')),
    redirect_to INTEGER,             -- v2 生命周期：跳转目标
    FOREIGN KEY (source_id) REFERENCES abstracts(id) ON DELETE CASCADE,
    FOREIGN KEY (target_abstract_id) REFERENCES abstracts(id) ON DELETE CASCADE
);
```

### decisions（全局决策表）

```sql
CREATE TABLE decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE,
    category TEXT DEFAULT '',
    content TEXT NOT NULL,
    first_seen TEXT DEFAULT (datetime('now','localtime')),
    last_updated TEXT DEFAULT (datetime('now','localtime')),
    source_sessions TEXT DEFAULT '[]'
);
```

### embeddings（向量存储表）

```sql
CREATE TABLE embeddings (
    source_id INTEGER,
    source_type TEXT,
    model TEXT,
    dimension INTEGER,
    vector BLOB,
    PRIMARY KEY (source_id, source_type, model)
);
```

---

## 检索规则

### 检索入口

| 你说 | 执行 | 检索范围 |
|------|------|---------|
| "查 XXX" | `recall.py --query 'XXX'` | Profile 专属库 / 全局库 |
| "查知识库 XXX" | `recall.py --query 'XXX' --domain knowledge` | 知识库 |
| "查决策" | `recall.py --decisions` | decisions 表 |
| "状态" | `auto.py status` | 三库统计 |

### 检索方式

1. **FTS5 精确搜索**：英文 + 数字分词后 AND 组合，匹配 `abstracts_fts`
2. **LIKE 兜底**：FTS5 失败或无结果时，逐词 LIKE 模糊匹配（中文兼容）
3. **语义搜索**：基于 2560 维 Qwen Embedding 余弦相似度
4. **混合搜索**：FTS5 结果优先 + 语义结果补充去重
5. **LLM 重排序**：对 top-N 结果再排（额外调用一次 LM Studio）

### 生命周期感知排序

检索时过滤 `status IN ('active', 'downgraded', 'stale')`（排除 superseded 和 archived）。

排序公式（`retriever.py search()`）：

```sql
ORDER BY (
    (COALESCE(sliding_hit_count,0) * 5) +       -- 近期活跃度
    (COALESCE(hit_count,0) * 1) +                -- 历史命中
    (COALESCE(freshness_score,1.0) * 10 *        -- 时效性 × weight
     CAST(weight AS REAL) / 100.0)
) DESC
```

- `sliding_hit_count * 5`：近期命中权重最高
- `hit_count * 1`：历史累积贡献较小
- `freshness_score * 10 * (weight/100)`：时效性 × 静态权重
- 排序权重在 `config/lifecycle.yaml` 中定义（`sort_sliding_weight`/`sort_history_weight`/`sort_freshness_weight`），变更后需同步更新 `retriever.py` 中的 SQL 硬编码值

---

## 生命周期管理

> ⚠️ 生命周期管理**仅适用于记忆**（source_type='memory'），知识库暂不参与。知识库的 status 仅用于版本管理（active / downgraded / superseded），无 stale / archived。

生命周期系统在每次注入记忆后自动增量执行，通过 `hooks/lifecycle.py` 实现。所有参数集中在 `config/lifecycle.yaml`。

### 语义去重 & 冲突检测

**步骤 A** — 对新写入的 L0 条目，与 profile 库中现有 active/downgraded 条目做余弦相似度对比：

| 相似度区间 | 条件 | 行为 |
|-----------|------|------|
| ≥ 0.85 (merge_threshold) | 任意 | **合并**：增量更新已有条目（weight+5, hit_count+1, freshness=1.0），继承 memory_links，标记新条目为 superseded |
| ≥ 0.85 (merge_threshold) | 目标为 downgraded | **跳过合并**：二击保险中的旧条目不受影响，新条目保持独立 |
| 0.65 ~ 0.85 (conflict_threshold) | 含否定词 | **Supersede**：旧条目标记 superseded，links 继承到新条目 |
| 0.65 ~ 0.85 (conflict_threshold) | 无否定词 | **记录冲突关联**：创建 `conflict_similar` 链接，不做覆盖 |
| < 0.65 | — | **新条目**：正常添加 |

**否定词白名单**（在 lifecycle.yaml 中完整定义）：不行、推翻、废弃、不再、改用、替换、别用、废除、弃用、supersede、deprecate、drop、remove、replace with、replaced by、abandon、retire、obsolete 等

Embedding 生成失败时跳过语义比对，直接作为新条目写入。

### 超时衰减

**步骤 B** — 遍历所有 active/downgraded/stale L0，重新计算 `freshness_score`：

```
freshness_score = max(0.0, 1.0 - delta_days / decay_window)
```

衰减窗口按 `lifecycle_category` 区分：

| 分类 | 关键词匹配 | 衰减窗口 | 含义 |
|------|-----------|---------|------|
| `daily` | 默认（不匹配 arch 和 rule 的） | 30 天 | 日常分析发现，短期有效 |
| `arch` | 架构、设计、方案、选型、architecture、design、重构、框架、体系、架构决策 | 180 天 | 架构/设计决策，长期有效 |
| `rule` | 流程、规范、规则、必须、禁止、policy、rule、standard、标准、制度、约定 | 永不衰减 (-1 → INF) | 规则/规范类，永久有效 |

分类通过关键词自动匹配（关键词列表在 lifecycle.yaml 中配置），不硬编码在代码中。

`last_accessed_at` 为空时 freshness=1.0（新条目默认最高分）。

### 时效标签判定

**步骤 C** — 综合 `freshness_score` 和 `hit_count` 判定状态转移：

```
active / downgraded → stale:    freshness < 0.2 (stale_freshness_threshold)
                                且滑动窗口内零命中（sliding_hit_count == 0）

stale → archived:                freshness = 0.0
                                且 hit_count = 0
                                且（compact 时额外要求 last_accessed_at < 60 天前）
```

状态层次结构：
- `active`：当前有效，正常检索显示
- `downgraded`：二击保险中（第一次被标记覆盖），检索仍显示
- `stale`：过时候选，检索仍显示但排序靠后
- `superseded`：已被替代，默认隐藏（`--include-obsolete` 可见）
- `archived`：深度归档，默认隐藏，需 `recover` 恢复

Superseded 和 archived 条目跳过时效判定。

### 引用计数更新

**步骤 D** — 更新本轮引用的 L0 条目的命中计数：

- Profile 库引用计数：查 `memory_links` 中 `target_abstract_id` 的引用数
- 全局库引用计数：通过 `global_id_map` 转换后查全局 `memory_links`
- 更新字段：`hit_count + 1`、`sliding_hit_count + 1`、`last_accessed_at`、`last_ref_session`
- 全局库同步：同步更新 freshness_score

### 同步 Profile → 全局库

`_sync_to_global()` — 将 profile 库的生命周期状态同步到全局库：

- 先精确匹配 abstract 文本
- 失败后回退到 LIKE 模糊匹配（前 30 字符）
- 同步字段：status、superseded_by_id、freshness_score、hit_count、sliding_hit_count、lifecycle_category、weight
- 维护 profile_id → global_id 的 id_map

### 定期压缩

**`auto.py compact`** — 深度归档 + 健康报告：

1. 统计当前状态分布（active / stale / superseded / archived）
2. 深度归档：freshness=0 且 stale 超过 60 天的条目 → archived
3. 清理全局库中超过 90 天无引用的孤立 `related` 链接
4. 输出健康报告（状态占比 + top-5 最久未命中 + 决策链路完整性检查）

### 恢复命令

**`auto.py recover`** — 恢复已归档条目：

| 子命令 | 用法 | 说明 |
|--------|------|------|
| `recover --id N` | 按 ID 恢复 | 将指定 ID 的条目 status 设为 active，freshness=1.0，同步全局库 |
| `recover --query Q` | 按语义匹配恢复 | 对所有 archived 条目做语义搜索，恢复 top-3 中相似度 > 0.5 的条目 |
| `recover` / `list-archived` | 列出所有 archived | 显示 ID、摘要片段和创建时间 |

恢复时自动同步全局库，并解除所有指向此条目的 `superseded_by_id` 指针。

---

## 版本管理（v3 已有）

### topic 版本覆盖

同一话题重复注入时的版本控制：

```sql
-- abstracts 表字段
version INTEGER DEFAULT 1           -- 当前版本号
status TEXT DEFAULT 'active'         -- active / downgraded / superseded
superseded_by_id INTEGER DEFAULT NULL  -- 被哪个新版本覆盖

-- memory_meta 和 knowledge_meta 字段
topic TEXT DEFAULT ''                -- 稳定主题键
```

**写入时：**
1. LLM 提取时生成 `【topic】`
2. 写库前查同 profile + 同 topic 的 active/downgraded 记录
3. 存在 → 二击保险处理
4. 不存在 → 正常 INSERT，version=1

**检索时：**
- 默认 `WHERE status IN ('active', 'downgraded', 'stale')`
- `--include-obsolete` 可查看全部版本链（含 superseded）

### 二击保险

| 关系 | 含义 | 旧知识处理 |
|------|------|-----------|
| `replaces` | 新替代旧 | 第一次 → `downgraded`（保险期），第二次 → `superseded` |
| `refines` | 新细化修正旧 | 同上二击保险 |
| `supplements` | 新补充旧 | 旧保留 `active`，两条共存 |
| `independent` | 不同维度无关 | 旧保留 `active` |

### 传递闭包推理

```python
_infer_transitive_links(db, source_ids, max_hops=2)
```

注入完成后自动 BFS 遍历新条目的 memory_links：
- 1 跳直达
- 2 跳传递：A→B, B→C ⇒ A→C (inferred)
- `max_hops=2`：2 跳以上不推理
- `INSERT OR IGNORE`：已存在的链接不重复
- `relation='inferred'`：区分手动关联和推理关联

---

## 权重系统

权重在全局库中追踪，跨 profile、多会话：

| 字段 | 初始值 | 更新时机 |
|------|-------|---------|
| `weight` | 100 | 注入时命中 +5（上限 100）；3+ 周期未命中 -2（下限 5）；30 天未命中额外 -5（下限 5） |
| `session_count` | 0 | 每次注入 +1 |
| `global_hit_ratio` | 0.5 | 每周期计算：命中数/总数 |
| `last_accessed_at` | 创建时间 | 每次被引用时更新 |

命中调权 > 时间衰减：一条记忆只要仍在被使用，权重不会因时间流逝而下降。

---

## 双模型分工

| | deepseek-v4-flash | LM Studio qwen3.6-35b-a3b-mlx |
|---|---|---|
| 做什么 | 全量压缩（会话/文档） | 从压缩结果提取结构 |
| 上下文 | 128k | ~10k（只需处理压缩后的 L2） |
| 费用 | API 按量计费 | 本地 LM Studio 0 费用 |
| 可靠性 | 高，云 API | 中等 |
| 调用方式 | `call_llm(config_key="compress")` | `call_llm(config_key="main")` |
| Embedding | — | `qwen3-embedding-4b-mxfp8`（2560 维） |

---

## 配置管理

### Memory/config.yaml（核心配置）

```yaml
models:
  main:              # 本地模型（提取结构）
    provider: lmstudio
    model: qwen3.6-35b-a3b-mlx
    base_url: http://localhost:1234/v1
  compress:          # 云模型（压缩）
    provider: deepseek
    model: deepseek-v4-flash
    base_url: https://api.deepseek.com/v1
  embed:             # 向量模型
    provider: lmstudio
    model: qwen3-embedding-4b-mxfp8
    base_url: http://localhost:1234/v1
    dimension: 2560
paths:
  global_vault: ../Memory
  knowledge_base: ../Knowledge
  logs_dir: logs
  profile_vault_base: ~/.hermes/profiles
retrieval:
  auto_inject_limit: 5
```

### config/lifecycle.yaml（生命周期参数）

所有生命周期参数集中管理：
- 语义去重阈值（merge_threshold: 0.85, conflict_threshold: 0.65）
- 否定词白名单（25 个关键词）
- 衰减窗口（daily: 30天, arch: 180天, rule: INF）
- 时效标签阈值（stale_freshness_threshold: 0.2）
- 排序公式权重（sliding: 5, history: 1, freshness: 10）
- L0 分类关键词（arch 10 个, rule 11 个）

Provider 支持继承：compress 留空时自动继承 main 的配置。
API Key 自动从 `~/.hermes/.env` 或环境变量 `DEEPSEEK_API_KEY` 加载。

---

## 文件结构

```text
Memento/
├── setup.py                # 新用户向导
├── requirements.txt        # Python 依赖
├── .gitignore
├── ReadMe/
│   ├── DESIGN.md           # 本文件
│   ├── EVALUATION.md       # 评估报告
│   ├── USAGE.md            # 使用文档
│   └── CHANGELOG-v2-lifecycle.md  # v2 生命周期升级记录
│
├── Memory/                 # 全局记忆库
│   ├── config.yaml         # 核心配置
│   ├── abstracts.db        # [生成] L0 索引 + FTS5 + 向量 + decisions
│   ├── categories/         # 分类索引
│   └── schema/
│       ├── v1.sql
│       └── v2.sql          # 当前使用版本
│
├── Knowledge/              # 知识库
│   ├── abstracts.db        # [生成] L0 索引
│   ├── categories/         # 分类索引
│   ├── overviews/docs/     # [生成] L1 知识摘要
│   ├── storage/docs/       # [生成] L2 文档压缩
│   └── raw/                # [保留] 原始文档
│
├── Engine/                 # 引擎
│   ├── config/
│   │   └── lifecycle.yaml  # 生命周期参数配置
│   ├── migrations/
│   │   └── v2_lifecycle.sql # 生命周期数据库迁移
│   ├── src/
│   │   ├── client.py       # 统一 LLM/Embedding 客户端
│   │   └── retriever.py    # 检索引擎（FTS5 + 语义 + rerank + 生命周期感知排序）
│   ├── hooks/
│   │   ├── remember.py     # 记忆/知识注入入口（调用生命周期）
│   │   ├── remember_doc.py # 知识注入快捷入口
│   │   ├── lifecycle.py    # 生命周期管理器（语义去重/衰减/标签/引用/压缩/恢复）
│   │   ├── recall.py       # 检索入口
│   │   ├── inject.py       # 注入输出格式化
│   │   └── auto.py         # 状态查询 & 注入输出 & 生命周期命令
│   └── logs/               # [生成] 调试日志
│
└── Profile 专属 vault（~/.hermes/profiles/<name>/memory-vault/）：
    ├── abstracts.db        # [生成] 专属 L0
    ├── overviews/sessions/ # [生成] L1 记忆摘要
    └── storage/sessions/   # [生成] L2 会话压缩
```

---

## 状态生命周期

```
创建 → active
         ├── 二击保险第一次 → downgraded（检索仍可见）
         │     └── 二击保险第二次 → superseded（检索默认隐藏）
         ├── 超时衰减 → stale（检索仍可见但排序靠后）
         │     └── freshness=0 且 hit_count=0 → archived（需 recover 恢复）
         └── 语义合并 → 合并到旧条目，自身标记 superseded
```

---

## 手动指令列表

| 你说 | 我执行 |
|------|--------|
| "注入记忆" | `hooks/remember.py --source session` |
| "注入知识" | `hooks/remember_doc.py <文件路径>` |
| "查 XXX" | `recall.py --query 'XXX'` |
| "查知识库 XXX" | `recall.py --query 'XXX' --domain knowledge` |
| "查决策" | `recall.py --decisions` |
| "记忆里有什么" | `auto.py inject` |
| "状态" | `auto.py status` |
| "压缩" | `auto.py compact` |
| "恢复 --id N" | `auto.py recover --id N` |
| "恢复 --query Q" | `auto.py recover --query Q` |
| "列出归档" | `auto.py list-archived` |

---

## 已知约束

- 生命周期管理仅在记忆注入（session）时触发，知识注入（doc）不触发
- Embedding 生成失败时跳过语义去重步骤，条目直接作为新条目
- 语义去重作用于 profile 库，全局库通过同步映射更新
- 知识库没有独立的生命周期管理（无 freshness_score / 无状态转移）
- 排序公式的参数在 lifecycle.yaml 中配置，变更后需重启生效
- SQLite 写锁串行：多个注入同时执行会排队（单用户场景无问题）
