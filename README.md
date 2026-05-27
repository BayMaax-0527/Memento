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

## 为什么需要 Memento？

AI Agent 每轮对话都是全新开始——它不记得昨天的结论、上周的踩坑、上个月的架构决策。

现有方案要么太重（Docker + PostgreSQL），要么太贵（SaaS 每轮计费），要么数据不在你手上。

**Memento 的答案是：你说"注入记忆" → 系统压缩对话 → 提取结构化摘要 → 存到本地 SQLite。下次问就能搜到。全部在你电脑上，零外部服务。**

## 快速开始

```bash
# 1. 克隆
git clone https://github.com/BayMaax-0527/Memento.git
cd Memento

# 2. 安装依赖
pip install -r requirements.txt

# 3. 配置（运行交互式向导）
python3 setup.py

# 4. 注入记忆
cd Engine
python3 hooks/remember.py --source session

# 5. 检索
python3 src/retriever.py --query "你想搜的"
```

### 对比其他方案

| | Memento | Mem0 | Zep | LangChain Memory |
|---|---|---|---|---|
| **部署** | pip install + SQLite | Docker + 向量库 | Docker + Postgres | 框架内嵌 |
| **离线** | ✅ 全本地 | ❌ 需 API | ❌ 需 Docker | ❌ 依赖框架 |
| **数据库** | 零（Python 自带 SQLite） | 需外部向量库 | Postgres + pgvector | 多种 |
| **去重** | ✅ 双层（内容+版本+二击保险） | ❌ | ❌ | ❌ |
| **版本管理** | ✅ 有（二击保险） | ❌ | ❌ | ❌ |
| **知识库** | ✅ 独立 | ❌ | ❌ | ❌ |
| **API 费用** | 零或几分/次 | 每轮计费 | 每轮计费 | 视 provider |

### 如果你是 Hermes 用户

Memento 最初为 [Hermes Agent](https://hermesagent.org.cn) 开发。安装后装技能即可用自然语言调用：

```bash
cp -r integrations/hermes/skills/* ~/.hermes/skills/
```

新会话中说 **"注入记忆"**、**"查 XXX"**、**"查知识库 XXX"** 即可。

## 架构

```
Memento/
├── Engine/        引擎代码
│   ├── src/       检索引擎 + 统一 API 客户端
│   └── hooks/     注入管道
├── Memory/        全局记忆库（L0 索引 + FTS5 + 向量 + 决策）
├── Knowledge/     知识库（文档专用）
├── setup.py       配置向导
├── DESIGN.md      设计文档
└── USAGE.md       详细使用说明
```

## 核心特性

- **三级存储**：L0 摘要 / L1 结构化 / L2 全文，逐级压缩
- **双源隔离**：记忆（对话）和知识（文档）独立存储，检索互不干扰
- **双层去重**：内容相似度去重 + topic 版本覆盖
- **二击保险**：同 topic 第一次注入降级，第二次确认才覆盖——LLM 一次误判不会丢数据
- **图谱推理**：tag 重叠 + 显式关联 + 2 跳 BFS 传递闭包
- **检索方式**：FTS5（快） + 语义搜索（准） + LLM rerank（精）
- **零外部依赖**：全本地运行，可选 DeepSeek API 做压缩

## 使用场景

- **AI 开发助手**：跨会话记住项目决策、技术选型、踩坑记录
- **个人知识管理**：把阅读笔记、文档摘要变成可检索的知识库
- **团队协作**：通过统一知识库共享经验，减少重复踩坑

## 支持的后端

| 提供商 | 用途 | 模式 |
|--------|------|------|
| LM Studio | 本地主模型 + embedding | localhost:1234 |
| DeepSeek | 云 API 压缩 | `DEEPSEEK_API_KEY` |
| OpenAI 兼容 | 任意替代 | `base_url` + `api_key` |

## License

Apache 2.0
