# 记忆 + 知识库系统 v3 — 使用文档

> 适用版本：v3 (2026-05) | 依赖：Python 3.10+, requests, pyyaml, numpy
>
> **新用户先运行:** `python3 setup.py` 或 `python3 setup.py --auto`

---

## 一、快速开始

```bash
# 0. 首次配置（生成 config.yaml）
python3 setup.py

# 1. 确保 LM Studio 已启动（localhost:1234）

# 2. 进入引擎目录
cd Engine

# 3. 健康检查
python3 src/retriever.py --health

# 4. 注入记忆（自动读取最新会话）
python3 hooks/remember.py --source session

# 5. 注入知识
python3 hooks/remember_doc.py /path/to/document.md
```

---

## 二、核心概念

### 三级存储

| 层级 | 内容 | 存储位置 | 大小 |
|---|---|---|---|
| **L0** | 一句话摘要（<20 字） | `abstracts.db` 的 `abstracts` 表 | ~20 tokens/条 |
| **L1** | 结构化摘要（~200-250 tokens） | `overviews/` 下的 .md 文件 | 注入时最多 5 篇 |
| **L2** | 全文压缩 | `storage/` 下的 .md 文件 | ~440 tokens/会话 |

### 三库架构

```text
Memento/
├── setup.py                # 向导配置脚本
├── requirements.txt        # Python 依赖
├── .gitignore
├── DESIGN.md               # 设计文档
├── EVALUATION.md            # 评估报告
├── USAGE.md                # 使用文档
├── Memory/                 # 全局记忆库
│   ├── config.yaml         # 🌟 核心配置（provider / 路径）
│   ├── abstracts.db        # L0 索引 + FTS5 + 向量
│   ├── overviews/sessions/ # L1 记忆摘要
│   ├── storage/            # L2 全文
│   └── categories/         # 分类
├── Knowledge/              # 知识库
│   ├── abstracts.db        # L0 索引 + FTS5 + 向量
│   └── ...
└── Engine/                 # 引擎
    ├── src/
    │   ├── client.py       # 🌟 统一 LLM/Embedding 客户端
    │   └── retriever.py    # 检索 + 自动注入
    └── hooks/
        ├── remember.py     # 记忆注入
        ├── remember_doc.py # 知识注入
        ├── inject.py       # 快捷注入
        ├── recall.py       # 快捷检索
        └── auto.py         # 状态 & 注入输出
```

Profile 专属库 (~/.hermes/profiles/<name>/memory-vault/)
  ├── abstracts.db          # L0 索引
  ├── overviews/sessions/   # L1 记忆摘要
  └── storage/sessions/     # L2 会话压缩
```

### 结论版本化

| 状态 | 含义 | 检索是否可见 |
|---|---|---|
| `active` | 当前有效 | ✅ 显示 |
| `downgraded` | 保险期（第一次标记覆盖） | ✅ 显示 |
| `superseded` | 已确认被替代（第二次确认后） | ❌ 默认隐藏，`--include-obsolete` 可见 |

### 知识关系类型

| 关系 | 含义 | 旧知识处理 |
|---|---|---|
| `replaces` | 新替代旧 | 第一次 → downgraded，第二次 → superseded |
| `refines` | 新细化/修正旧 | 同上二击保险 |
| `supplements` | 补充旧知识 | 保留 active，两条共存 |
| `independent` | 不同维度，无关 | 保留 active，不关联 |

---

## 三、注入记忆

```bash
cd ~/workspace/.memory-vault-engine

# 注入当前会话
python3 hooks/remember.py --source session
# 或简写（默认就是 session）
python3 hooks/remember.py

# 注入指定 profile
python3 hooks/remember.py --profile default

# 手动指定 slug
python3 hooks/remember.py --slug my-custom-slug
```

### 注入流程

```
读取最新 Hermes 会话
  → DeepSeek 压缩 → L2 文件
  → LM Studio 提取结构化字段 + L0 + L1 + 决策
  → 写入专属库（L0 + memory_meta + L1 + L2）
  → 同步全局库（L0 + memory_meta）
  → 生成语义向量（2560 维）
  → 写入决策（decisions 表）
  → 关联解析 + Tag 重叠 + 传递闭包推理
  → 权重更新
```

---

## 四、注入知识

```bash
# 注入知识文档
python3 hooks/remember_doc.py /path/to/document.md

# 带自定义 slug
python3 hooks/remember_doc.py /path/to/document.md --slug my-knowledge-slug
```

### 知识文档格式

Markdown 格式，纯文本即可。系统会自动：
1. 读取文件内容
2. DeepSeek 压缩
3. LM Studio 提取品牌/类目/人群/话题/L0/L1
4. 写入知识库（含 knowledge_meta）
5. 生成向量
6. 传递闭包推理

---

## 五、检索

### 基础用法

```bash
cd ~/workspace/.memory-vault-engine

# 关键词搜索（默认 FTS5，全局库）
python3 src/retriever.py --query "方案"

# 指定数量
python3 src/retriever.py --query "模型" --limit 3

# 仅搜记忆
python3 src/retriever.py --query "决策" --domain memory

# 仅搜知识库
python3 src/retriever.py --query "劳动法" --domain knowledge
```

### 语义搜索

```bash
# 纯语义搜索（不需要关键词匹配）
python3 src/retriever.py --query "性能对比" --domain knowledge --semantic

# 混合搜索（FTS5 优先 + 语义补充）
python3 src/retriever.py --query "GPU" --global-db --hybrid
```

### LLM 重排序

```bash
# 任何搜索模式后加 --rerank
python3 src/retriever.py --query "模型选型" --global-db --rerank
python3 src/retriever.py --query "GPU 性能" --global-db --semantic --rerank
```

### 高级选项

```bash
# 查看版本链（含 superseded 的旧版本）
python3 src/retriever.py --query "模型" --global-db --include-obsolete

# 结构化过滤
python3 src/retriever.py --query "面霜" --domain knowledge --brand 珀莱雅 --l2 面霜

# 查看全部决策
python3 src/retriever.py --decisions

# 自动注入（生成 memory-context 摘要）
python3 src/retriever.py --auto-inject
```

### 快捷检索

```
你说：
  "查 XXX"               → 检索记忆
  "查知识库 XXX"         → 检索知识库
  "查决策"               → 输出所有决策
  "状态"                 → 健康检查
```

---

## 六、系统维护

```bash
# 健康检查
python3 src/retriever.py --health

# 查看状态统计
python3 hooks/auto.py status

# 注入当前会话（手动触发）
python3 hooks/remember.py

# 开启/关闭自动召回（在当前会话生效）
你说"开启自动召回" → 每轮首步执行 auto_inject()
你说"关闭自动召回" → 停止
```

---

## 七、配置文件

`~/.hermes/config.yaml`（Hermes 主配置）：

```yaml
auxiliary:
  vision:
    provider: custom
    model: qwen3.6-35b-a3b-mlx
    base_url: http://localhost:1234/v1

delegation:
  model: qwen3.6-35b-a3b-mlx
  provider: custom
  base_url: http://localhost:1234/v1
```

`~/.hermes/memory-vault/global/config.yaml`（记忆系统配置）：

```yaml
lmstudio:
  model: qwen3.6-35b-a3b-mlx
  base_url: http://localhost:1234/v1

embedding:
  model: qwen3-embedding-4b-mxfp8
  base_url: http://localhost:1234/v1
  dimension: 2560

retrieval:
  auto_inject_limit: 5
```

---

## 八、文件结构

```
workspace/
├── .global-memory-vault/          # 全局记忆库
│   ├── config.yaml
│   ├── DESIGN.md / DESIGN-v2.md / EVALUATION.md / USAGE.md
│   ├── abstracts.db
│   ├── categories/
│   ├── overviews/
│   └── scripts/
│
├── .global-knowledge-base/        # 知识库
│   ├── abstracts.db
│   ├── categories/
│   ├── overviews/
│   ├── raw/
│   └── storage/
│
└── .memory-vault-engine/          # 引擎代码
    ├── hooks/
    │   ├── remember.py            # 主注入入口
    │   ├── remember_doc.py        # 知识注入快捷入口
    │   ├── inject.py              # 注入输出
    │   ├── recall.py              # 检索入口
    │   └── auto.py                # 状态查询
    └── src/
        └── retriever.py           # 检索引擎
```

---

## 九、故障排查

| 问题 | 原因 | 解决 |
|---|---|---|
| "LM Studio 不可达" | LM Studio 未启动或 Server 未开启 | 打开 LM Studio → Developer → Start Server |
| 搜索结果为空 | 还没注入过，或 topic 不匹配 | 先执行 `python3 hooks/remember.py` 注入 |
| 注入失败 | DeepSeek API key 未配置 | 在 `~/.hermes/.env` 设置 `DEEPSEEK_API_KEY` |
| 语义搜索为 0 | embedding 模型未加载 | 确认 LM Studio 已加载 `qwen3-embedding-4b-mxfp8` |
| 注入超时 | 模型推理太慢 | 确认 LM Studio 加载的是 `qwen3.6-35b-a3b-mlx`（MoE 3B 激活）而非全量模型 |
