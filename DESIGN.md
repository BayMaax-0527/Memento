# Memento — 记忆 + 知识库系统

## 一句话

你说"注入记忆" → 压缩当前会话；你说"注入知识" → 压缩文档；然后统一检索。

---

## 核心原则

- **零额外依赖**：SQLite3 + Python requests + yaml，不需要新服务
- **手动触发**：没有自动钩子，全凭你说"注入记忆"、"注入知识"、"查XXX"、"查知识库XXX"
- **双模型分工**：DeepSeek 做压缩（付费可靠），LM Studio 做本机提取和向量化
- **Provider 抽象**：统一 `call_llm()` / `call_embedding()`，支持 LM Studio 本地 / DeepSeek / OpenAI 三种后端，config.yaml 中配置
- **全相对路径**：所有路径相对于 `Engine/` 目录自动解析，任意位置解压即用
- **三级存储**：L0 索引(FTS5) / L1 摘要(overviews) / L2 全文(sessions 或 docs)
- **双源隔离**：记忆用 `source_type='session'`，知识用 `source_type='doc'`，检索时互不干扰
- **独立运行**：不嵌入 Hermes plugin，升级互不影响

---

## 架构

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
  │   ├─ L0: 3-5 条一句话摘要 → abstracts.db，**双层去重**：
  │   │   ├─ 写 profile 库时：`soft_dedup()` 内容去重 + topic 版本覆盖
  │   │   ├─ 写全局库时：`soft_dedup()` 内容去重 + topic 版本覆盖
  │   │   └─ source_type 隔离：记忆和知识各自独立去重
  │   ├─ L1: Markdown 文件 → overviews/sessions/<日期>_sess_<id>.md
  │   │   含：核心主题、关键决策、踩坑记录、结论与待办、技术细节
  │   ├─ decisions：与已有决策语义对比，新增的 slug 写入 abstracts.db 的 decisions 表
  │   │   （全局唯一，slug UNIQUE 兜底，仅 session 注入时产生）
  │   └─ 决策匹配不做跨源对比：L0 软去重按 source_type 隔离
  │
  └─ ④ 输出 <memory-context> 注入结果
```

### 知识注入（"注入知识"）

```
你发文档 → 我转 .md → 我执行 remember_doc.py
  │
  ▼
hooks/remember_doc.py <路径>
  │  → hooks/remember.py --source doc --file <路径>
  │
  ├─ ① 读取 .md 文件（你转好的）
  │
  ├─ ② deepseek-v4-flash 压缩 → storage/docs/<日期>_<slug>.md (L2)
  │   "保留核心概念、技术原理、架构决策、数据流、关键步骤"
  │
  ├─ ③ LM Studio qwen3.6-35b-a3b-mlx 提取结构化内容
  │   ├─ L0: 3-5 条 → abstracts.db (source_type='doc')
  │   └─ L1: → overviews/docs/<日期>_doc_<slug>.md
  │
  └─ ④ 输出 <memory-context>（带分类标签）
```

### 检索

```
你说"查 XXX"            → 检索 source_type='session'（记忆）
你说"查知识库 XXX"      → 检索 source_type='doc'（知识库）
你说"查决策"            → 只输出 decisions
  │
  ▼
hooks/recall.py --query 'XXX' [--source session|doc]
  │
  ├─ FTS5 搜索 abstracts.db（英文 + 纯数字）
  ├─ 无结果 → LIKE 逐词兜底（中文）
  └─ 返回匹配的 L0 + 对应 L1 摘要前 600 字
```

---

## 三级存储

| 层级 | 存什么 | 存储 | 字符量 | 来源 |
|------|--------|------|--------|------|
| L0 | 一句话摘要 + source_type 标签 | SQLite FTS5 | ~20 tokens/条 | 记忆/知识共用 |
| L1 | 结构化摘要 | overviews/sessions/*.md 或 overviews/docs/*.md | ~200-250 tokens/篇 | 注入时最多 5 篇 |
| L2（会话）| 全量压缩对话 | storage/sessions/*.md | ~440 tokens/篇 | 记忆专用 |
| L2（知识）| 全量压缩文档 | storage/docs/*.md | 视文档大小 | 知识库专用 |
| decisions | 全局唯一决策/结论 | abstracts.db 的 decisions 表 | ~50 tokens 全量 | 记忆注入时产生 |

---

## 文件结构

```text
Memento/
├── setup.py                # ★ 新用户向导（python3 setup.py）
├── requirements.txt        # Python 依赖
├── .gitignore
├── ReadMe/
│   ├── DESIGN.md           # 本文件
│   ├── EVALUATION.md
│   └── USAGE.md
│
├── Engine/                 # 引擎（Finder 中隐藏）
│   ├── src/
│   │   ├── client.py       # ★ 统一 LLM/Embedding 客户端
│   │   │   ├─ call_llm()        → lmstudio / deepseek / openai
│   │   │   ├─ call_embedding()  → 统一向量生成
│   │   │   ├─ resolve_path()    → 相对路径解析
│   │   │   └─ resolve_provider()→ provider 继承 main
│   │   └── retriever.py    # 检索引擎（FTS5 + 语义 + rerank）
│   │
│   ├── hooks/
│   │   ├── remember.py     # ★ 记忆注入（--source session/doc）
│   │   ├── remember_doc.py # 知识注入快捷入口
│   │   ├── recall.py       # 检索入口
│   │   ├── inject.py       # 注入输出格式化
│   │   └── auto.py         # 状态查询 & 注入输出
│   │
│   └── logs/               # [生成] 调试日志
│
├── Memory/                 # ★ 全局记忆库（profiles 共享）
│   ├── config.yaml         # 核心配置（provider / 路径 / 检索参数）
│   ├── abstracts.db        # [生成] L0 索引 + FTS5 + 向量 + decisions
│   ├── categories/         # 分类索引
│   └── schema/             # 建表 SQL
│       ├── v2.sql          # 当前使用版本
│
└── Knowledge/              # 知识库（文档专用）
    ├── abstracts.db        # [生成] L0 索引
    ├── overviews/docs/     # [生成] L1 知识摘要
    ├── storage/docs/       # [生成] L2 文档压缩
    └── raw/                # [保留] 原始文档

Profile 专属 vault（~/.hermes/profiles/<name>/memory-vault/）：
    ├── abstracts.db        # [生成] 专属 L0
    ├── overviews/sessions/ # [生成] L1 记忆摘要
    └── storage/sessions/   # [生成] L2 会话压缩
```

---

## 双模型分工

| | deepseek-v4-flash | LM Studio qwen3.6-35b-a3b-mlx |
|---|---|---|
| 做什么 | 全量压缩（会话/文档） | 从压缩结果提取结构 |
| 上下文 | 128k | ~10k（只需处理压缩后的 L2） |
| 费用 | API 按量计费 | 本地 LM Studio 0 费用 |
| 可靠性 | 高，云 API | 中等 |
| 调用方式 | `call_llm(config_key="compress")` 经 `client.py` 统一路由 | `call_llm(config_key="main")` 经 `client.py` 统一路由 |

---

## 结论版本化（v3 新增）

### 场景

同一话题重复注入时（如"模型配置"发生了三次变更），旧结论与新结论并存可能产生矛盾。

### 方案：topic 版本覆盖

```sql
-- abstracts 新增字段
version INTEGER DEFAULT 1           -- 当前版本号
status TEXT DEFAULT 'active'         -- active / superseded
superseded_by_id INTEGER DEFAULT NULL  -- 被哪个新版本覆盖

-- memory_meta 新增字段
topic TEXT DEFAULT ''                -- 稳定主题键
```

**写入时：**
1. LLM 提取时生成 `【topic】`，格式：`{类别}/{project}/{decision_type}`
2. 写库前查同 profile + 同 topic 的 active 记录
3. 存在 → 旧记录标记 `status='superseded'`, `superseded_by_id=new_id`；新记录 `version = old_version + 1`
4. 不存在 → 正常 INSERT，version=1

**检索时：**
- 默认 `WHERE status != 'superseded'`，返回 active + downgraded（保险期内的旧版本仍可见）
- `--include-obsolete` 可查看全部版本链（含 superseded）

### 知识库二击保险（记忆库通用）

记忆和知识库都使用相同的二击保险机制。关系通过 `【关系】` 字段输出，LLM 判断新内容与已有同 topic 内容的关系：

| 关系 | 含义 | 旧知识处理 |
|---|---|---|
| `replaces` | 新替代旧 | 第一次 → `downgraded`（保险期），第二次 → `superseded` |
| `refines` | 新细化修正旧 | 同上二击保险 |
| `supplements` | 新补充旧 | 旧保留 `active`，两条共存 |
| `independent` | 不同维度无关 | 旧保留 `active` |

**二击保险原理：** LLM 一次误判只会将旧知识降级为 `downgraded`——检索时仍然可见，只是优先级降低。同 topic 出现第二次 `replaces`/`refines` 判断时，才真正标记 `superseded`。误判两次的概率极低。

- `supplements` 和 `independent` 不会触发任何覆盖
- 记忆库同样启用此保险（配置变更也可能存在不同角度的有效结论）

### 适用性

- 记忆注入（session）和知识注入（doc）均适用
- 知识库的 topic 由 `【话题】` 输出，格式：`{品牌}/{一级类目}/{二级类目}`（如 `珀莱雅/美妆护肤/面霜`），比记忆的 topic 更稳定——品牌名和类目是枚举值，LLM 不会写错
- topic 为空时不触发覆盖（兼容旧数据）
- 旧数据的 `status` 默认 `'active'`，不会被误覆盖

---

## 传递闭包推理（v3 新增）

### 场景

新注入的抽象 A 关联到了旧抽象 B，而 B 已经关联了 C。此时 A→C 应有隐含关联，无需等下次人工指定。

### 实现

```python
_infer_transitive_links(db, source_ids, max_hops=2)
```

注入完成后自动执行 BFS 遍历：

```
A 新注入 → 查 memory_links: A→B（1 跳）
         → 查 B→{C, D}（2 跳）
         → 自动创建 A→C (inferred), A→D (inferred)
```

**限制：**
- `max_hops=2`：2 跳以上的关联纯噪音，不推理
- `INSERT OR IGNORE`：已存在的链接不重复写
- `relation='inferred'`：区分手动关联和推理关联
- 去重 `seen` 集合：A→A 不自环，已关联的不重复

### 覆盖范围

| 注入类型 | DB | 触发 |
|---|---|---|
| 记忆（session） | 全局库 `memory_links` | `write_global_l0()` 之后 |
| 知识（doc） | 知识库 `memory_links` | `write_knowledge_l0_l1()` 之后 |

---

## 搜索结果重排序（v3 新增）

### 场景

FTS5 按 weight 排序、语义搜索按余弦得分排序，但两者都不能真正理解"用户想要什么"。用 LLM 对 top-N 结果做一次相关性重排。

### 用法

```bash
python3 src/retriever.py --query "模型选型" --global-db --rerank
python3 src/retriever.py --query "GPU 性能对比" --global-db --semantic --rerank
```

### 原理

1. 搜索结果取 top-N（默认 ~10 条）
2. 将查询 + 候选结果拼成 prompt 发给 LM Studio
3. LLM 输出排序号（`3,1,4,2,...`）
4. 按 LLM 的输出重新排列结果

### 注意

- rerank 额外调用一次 LM Studio（~1-2s），不影响检索速度
- LLM 只看到 abstract 摘要，不看完整 L1——避免 token 开销
- rerank 失败时自动回退到原始排序

---

## 手动指令列表

| 你说 | 我执行 |
|------|--------|
| "注入记忆" | `hooks/remember.py --source session` |
| "注入知识" | `hooks/remember_doc.py <文件路径>` |
| "查 XXX" | `recall.py --query 'XXX' --source session` |
| "查知识库 XXX" | `recall.py --query 'XXX' --source doc` |
| "查决策" | `recall.py --decisions` |
| "记忆里有什么" | `auto.py inject` |
| "状态" | `auto.py status` |

---

## 踩坑记录

- 自动钩子未执行导致注入承诺违约（Hermes 不暴露事件钩子）
- Python requests 调 Ollama 返回 502 → 改用 requests 直接调用 LM Studio
- FTS5 默认不分词中文 → LIKE 逐词兜底
- `rm -rf data/` 连数据库一起删了 → 只清数据不删文件
- 压缩脚本只发最近 80 条消息 → 改为全量发送
- `profile_vault_base` 未配时静默 fallback 到全局库 → 重复写入 L0 且路径错乱 → 改为报错退出 + `write_global_l0()` 补 `soft_dedup` 防止内容重复

---

## 建表语句

```sql
-- L0 索引（FTS5 + 主表 + 触发器自动同步）
CREATE VIRTUAL TABLE memories_fts USING fts5(...);
CREATE TABLE abstracts (
    id INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL,
    source_type TEXT NOT NULL DEFAULT 'session',  -- 'session' 或 'doc'
    abstract TEXT NOT NULL,
    ...
);
CREATE TRIGGER abstracts_ai AFTER INSERT ...

-- 全局决策
CREATE TABLE decisions (
    id INTEGER PRIMARY KEY,
    slug TEXT UNIQUE,
    ...
);
```

---

## 起点

**首次使用：**
```bash
cd Memento && python3 setup.py --auto   # 生成配置
cd Memento/Engine && python3 hooks/remember.py --source session
```

**注入记忆：**
```bash
cd Memento/Engine && python3 hooks/remember.py --source session
```

**注入知识（先由我转 .md）：**
```bash
cd Memento/Engine && python3 hooks/remember_doc.py /path/to/file.md
```

**检索记忆：**
```bash
cd Memento/Engine && python3 hooks/recall.py --query '关键词'
```

**检索知识：**
```bash
cd Memento/Engine && python3 hooks/recall.py --query '关键词' --source doc
```

**查状态：**
```bash
cd Memento/Engine && python3 hooks/auto.py status
# → {"memory_l0": 63, "profile_l0": 4, "knowledge_l0": 8, ...}
```


---

<!-- 以下章节从 DESIGN-v2.md 合并 -->

## 统一 Schema

**三表设计：主表共用 + 扩展表按域拆分，全部在同一个 abstracts.db 中。**

```sql
-- 主表（所有域共用，FTS5 索引全文）
CREATE TABLE abstracts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type TEXT NOT NULL,     -- 'memory' / 'knowledge'
    abstract TEXT NOT NULL,         -- L0 摘要
    category TEXT,                  -- 大类（项目开发/薪酬分析/...）
    tags TEXT,                      -- 逗号分隔，如 "抗皱,保湿,敏感肌适用"
    weight INTEGER DEFAULT 100,    -- 权重（初始 100）
    global_hit_ratio REAL DEFAULT 0.5, -- 长期跨 profile 命中率
    session_count INTEGER DEFAULT 0,   -- 经历的会话周期数
    last_accessed_at TEXT,         -- 最后命中时间
    created_at TEXT DEFAULT (datetime('now','localtime')),
    storage_path TEXT               -- 指向 L1/L2 文件
);

CREATE VIRTUAL TABLE abstracts_fts USING fts5(
    abstract, category, tags,
    content='abstracts', content_rowid='id'
);

-- 记忆域扩展（source_type='memory' 时有值）
CREATE TABLE memory_meta (
    abstract_id INTEGER PRIMARY KEY,
    profile TEXT,                    -- default / work / private
    profile_tag TEXT,                -- 工作 / 个人
    project TEXT,                    -- hr-efficiency / payroll / xiaohongshu
    project_type TEXT,               -- 系统开发 / 工具配置 / 数据分析 / 内容创作
    decision_type TEXT,              -- 决策 / 踩坑 / 经验 / 待办
    importance TEXT,                 -- high / medium / low
    FOREIGN KEY (abstract_id) REFERENCES abstracts(id) ON DELETE CASCADE
);

-- 知识域扩展（source_type='knowledge' 时有值）
CREATE TABLE knowledge_meta (
    abstract_id INTEGER PRIMARY KEY,
    brand_name TEXT,                 -- 珀莱雅 / 欧莱雅（具体品牌）
    brand_tier TEXT,                 -- 国货美妆 / 国际大牌（品牌归类）
    category_l1 TEXT,                -- 美妆护肤 / 食品饮料（一级类目）
    category_l2 TEXT,                -- 面霜 / 乳制品（细分 / 归类）
    target_audience TEXT,            -- 敏感肌 / 减脂人群（具体人群）
    audience_tag TEXT,               -- 成分党 / 职场白领（人群归类）
    FOREIGN KEY (abstract_id) REFERENCES abstracts(id) ON DELETE CASCADE
);

-- 记忆关联表
CREATE TABLE memory_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL,
    target_abstract_id INTEGER NOT NULL,
    relation TEXT DEFAULT 'related',   -- related / duplicate / superseded / tag_overlap
    created_at TEXT DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (source_id) REFERENCES abstracts(id) ON DELETE CASCADE,
    FOREIGN KEY (target_abstract_id) REFERENCES abstracts(id) ON DELETE CASCADE
);

-- 全局决策表
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

### 两层定位规则（通用设计模式）

每个维度假两层：**具体值 + 归类值**。

```
brand_name = "珀莱雅"      → 具体值，搜"珀莱雅"精准命中
brand_tier = "国货美妆"    → 归类值，搜"国货品牌"也能命中

category_l2 = "面霜"       → 具体值
category_l2 = "乳制品"     → 归类值（酸奶/奶片一类）

target_audience = "敏感肌"  → 具体人群
audience_tag = "成分党"     → 人群共性标签
```

---


## 三个子系统的关系

| 维度 | 全局记忆库 | 全局知识库 | 专属 profile vault |
|------|-----------|-----------|-------------------|
| 来源 | 对话产生的决策/踩坑/经验 | 你给我的文档/报告/制度 | 当前 profile 对话的记忆 |
| 存放 | `Memory/abstracts.db`（source_type='memory'） | `Knowledge/abstracts.db`（source_type='knowledge'） + `raw/` + `storage/docs/` + `overviews/docs/` | `~/.hermes/profiles/<name>/memory-vault/` |
| 注入方式 | 自动双写（先专属后全局） | "注入知识" | 注入时自动同步 |
| 检索手动 | "查 XXX" / "查全域 XXX" | "查知识库 XXX" | 默认检索范围 |
| 检索自动 | 背景自动召回 | 背景自动召回 | 无（归全局） |
| 跨 profile | 全局索引可见 | 全局，所有 profile 都能查 | 不跨 |

---


## 权重降级（跨 profile 长期统计）

不追踪单次调用命中率（太短视），而是追踪**跨 profile、多会话的长期命中情况**。

### 核心优先级

**命中调权 > 时间衰减。** 一条记忆只要仍在被使用，它的权重就不会因为时间流逝而下降。

### 实现方式

每条记忆在全局库中记录：
- `weight` — 初始 100（排序用）
- `global_hit_ratio` — 长期命中率，0~1
- `session_count` — 经历的会话周期数
- `last_accessed_at` — 最后命中时间

会话周期：你说**"注入记忆"到下一次"注入记忆"之间为一个周期。**

### 更新逻辑

```
每个周期结束时（你说"注入记忆"时）：
  本周期内被自动召回命中过 → hit_count +1
  本周期内未命中 → 不做操作（不主动降权）

session_count >= 3 开始计算 weight：
  命中率 ≥ 0.7                 → weight = 80~100
  命中率 0.3 ~ 0.7             → weight = 50~80
  命中率 < 0.3 AND 5+ 周期     → weight 每周期 -5
  weight 下限为 5

时间衰减只在命中降权后辅助加速（weight ≤ 30 且 30 天未命中时生效）
```

---


## Profile 自动初始化规则

你说"新建一个 profile 叫 work" → 自动创建 `~/.hermes/profiles/work/memory-vault/`：

```
创建 memory-vault/
  初始化 abstracts.db（v2 schema：abstracts + FTS5 + memory_meta + memory_links）
  创建 overviews/sessions/
  创建 storage/sessions/
```

---


## 新域扩展规则

| 场景 | 做法 |
|------|------|
| 新话题，memory_meta 字段够用 | 不新建域，新 `category` 就好 |
| 新话题，字段结构完全不同 | 我提要不要建新域，你确认后我建扩展表 |
| 你觉得应该建新域 | 直接说"建个 XX 域" |

当前只有 **memory 域** 和 **knowledge 域**。

---


## 文件清单

```
Memento/
├── ReadMe/
│   ├── DESIGN.md           # 设计文档
│   ├── EVALUATION.md
│   └── USAGE.md
├── Memory/                 # 全局记忆库
│   ├── config.yaml
│   ├── schema/v2.sql
│   ├── abstracts.db
│   └── categories/
│
├── Knowledge/              # 知识库
│   ├── abstracts.db
│   ├── categories/
│   ├── raw/
│   ├── storage/docs/       L2
│   └── overviews/docs/     L1
│
├── Engine/                 # 引擎
│   ├── src/
│   │   ├── client.py
│   │   └── retriever.py
│   ├── hooks/
│   │   ├── remember.py
│   │   ├── remember_doc.py
│   │   ├── recall.py
│   │   ├── inject.py
│   │   └── auto.py
│   └── logs/
│
└── profiles/<name>/memory-vault/      专属 vault
    ├── abstracts.db
    ├── storage/sessions/              L2
    └── overviews/sessions/            L1
```


