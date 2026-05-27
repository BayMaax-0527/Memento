<div align="center">
  <h1>Memento</h1>
  <p><b>轻量级 AI Agent 记忆持久化系统</b></p>
  <p>把你的对话决策、踩坑经验、知识文档变成可检索的长期记忆。</p>

  [![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
  [![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://python.org)
  [![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen)](https://github.com/BayMaax-0527/Memento/pulls)
  [![GitHub last commit](https://img.shields.io/github/last-commit/BayMaax-0527/Memento)](https://github.com/BayMaax-0527/Memento/commits/main)
</div>

---

## 目录

- [为什么需要 Memento？](#为什么需要-memento)
- [与其他方案对比](#与其他方案对比)
- [快速开始](#快速开始)
- [架构与设计](#架构与设计)
- [使用指南](#使用指南)
- [配置说明](#配置说明)
- [Hermes 集成](#hermes-集成)
- [故障排查](#故障排查)
- [Schema](#schema)
- [License](#license)

---

## 为什么需要 Memento？

AI Agent 每轮对话都是全新开始——它不记得昨天的结论、上周的踩坑、上个月的架构决策。

现有方案要么太重（Docker + PostgreSQL），要么太贵（SaaS 每轮计费），要么数据不在你手上。

**Memento 的答案是：**

```
你说"注入记忆" → DeepSeek 压缩对话 → 
LM Studio 提取结构化摘要 → 
写入本地 SQLite（FTS5 全文索引 + 语义向量 + 图谱关联）

下次你问"查 XXX"就能找到。全部在你电脑上，零外部服务。
```

## 与其他方案对比

| 特性 | Memento | Mem0 | Zep | LangChain Memory |
|------|---------|------|-----|-----------------|
| 部署 | pip install + SQLite | Docker + 向量库 | Docker + Postgres | 框架内嵌 |
| 离线运行 | ✅ 全本地 | ❌ 需 API | ❌ 需 Docker | ❌ 依赖框架 |
| 依赖数 | 0（SQLite 内置） | 需外部向量库 | Postgres + pgvector | 多种 |
| 去重机制 | ✅ 双层（内容+版本+二击保险） | ❌ | ❌ | ❌ |
| 版本管理 | ✅ 二击保险（downgrade→supersede） | ❌ | ❌ | ❌ |
| 知识库 | ✅ 独立，与记忆隔离 | ❌ | ❌ | ❌ |
| 图谱推理 | ✅ 2 跳 BFS 传递闭包 | ❌ | ✅ Graph RAG | ❌ |
| API 费用 | 零或几分/次 | 每轮计费 | 每轮计费 | 视 provider |
| 存储层级 | L0（摘要）/ L1（结构化）/ L2（全文） | 单层 embedding | 单层 + graph | 单层 |
| 手动/自动 | 手动触发（你说才写） | 自动 | 自动 | 自动 |

## 快速开始

### 环境要求

- **Python 3.10+**
- **SQLite 3**（Python 自带）
- **DeepSeek API Key**（可选，用于压缩；不配置则纯本地运行）
- **LM Studio**（可选，用于本地模型提取和向量化）

### 安装

```bash
# 1. 克隆
git clone https://github.com/BayMaax-0527/Memento.git
cd Memento

# 2. 安装依赖
pip install -r requirements.txt

# 3. 运行配置向导（交互式）
python3 setup.py

# 或一键默认配置（LM Studio localhost:1234）
python3 setup.py --auto
```

### 首次运行

```bash
# 1. 确保 LM Studio 已启动（localhost:1234）

# 2. 进入引擎目录
cd Engine

# 3. 健康检查
python3 src/retriever.py --health

# 4. 注入记忆（自动读取最新会话或通过 --file 指定 JSON）
python3 hooks/remember.py --source session

# 5. 检索
python3 src/retriever.py --query "你想搜的"
```

---

## 架构与设计

### 目录结构

```
Memento/
├── setup.py                 配置向导
├── requirements.txt         Python 依赖
├── Engine/                  引擎代码
│   ├── src/
│   │   ├── client.py        统一 LLM/Embedding 客户端
│   │   └── retriever.py     检索引擎（FTS5 + 语义 + rerank）
│   └── hooks/
│       ├── remember.py      记忆注入主入口
│       ├── remember_doc.py  知识注入快捷入口
│       ├── recall.py        检索入口
│       ├── inject.py        注入输出格式化
│       └── auto.py          状态查询 & 自动注入
├── Memory/                  全局记忆库
│   ├── config.yaml          核心配置
│   ├── abstracts.db         数据库（L0 + FTS5 + 向量 + 决策）
│   ├── categories/          分类索引
│   └── schema/              建表 SQL
├── Knowledge/               知识库
│   ├── abstracts.db         L0 索引
│   ├── raw/                 原始文档
│   ├── storage/docs/        L2 文档压缩
│   └── overviews/docs/      L1 知识摘要
└── integrations/
    └── hermes/              Hermes Agent 集成
```

Profile 专属库（如果使用 Hermes）：

```
~/.hermes/profiles/<name>/memory-vault/
├── abstracts.db          L0 索引
├── storage/sessions/     L2 会话压缩
└── overviews/sessions/   L1 记忆摘要
```

### 三级存储

| 层级 | 存什么 | 存储方式 | 大小 |
|------|--------|----------|------|
| **L0** | 一句话摘要 | SQLite FTS5 全文索引 | ~20 tokens/条 |
| **L1** | 结构化摘要（核心主题/决策/踩坑/待办/技术细节） | Markdown 文件 | ~200-250 tokens/篇 |
| **L2** | 全量压缩对话或文档 | Markdown 文件 | ~440 tokens/会话 |
| **decisions** | 全局唯一决策/踩坑记录 | SQLite decisions 表 | ~50 tokens 全量 |

### 双模型分工

| | 压缩模型 | 提取模型 |
|---|---------|---------|
| 做什么 | 全量压缩（会话/文档） | 从压缩结果提取结构 |
| 默认 | DeepSeek v4 Flash（API） | LM Studio 本地模型 |
| 上下文 | 128k | ~10k（只需处理 L2） |
| 费用 | 按量计费 | 本地免费 |
| 可替换 | 任意 OpenAI 兼容 API | 同左 |

### 注入流程

```
输入（对话 JSON / 文档 .md）
  │
  ├─ ① DeepSeek 压缩 → L2
  │
  ├─ ② LM Studio 提取：
  │   ├─ L0（3-5 条一句话摘要，双层去重）
  │   ├─ L1（结构化 Markdown）
  │   ├─ decisions（语义去重，slug 唯一）
  │   └── memory_meta / knowledge_meta（结构化字段）
  │
  └─ ③ 写入：
      ├─ L0 → abstracts.db（FTS5 索引 + 2560 维向量）
      ├─ L1 → overviews/
      ├─ L2 → storage/
      └─ memory_links → tag 重叠 + 传递闭包（2 跳）
```

### 结论版本化（二击保险）

同 topic 内容重复注入时，不会被立即覆盖——这是系统的核心保险机制：

```
第 1 次同 topic 注入（replaces / refines）
  → 旧记录标记 downgraded（保险期，检索时仍然可见）
  → 新记录 version+1

第 2 次同 topic 注入再次确认（replaces / refines）
  → 旧记录标记 superseded（默认隐藏，--include-obsolete 可见）
  → 新记录 version+2

supplements / independent → 不触发覆盖，旧记录保留 active
```

| 关系 | 含义 | 旧知识处理 |
|------|------|-----------|
| `replaces` | 新替代旧 | 第一次 → downgraded，第二次 → superseded |
| `refines` | 新细化/修正旧 | 同上二击保险 |
| `supplements` | 补充旧知识 | 保留 active，两条共存 |
| `independent` | 不同维度无关 | 保留 active，不关联 |

### 传递闭包推理

注入时自动执行 2 跳 BFS 图谱推理：

```
新注入 A → 查 memory_links: A→B（1 跳）
         → 查 B→{C, D}（2 跳）
         → 自动创建 A→C (inferred), A→D (inferred)
```

### 搜索结果重排序

```bash
python3 src/retriever.py --query "XXX" --rerank
```

取 top-N 结果 → LM Studio 按相关性重排 → 输出。

---

## 使用指南

### 注入记忆

```bash
cd Engine

# 注入当前 Hermes 会话
python3 hooks/remember.py --source session

# 从指定 JSON 文件注入（通用模式，不依赖 Hermes）
python3 hooks/remember.py --source session --file /path/to/conversation.json

# 指定 session 目录
python3 hooks/remember.py --session-dir /path/to/sessions/

# 指定 profile
python3 hooks/remember.py --profile work

# 手动指定 slug
python3 hooks/remember.py --slug my-custom-slug
```

### 注入知识

```bash
# 注入知识文档（Markdown 格式）
python3 hooks/remember_doc.py /path/to/document.md

# 带自定义 slug
python3 hooks/remember_doc.py /path/to/document.md --slug my-knowledge
```

### 检索

```bash
# 关键词搜索（默认 FTS5，全局库）
python3 src/retriever.py --query "模型选型"

# 指定检索数量
python3 src/retriever.py --query "方案" --limit 3

# 仅搜知识库
python3 src/retriever.py --query "劳动法" --domain knowledge

# 语义搜索（不需要关键词匹配）
python3 src/retriever.py --query "性能对比" --domain knowledge --semantic

# 混合搜索（FTS5 优先 + 语义补充）
python3 src/retriever.py --query "GPU" --global-db --hybrid

# LLM 重排序
python3 src/retriever.py --query "模型选型" --global-db --rerank

# 查看版本链（含已 superseded 的旧版本）
python3 src/retriever.py --query "模型" --global-db --include-obsolete

# 结构化过滤（知识库专用）
python3 src/retriever.py --query "面霜" --domain knowledge --brand 珀莱雅 --l2 面霜

# 查看全部决策
python3 src/retriever.py --decisions
```

### 系统维护

```bash
# 健康检查
python3 src/retriever.py --health

# 查看状态统计
python3 hooks/auto.py status

# 手动注入（当前会话）
python3 hooks/remember.py

# 自动召回（在当前会话中生效）
# 你说"开启自动召回" → 每轮首步执行 auto_inject()
# 你说"关闭自动召回" → 停止
```

---

## 配置说明

核心配置在 `Memory/config.yaml`，`engine/setup.py` 会自动生成。手动配置示例：

```yaml
models:
  main:
    provider: lmstudio        # lmstudio / deepseek / openai
    model: qwen3.6-35b-a3b-mlx
    base_url: http://localhost:1234/v1
    api_key: ''
  compress:
    provider: deepseek         # 压缩模型，建议云 API
    model: deepseek-v4-flash
    base_url: https://api.deepseek.com/v1
    api_key: '${DEEPSEEK_API_KEY}'   # 从 .env 读取
  embed:
    provider: lmstudio
    model: qwen3-embedding-4b-mxfp8
    dimension: 2560
    # 也支持 OpenAI 兼容 API：
    # provider: openai
    # model: text-embedding-3-small
    # dimension: 1536
paths:
  global_vault: ../Memory
  knowledge_base: ../Knowledge
  logs_dir: logs
  profile_vault_base: profiles    # 或 ~/.hermes/profiles（Hermes 用户）
retrieval:
  auto_inject_limit: 5
```

DeepSeek API Key 配置方式（二选一）：

```bash
# 方式 1：.env 文件（推荐）
echo 'DEEPSEEK_API_KEY=sk-your-key' > Memento/.env

# 方式 2：环境变量
export DEEPSEEK_API_KEY=sk-your-key
```

---

## Hermes 集成

如果你是 [Hermes Agent](https://hermesagent.org.cn) 用户，安装配套技能后可以直接说自然语言指令：

```bash
# 安装技能文件
cp -r integrations/hermes/skills/* ~/.hermes/skills/
```

新会话中支持以下指令：

| 你说 | 效果 |
|------|------|
| "注入记忆" | 压缩当前会话 → 提取摘要 → 写入双库 |
| "注入知识" | 处理文档 → 写入知识库 |
| "查 XXX" | 检索记忆库 |
| "查知识库 XXX" | 检索知识库 |
| "查决策" | 列出最近决策 |
| "语义查 XXX" | 向量语义搜索 |
| "开启/关闭自动召回" | 开关自动检索 |

非 Hermes 用户按上面「使用指南」中的命令手动执行即可。

---

## 故障排查

| 问题 | 原因 | 解决 |
|------|------|------|
| "LM Studio 不可达" | LM Studio 未启动或 Server 未开启 | 打开 LM Studio → Developer → Start Server |
| 搜索结果为空 | 还没注入过，或关键词不匹配 | 先执行注入命令，或用 `--semantic` 语义搜索 |
| 注入失败 | API key 未配置或模型未加载 | 检查 `.env` 和 LM Studio 状态 |
| 语义搜索为 0 | embedding 模型未加载 | 确认 LM Studio 加载了 embedding 模型 |
| 注入超时 | 模型推理太慢 | 确认使用 MoE 模型（如 35B 但仅 3B 激活），非全量模型 |
| 重复 L0 | `profile_vault_base` 配置错误 | 检查 config.yaml 中的路径配置 |

---

## Schema

```sql
-- 主表（所有域共用，FTS5 索引全文）
CREATE TABLE abstracts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type TEXT NOT NULL,      -- 'memory' / 'knowledge'
    abstract TEXT NOT NULL,          -- L0 摘要
    category TEXT,
    tags TEXT,
    weight INTEGER DEFAULT 100,
    version INTEGER DEFAULT 1,      -- topic 版本号
    status TEXT DEFAULT 'active',    -- active / downgraded / superseded
    storage_path TEXT,               -- 指向 L1/L2 文件
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

-- 记忆域扩展
CREATE TABLE memory_meta (
    abstract_id INTEGER PRIMARY KEY,
    profile TEXT,
    project TEXT,
    project_type TEXT,
    decision_type TEXT,              -- 决策 / 踩坑 / 经验 / 待办
    importance TEXT,                 -- high / medium / low
    topic TEXT DEFAULT ''            -- 版本覆盖主题键
);

-- 知识域扩展
CREATE TABLE knowledge_meta (
    abstract_id INTEGER PRIMARY KEY,
    brand_name TEXT,
    brand_tier TEXT,
    category_l1 TEXT,
    category_l2 TEXT,
    target_audience TEXT,
    audience_tag TEXT
);

-- 记忆关联表（图谱推理）
CREATE TABLE memory_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL,
    target_abstract_id INTEGER NOT NULL,
    relation TEXT DEFAULT 'related', -- related / superseded / tag_overlap / inferred
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

-- 全局决策表
CREATE TABLE decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE,
    content TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

-- FTS5 全文索引（触发器自动同步）
CREATE VIRTUAL TABLE abstracts_fts USING fts5(
    abstract, category, tags,
    content='abstracts', content_rowid='id'
);
```

完整建表语句见 `Memory/schema/v2.sql`。

---

## License

Apache 2.0
