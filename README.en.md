<div align="center">
  <h1>Memento</h1>
  <p><b>A lightweight, local-first memory persistence system for AI agents.</b></p>
  <p>Zero external dependencies · Native Chinese support · Three-tier storage · Lifecycle management</p>

  [![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
  [![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://python.org)
  [![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen)](https://github.com/BayMaax-0527/Memento/pulls)
  [![GitHub last commit](https://img.shields.io/github/last-commit/BayMaax-0527/Memento)](https://github.com/BayMaax-0527/Memento/commits/main)
</div>

---

> **🇬🇧 English** | [🇨🇳 中文版](./README.md)

---

## One-liner

Say "**inject memory**" → your conversation gets compressed, structured, and stored in local SQLite. Later you say "**find XXX**" and it's there. All on your machine.

---

## Why Memento, not Mem0 / Letta / LangChain?

| Feature | Memento | Others (Mem0, Letta, etc.) |
|---------|--------|---------------------------|
| **Zero external services** | Pure SQLite + zero deps beyond Python stdlib | Need vector DB / PostgreSQL / standalone server |
| **Chinese-native search** | FTS5 prefix wildcards + LIKE fallback + Chinese keyword lists | Rely on embedding models, no Chinese optimization |
| **Three-tier storage** | L0 abstract → L1 structured → L2 full text | Single-layer embedding |
| **Lifecycle management** | Semantic dedup → decay → auto stale/archive → recovery | None |
| **Versioning** | Two-click insurance (downgrade first, supersede on second confirm) | None |
| **Graph reasoning** | Tag overlap + 2-hop BFS transitive closure | None |
| **5 retrieval modes** | FTS5 / LIKE / semantic / hybrid / LLM rerank | Usually just semantic search |
| **Independent knowledge base** | Memory + Knowledge dual-source isolation | No dedicated knowledge base |

---

## Quick Start

### Requirements

- **Python 3.10+**
- **A model backend**: LM Studio (local, recommended) / DeepSeek / any OpenAI-compatible API

### Install

```bash
git clone https://github.com/BayMaax-0527/Memento.git
cd Memento
pip install -r requirements.txt
python3 setup.py --auto          # or python3 setup.py (interactive)
```

### First Run

```bash
cd Engine
python3 src/retriever.py --health                      # health check
python3 hooks/remember.py --source session              # inject current session
python3 src/retriever.py --query "your search term"     # search
```

### Common Commands

```bash
# Injection
python3 hooks/remember.py --source session                          # inject memory
python3 hooks/remember_doc.py /path/to/doc.md                       # inject knowledge

# Retrieval
python3 src/retriever.py --query "keyword"                          # FTS5 search
python3 src/retriever.py --query "semantic search" --semantic       # semantic search
python3 src/retriever.py --query "topic" --domain knowledge --brand BrandName  # structured filter

# Maintenance
python3 hooks/auto.py status                                         # statistics
python3 hooks/auto.py compact                                        # archive + health report
python3 hooks/auto.py recover --id N                                 # recover archived entry
```

---

## Architecture Overview

```
You say "inject memory"
  │
  ├─ ① DeepSeek compresses → L2 (full text)
  ├─ ② LM Studio extracts → L0 abstracts + L1 structured + decisions
  ├─ ③ Write to SQLite (FTS5 index + 2560-dim vectors)
  ├─ ④ Graph inference (tag overlap + 2-hop BFS)
  ├─ ⑤ Weight update
  └─ ⑥ Lifecycle management (dedup → decay → label → reference count)
```

### Three Vaults

| Vault | Location | Content |
|-------|----------|---------|
| **Global Memory** | `Memory/abstracts.db` | Cross-session decisions & conclusions |
| **Knowledge** | `Knowledge/abstracts.db` | Structured knowledge from documents |
| **Profile** | `profiles/<name>/memory-vault/` | Per-profile L0+L1+L2 |

### Lifecycle

```
Created → active
            ├── First similar injection → downgraded (still visible)
            │     └── Second confirmation → superseded (frozen, hidden)
            ├── Decay + zero references → stale (ranked lower)
            │     └── freshness=0 + never referenced → archived (recoverable)
            └── Semantic conflict → auto-merge or supersede
```

All parameters in `Engine/config/lifecycle.yaml`. See [DESIGN.md](ReadMe/DESIGN.md).

---

## Hermes Integration

If you use [Hermes Agent](https://hermesagent.org.cn):

```bash
cp -r integrations/hermes/skills/* ~/.hermes/skills/
```

| You say | Effect |
|---------|--------|
| "inject memory" | Compress → extract → write to dual vaults |
| "inject knowledge" | Process document → write to knowledge base |
| "find XXX" | Search memory vault |
| "find knowledge XXX" | Search knowledge vault |
| "compact" | Deep archive + health report |
| "recover --id N" | Recover archived entry |

Non-Hermes users run the commands above directly.

---

## Configuration

Main config at `Memory/config.yaml` (auto-generated by `setup.py`):

```yaml
models:
  main:
    provider: lmstudio        # or deepseek / openai
    model: qwen3.6-35b-a3b-mlx
    base_url: http://localhost:1234/v1
  compress:
    provider: deepseek
    model: deepseek-v4-flash
  embed:
    provider: lmstudio
    model: qwen3-embedding-4b-mxfp8
    dimension: 2560
```

Lifecycle parameters in `Engine/config/lifecycle.yaml`. Full documentation at [DESIGN.md](ReadMe/DESIGN.md).

---

## License

Apache 2.0
