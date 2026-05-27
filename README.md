# Memento

> **轻量级 AI Agent 记忆持久化系统。** 把你的对话决策、踩坑记录、知识文档变成可检索的长期记忆。

[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://python.org)

---

## 一句话

你说"注入记忆" → 压缩对话 → 提取结构化摘要 → 存入可检索的本地数据库。下次你问"查 XXX"就能找到。

## 快速开始

```bash
# 1. 克隆
git clone https://github.com/yourusername/Memento.git
cd Memento

# 2. 安装依赖
pip install -r requirements.txt

# 3. 运行配置向导
python3 setup.py

# 4. 注入记忆（需要先准备好对话 JSON 或直接运行）
cd Engine
python3 hooks/remember.py --source session

# 5. 检索
python3 src/retriever.py --query "你想搜的"
```

## 架构

```
Memento/
├── Engine/        # 引擎代码
│   ├── src/       # 检索引擎 + 统一 API 客户端
│   └── hooks/     # 注入管道
├── Memory/        # 全局记忆库（L0 索引 + FTS5 + 向量 + 决策）
├── Knowledge/     # 知识库（文档专用）
├── setup.py       # 配置向导
└── ReadMe/        # 文档
```

### 工作流程

```
输入（对话 JSON / 文档 .md）
  │
  ├─ DeepSeek 压缩 → L2（全文压缩）
  │
  ├─ 本地模型提取 → L0（一句话摘要）
  │                 → L1（结构化摘要）
  │                 → decisions（决策记录）
  │
  └─ 写入 SQLite（FTS5 全文索引 + 语义向量 + 图谱关联）
```

## 核心特性

| 特性 | 说明 |
|------|------|
| **三级存储** | L0 摘要 / L1 结构化 / L2 全文 |
| **双源隔离** | 记忆（会话）和知识（文档）互不干扰 |
| **双层去重** | 内容相似度去重 + topic 版本覆盖 |
| **二击保险** | 第一次注入降级，第二次确认才真正覆盖 |
| **图谱推理** | tag 重叠 + 显式关联 + 2 跳传递闭包 |
| **检索方式** | FTS5 + 语义搜索 + LLM rerank |
| **零外部服务** | 可选：纯本地（LM Studio）或混合（DeepSeek API） |

## 支持的后端

| 提供商 | 用途 | 模式 |
|--------|------|------|
| LM Studio | 本地主模型 + embedding | localhost:1234 |
| DeepSeek | 云 API 压缩 | `DEEPSEEK_API_KEY` |
| OpenAI 兼容 | 任意替代 | `base_url` + `api_key` |

## 项目状态

自用项目，但欢迎 Issue 和 PR。详见 [CHANGELOG](CHANGELOG.md)。

## License

Apache 2.0
