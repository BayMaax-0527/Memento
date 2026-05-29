<div align="center">
  <h1>Memento</h1>
  <p><b>轻量级 AI Agent 记忆持久化系统</b></p>
  <p>零外部服务依赖 · 中文原生 · 三级存储 · 生命周期管理</p>

  [![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
  [![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://python.org)
  [![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen)](https://github.com/BayMaax-0527/Memento/pulls)
  [![GitHub last commit](https://img.shields.io/github/last-commit/BayMaax-0527/Memento)](https://github.com/BayMaax-0527/Memento/commits/main)
</div>

---

> **🇨🇳 中文版** | [🇬🇧 English](./README.en.md)

---

## 一句话

你说"**注入记忆**"——对话被压缩、提取为结构化摘要、写入本地 SQLite。下次你说"**查 XXX**"就能找到。全部在你电脑上。

---

## 凭什么不一样？

| 你在别处找不到的 | Memento | 其他（Mem0 / Letta 等） |
|-----------------|---------|------------------------|
| **零外部服务依赖** | 纯 SQLite + 3 个 pip 包 | 需要向量数据库 / PostgreSQL / 独立服务器 |
| **中文检索原生支持** | FTS5 中文通配 + LIKE 兜底 + 中文分类词库 | 依赖 embedding 模型，无中文优化 |
| **三级存储** | L0 摘要 → L1 结构化 → L2 全文 | 单层 embedding |
| **生命周期管理** | 语义去重 → 超时衰减 → 自动 stale/archive → 可恢复 | 无 |
| **版本管理** | 同 topic 二击保险（先降级，再确认才覆盖） | 无 |
| **图谱推理** | Tag 重叠 + 2 跳 BFS 传递闭包 | 无 |
| **5 种检索方式** | FTS5 / LIKE / 语义 / 混合搜索 / LLM 重排序 | 通常只有语义搜索 |
| **知识库独立管理** | 记忆 + 知识双源隔离，互不干扰 | 无独立知识库 |

---

## 快速开始

### 环境要求

- **Python 3.10+**
- **模型后端**（任选其一，均可配置）：LM Studio（本地推荐） / DeepSeek / OpenAI 兼容 API

### 安装

```bash
git clone https://github.com/BayMaax-0527/Memento.git
cd Memento
pip install -r requirements.txt
python3 setup.py --auto          # 或 python3 setup.py（交互式）
```

### 首次使用

```bash
cd Engine
python3 src/retriever.py --health                      # 检查后端状态
python3 hooks/remember.py --source session              # 注入当前会话
python3 src/retriever.py --query "你想搜的"              # 检索
```

### 日常常用命令

```bash
# 注入
python3 hooks/remember.py --source session                          # 注入记忆
python3 hooks/remember_doc.py /path/to/doc.md                       # 注入知识文档

# 检索
python3 src/retriever.py --query "关键词"                            # 关键词搜索
python3 src/retriever.py --query "语义搜索" --semantic               # 语义搜索
python3 src/retriever.py --query "面霜" --domain knowledge --brand 珀莱雅  # 知识库结构化检索

# 维护
python3 hooks/auto.py status                                         # 查看统计
python3 hooks/auto.py compact                                        # 深度归档 + 健康报告
python3 hooks/auto.py recover --id N                                 # 恢复已归档条目
```

---

## 架构速览

```
你说"注入记忆"
  │
  ├─ ① DeepSeek 压缩 → L2 全文
  ├─ ② LM Studio 提取 → L0 摘要 + L1 结构化 + decisions
  ├─ ③ 写入 SQLite（FTS5 索引 + 2560 维向量）
  ├─ ④ 图谱推理（tag 重叠 + 2 跳 BFS）
  ├─ ⑤ 权重更新
  └─ ⑥ 生命周期管理（语义去重 → 衰减 → 标签判定 → 引用计数）
```

### 三库分离

| 库 | 位置 | 存什么 |
|---|------|--------|
| **全局记忆库** | `Memory/abstracts.db` | 跨 session 的通用决策和结论 |
| **知识库** | `Knowledge/abstracts.db` | 文档/报告/制度的结构化知识 |
| **Profile 专属库** | `profiles/<name>/memory-vault/` | 当前对话的 L0+L1+L2 完整记录 |

### 双模型分工

| | 压缩模型 | 提取模型 |
|---|---------|---------|
| 做什么 | 全量压缩会话/文档 | 从压缩结果提取结构化内容 |
| 典型配置 | DeepSeek（云 API，128k 上下文） | LM Studio 本地模型（免费） |
| 均可自由更换 | ✅ 任意 OpenAI 兼容 API | ✅ 同左 |

### 生命周期管理

```
创建 → active
         ├── 二击保险(第一次) → downgraded（检索可见）
         │     └── 二击保险(第二次) → superseded（冻结，默认隐藏）
         ├── 超时衰减 + 零引用 → stale（排序靠后）
         │     └── freshness=0 + 零命中 → archived（可恢复）
         └── 语义冲突 → 自动合并或 supersede
```

所有参数集中在 `Engine/config/lifecycle.yaml`，不硬编码。详见 [DESIGN.md](ReadMe/DESIGN.md)。

---

## Hermes 集成

如果你是 [Hermes Agent](https://hermesagent.org.cn) 用户，安装技能后直接说自然语言：

```bash
cp -r integrations/hermes/skills/* ~/.hermes/skills/
```

| 你说 | 效果 |
|------|------|
| "注入记忆" | 压缩当前会话 → 提取摘要 → 写入双库 |
| "注入知识" | 处理文档 → 写入知识库 |
| "查 XXX" | 检索记忆库 |
| "查知识库 XXX" | 检索知识库 |
| "压缩" | 深度归档 + 健康报告 |
| "恢复 --id N" | 恢复已归档条目 |

非 Hermes 用户直接运行上方「日常常用命令」中的脚本即可。

---

## 配置

核心配置在 `Memory/config.yaml`，`setup.py` 自动生成：

```yaml
models:
  main:              # 提取模型（本地，自由更换）
    provider: lmstudio
    model: qwen3.6-35b-a3b-mlx
  compress:          # 压缩模型（云端，自由更换）
    provider: deepseek
    model: deepseek-v4-flash
  embed:             # 向量模型
    provider: lmstudio
    model: qwen3-embedding-4b-mxfp8
    dimension: 2560
```

生命周期参数集中在 `Engine/config/lifecycle.yaml`。详见 [DESIGN.md](ReadMe/DESIGN.md)。

---

## License

Apache 2.0
