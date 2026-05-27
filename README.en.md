<div align="center">
  <h1>Memento</h1>
  <p><b>A lightweight, local-first memory persistence system for AI agents.</b></p>
  <p>Turn your conversation decisions, lessons learned, and knowledge documents into searchable long-term memory.</p>

  [![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
  [![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://python.org)
  [![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen)](https://github.com/BayMaax-0527/Memento/pulls)
  [![GitHub last commit](https://img.shields.io/github/last-commit/BayMaax-0527/Memento)](https://github.com/BayMaax-0527/Memento/commits/main)
</div>

---

> **🇬🇧 English** | [🇨🇳 中文版](./README.md)

---

## Table of Contents

- [Why Memento?](#why-memento)
- [Comparison](#comparison)
- [Quick Start](#quick-start)
- [Architecture & Design](#architecture--design)
- [Usage Guide](#usage-guide)
- [Configuration](#configuration)
- [Hermes Integration](#hermes-integration)
- [Troubleshooting](#troubleshooting)
- [Schema](#schema)
- [License](#license)

---

## Why Memento?

Every AI agent conversation starts from scratch — it doesn't remember yesterday's decisions, last week's pitfalls, or last month's architecture choices.

Existing solutions are either too heavy (Docker + PostgreSQL), too expensive (SaaS per-turn billing), or don't give you data ownership.

**Memento's approach:**

```
Say "inject memory" → DeepSeek compresses the conversation →
Local LM Studio extracts structured summaries →
Writes to local SQLite (FTS5 full-text index + semantic vectors + graph links)

Next time you ask, just search. Everything runs locally. Zero external services.
```

## Comparison

| Feature | Memento | Mem0 | Zep | LangChain Memory |
|---------|---------|------|-----|-----------------|
| Setup | pip install + SQLite | Docker + vector DB | Docker + Postgres | Framework embedded |
| Offline | ✅ Fully local | ❌ API required | ❌ Docker required | ❌ Framework bound |
| Dependencies | 0 (SQLite built-in) | External vector DB | Postgres + pgvector | Multiple |
| Deduplication | ✅ Dual-layer (content + topic versioning + two-click insurance) | ❌ | ❌ | ❌ |
| Versioning | ✅ Two-click insurance (downgrade → supersede) | ❌ | ❌ | ❌ |
| Knowledge base | ✅ Independent, isolated from memory | ❌ | ❌ | ❌ |
| Graph reasoning | ✅ 2-hop BFS transitive closure | ❌ | ✅ Graph RAG | ❌ |
| API cost | Free or pennies per run | Per-turn billing | Per-turn billing | Varies by provider |
| Storage tiers | L0 (abstract) / L1 (structured) / L2 (full text) | Single embedding layer | Single layer + graph | Single layer |
| Trigger model | Manual (you say when to write) | Automatic | Automatic | Automatic |

## Quick Start

### Requirements

- **Python 3.10+**
- **SQLite 3** (built into Python)
- **DeepSeek API Key** (optional, for cloud compression)
- **LM Studio** (optional, for local model extraction and embeddings)

### Installation

```bash
# 1. Clone
git clone https://github.com/BayMaax-0527/Memento.git
cd Memento

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run the setup wizard (interactive)
python3 setup.py

# Or one-command default (LM Studio localhost:1234)
python3 setup.py --auto
```

### First Run

```bash
# 1. Make sure LM Studio is running (localhost:1234)

# 2. Enter the engine directory
cd Engine

# 3. Health check
python3 src/retriever.py --health

# 4. Inject memory (reads latest session or specify --file)
python3 hooks/remember.py --source session

# 5. Search
python3 src/retriever.py --query "what you're looking for"
```

---

## Architecture & Design

### Directory Structure

```
Memento/
├── setup.py                 Setup wizard
├── requirements.txt         Python dependencies
├── Engine/                  Engine code
│   ├── src/
│   │   ├── client.py        Unified LLM/Embedding client
│   │   └── retriever.py     Search engine (FTS5 + semantic + rerank)
│   └── hooks/
│       ├── remember.py      Memory injection entry point
│       ├── remember_doc.py  Knowledge injection shortcut
│       ├── recall.py        Search entry point
│       ├── inject.py        Injection output formatting
│       └── auto.py          Status check & auto-injection
├── Memory/                  Global memory vault
│   ├── config.yaml          Core configuration
│   ├── abstracts.db         Database (L0 + FTS5 + vectors + decisions)
│   ├── categories/          Category index
│   └── schema/              Table schemas
├── Knowledge/               Knowledge base
│   ├── abstracts.db         L0 index
│   ├── raw/                 Original documents
│   ├── storage/docs/        L2 compressed docs
│   └── overviews/docs/      L1 knowledge summaries
└── integrations/
    └── hermes/              Hermes Agent integration
```

Profile vault (if using Hermes):

```
~/.hermes/profiles/<name>/memory-vault/
├── abstracts.db          L0 index
├── storage/sessions/     L2 compressed conversations
└── overviews/sessions/   L1 memory summaries
```

### Three-Tier Storage

| Tier | Content | Storage | Size |
|------|---------|---------|------|
| **L0** | One-line abstract | SQLite FTS5 full-text index | ~20 tokens/item |
| **L1** | Structured summary (topic/decisions/pitfalls) | Markdown files | ~200-250 tokens/item |
| **L2** | Full compressed conversation or document | Markdown files | ~440 tokens/session |
| **decisions** | Global unique decisions/findings | SQLite decisions table | ~50 tokens total |

### Dual-Model Pipeline

| | Compression Model | Extraction Model |
|---|-----------------|-----------------|
| Role | Full compression (sessions/docs) | Structure extraction from compressed output |
| Default | DeepSeek v4 Flash (API) | LM Studio local model |
| Context | 128k | ~10k (only processes L2) |
| Cost | Pay-per-use | Free (local) |
| Replaceable | Any OpenAI-compatible API | Same |

### Injection Flow

```
Input (conversation JSON / document .md)
  │
  ├─ ① DeepSeek compresses → L2
  │
  ├─ ② LM Studio extracts:
  │   ├─ L0 (3-5 one-line abstracts, dual-layer dedup)
  │   ├─ L1 (structured Markdown)
  │   ├─ decisions (semantic dedup, unique slug)
  │   └── memory_meta / knowledge_meta (structured fields)
  │
  └─ ③ Writes:
      ├─ L0 → abstracts.db (FTS5 index + 2560-dim vectors)
      ├─ L1 → overviews/
      ├─ L2 → storage/
      └─ memory_links → tag overlap + transitive closure (2 hops)
```

### Versioning (Two-Click Insurance)

When content with the same topic is injected again, it's not immediately overwritten:

```
1st same-topic injection (replaces / refines)
  → Old record marked downgraded (insurance period, still visible in search)
  → New record version+1

2nd same-topic injection confirmed again (replaces / refines)
  → Old record marked superseded (hidden by default, --include-obsolete shows it)
  → New record version+2

supplements / independent → No overwrite, old record stays active
```

| Relation | Meaning | Old Record |
|----------|---------|------------|
| `replaces` | New replaces old | 1st → downgraded, 2nd → superseded |
| `refines` | New refines/corrects old | Same two-click insurance |
| `supplements` | Supplements old knowledge | Stays active, both coexist |
| `independent` | Different dimension, unrelated | Stays active, no link |

### Transitive Closure Reasoning

Automatically runs 2-hop BFS graph reasoning on injection:

```
New A inserted → query memory_links: A→B (hop 1)
               → query B→{C, D} (hop 2)
               → auto-create A→C (inferred), A→D (inferred)
```

### Search Result Reranking

```bash
python3 src/retriever.py --query "query" --rerank
```

Takes top-N results → LM Studio re-sorts by relevance → outputs.

---

## Usage Guide

### Inject Memory

```bash
cd Engine

# Inject current Hermes session
python3 hooks/remember.py --source session

# Inject from a JSON file (standalone mode, no Hermes needed)
python3 hooks/remember.py --source session --file /path/to/conversation.json

# Specify a custom session directory
python3 hooks/remember.py --session-dir /path/to/sessions/

# Specify a profile
python3 hooks/remember.py --profile work

# Custom slug
python3 hooks/remember.py --slug my-custom-slug
```

### Inject Knowledge

```bash
# Inject a knowledge document (Markdown)
python3 hooks/remember_doc.py /path/to/document.md

# With custom slug
python3 hooks/remember_doc.py /path/to/document.md --slug my-knowledge
```

### Search

```bash
# Keyword search (default FTS5, global vault)
python3 src/retriever.py --query "model selection"

# Limit results
python3 src/retriever.py --query "plan" --limit 3

# Search knowledge base only
python3 src/retriever.py --query "labor law" --domain knowledge

# Semantic search (no keyword matching needed)
python3 src/retriever.py --query "performance comparison" --domain knowledge --semantic

# Hybrid search (FTS5 first + semantic supplement)
python3 src/retriever.py --query "GPU" --global-db --hybrid

# LLM reranking
python3 src/retriever.py --query "model selection" --global-db --rerank

# Show version chain (including superseded records)
python3 src/retriever.py --query "model" --global-db --include-obsolete

# Structured filter (knowledge base specific)
python3 src/retriever.py --query "cream" --domain knowledge --brand "Laneige" --l2 "moisturizer"

# View all decisions
python3 src/retriever.py --decisions
```

### System Maintenance

```bash
# Health check
python3 src/retriever.py --health

# Status statistics
python3 hooks/auto.py status

# Manual injection (current session)
python3 hooks/remember.py

# Auto-recall (takes effect in current session):
# Say "enable auto-recall" → runs auto_inject() before each turn
# Say "disable auto-recall" → stops
```

---

## Configuration

Core configuration is in `Memory/config.yaml`. The `setup.py` wizard generates it automatically.

```yaml
models:
  main:
    provider: lmstudio        # lmstudio / deepseek / openai
    model: qwen3.6-35b-a3b-mlx
    base_url: http://localhost:1234/v1
    api_key: ''
  compress:
    provider: deepseek         # compression model, cloud API recommended
    model: deepseek-v4-flash
    base_url: https://api.deepseek.com/v1
    api_key: '${DEEPSEEK_API_KEY}'   # read from .env
  embed:
    provider: lmstudio
    model: qwen3-embedding-4b-mxfp8
    dimension: 2560
    # Also supports OpenAI-compatible APIs:
    # provider: openai
    # model: text-embedding-3-small
    # dimension: 1536
paths:
  global_vault: ../Memory
  knowledge_base: ../Knowledge
  logs_dir: logs
  profile_vault_base: profiles    # or ~/.hermes/profiles (Hermes users)
retrieval:
  auto_inject_limit: 5
```

DeepSeek API Key setup (choose one):

```bash
# Option 1: .env file (recommended)
echo 'DEEPSEEK_API_KEY=*** > Memento/.env

# Option 2: Environment variable
export DEEPSEEK_API_KEY=sk-your...
```

---

## Hermes Integration

If you use [Hermes Agent](https://hermesagent.org.cn), install the companion skill to use natural language commands:

```bash
cp -r integrations/hermes/skills/* ~/.hermes/skills/
```

In a new session, these commands become available:

| You Say | Effect |
|---------|--------|
| "inject memory" | Compress current session → extract → write to dual vaults |
| "inject knowledge" | Process document → write to knowledge base |
| "search XXX" | Search memory vault |
| "search knowledge XXX" | Search knowledge base |
| "show decisions" | List recent decisions |
| "semantic search XXX" | Vector semantic search |
| "enable/disable auto-recall" | Toggle automatic retrieval |

Non-Hermes users can execute the commands from the Usage Guide section directly.

---

## Troubleshooting

| Problem | Cause | Solution |
|---------|-------|----------|
| "LM Studio unreachable" | LM Studio not running or server not started | Open LM Studio → Developer → Start Server |
| Empty search results | Nothing injected yet, or keyword mismatch | Run injection first, or use `--semantic` search |
| Injection fails | API key not configured or model not loaded | Check `.env` and LM Studio status |
| Semantic search returns 0 | Embedding model not loaded | Make sure LM Studio has the embedding model loaded |
| Injection timeout | Model too slow | Ensure you're using a MoE model (e.g. 35B with 3B active), not a dense model |
| Duplicate L0 entries | Wrong `profile_vault_base` config | Check the path in config.yaml |

---

## Schema

```sql
-- Main table (shared across domains, FTS5-indexed)
CREATE TABLE abstracts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type TEXT NOT NULL,      -- 'memory' / 'knowledge'
    abstract TEXT NOT NULL,          -- L0 abstract
    category TEXT,
    tags TEXT,
    weight INTEGER DEFAULT 100,
    version INTEGER DEFAULT 1,      -- topic version number
    status TEXT DEFAULT 'active',    -- active / downgraded / superseded
    storage_path TEXT,               -- points to L1/L2 files
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

-- Memory domain extensions
CREATE TABLE memory_meta (
    abstract_id INTEGER PRIMARY KEY,
    profile TEXT,
    project TEXT,
    project_type TEXT,
    decision_type TEXT,              -- decision / pitfall / experience / todo
    importance TEXT,                 -- high / medium / low
    topic TEXT DEFAULT ''            -- versioning topic key
);

-- Knowledge domain extensions
CREATE TABLE knowledge_meta (
    abstract_id INTEGER PRIMARY KEY,
    brand_name TEXT,
    brand_tier TEXT,
    category_l1 TEXT,
    category_l2 TEXT,
    target_audience TEXT,
    audience_tag TEXT
);

-- Memory links table (graph reasoning)
CREATE TABLE memory_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL,
    target_abstract_id INTEGER NOT NULL,
    relation TEXT DEFAULT 'related', -- related / superseded / tag_overlap / inferred
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

-- Global decisions table
CREATE TABLE decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE,
    content TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

-- FTS5 full-text index (auto-synced via triggers)
CREATE VIRTUAL TABLE abstracts_fts USING fts5(
    abstract, category, tags,
    content='abstracts', content_rowid='id'
);
```

Full DDL at `Memory/schema/v2.sql`.

---

## License

Apache 2.0
