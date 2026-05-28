#!/usr/bin/env python3
"""
retriever.py — v2 记忆/知识检索引擎

支持双库（全局 + 专属）、结构化字段过滤、权重排序。

用法:
    retriever.py --query <关键词>                            手动检索（默认专属库）
    retriever.py --query <关键词> --global                    全局库检索
    retriever.py --query <关键词> --domain memory             仅记忆
    retriever.py --query <关键词> --domain knowledge          仅知识
    retriever.py --query <关键词> --brand 珀莱雅 --l2 面霜    知识库结构化过滤
    retriever.py --auto-inject                                自动注入
    retriever.py --health                                     健康检查
"""

import json, logging, re, sqlite3, subprocess, sys
from pathlib import Path
import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from client import call_llm, call_embedding, resolve_path
CONFIG = yaml.safe_load((ROOT.parent / "Memory" / "config.yaml").read_text())
LOG_DIR = Path(resolve_path(CONFIG["paths"]["logs_dir"]))


def setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s %(message)s",
        handlers=[
            logging.FileHandler(str(LOG_DIR / "memory-vault.log"), encoding="utf-8"),
            logging.StreamHandler(),
        ],
        force=True,
    )


logger = logging.getLogger("retriever")

GLOBAL_VAULT = Path(CONFIG.get("paths", {}).get("global_vault", str(Path.home() / "workspace" / "Memento" / "Memory")))
KNOWLEDGE_VAULT = Path(CONFIG.get("paths", {}).get("knowledge_base", str(Path.home() / "workspace" / "Memento" / "Knowledge")))
PROFILE_BASE = Path(CONFIG.get("paths", {}).get("profile_vault_base", str(Path.home() / ".hermes" / "profiles")))


def get_db(global_db: bool = False, profile: str = "default", knowledge_db: bool = False) -> sqlite3.Connection | None:
    """获取数据库连接。global_db=True 查全局记忆库，knowledge_db=True 查知识库，否则查专属库。"""
    if knowledge_db:
        db_path = KNOWLEDGE_VAULT / "abstracts.db"
    elif global_db:
        db_path = GLOBAL_VAULT / "abstracts.db"
    else:
        db_path = PROFILE_BASE / profile / "memory-vault" / "abstracts.db"
    if not db_path.exists():
        return None
    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row
    return db


def _build_fts_query(query: str) -> str:
    """将用户查询转为 FTS5 MATCH 表达式。
    
    中文 Token 自动加前缀通配符（*），解决 FTS5 unicode61 分词器
    将连续中文字符串视为一个 Token 导致无法精确匹配的问题。
    """
    tokens = re.findall(r'[\u4e00-\u9fff]+|[a-zA-Z0-9]+', query)
    tokens = [t for t in tokens if len(t) >= 2]
    # 中文 Token 加前缀通配符：FTS5 支持 token 前缀匹配
    tokens = [
        t + "*" if re.match(r'^[\u4e00-\u9fff]', t) else t
        for t in tokens
    ]
    return " AND ".join(tokens[:5])


def search(query: str, limit: int = 5, domain: str = None,
           global_db: bool = False, profile: str = "default",
           knowledge_db: bool = False, **filters) -> list[dict]:
    """带结构化字段过滤的检索。

    domain: 'memory'/'knowledge'/None
    filters: brand, category_l2, target_audience, decision_type, project 等
    """
    db = get_db(global_db=global_db, profile=profile, knowledge_db=knowledge_db)
    if db is None:
        return []

    tokens = re.findall(r'[\u4e00-\u9fff]+|[a-zA-Z0-9]+', query)
    fts_query = _build_fts_query(query)
    rows = []

    # 确定 JOIN 的表和字段
    if domain == "knowledge":
        if not knowledge_db:
            knowledge_db = True  # auto-route to knowledge DB
            # 路由改变后重新连接
            db.close()
            db = get_db(global_db=global_db, profile=profile, knowledge_db=knowledge_db)
            if db is None:
                return []
        join_clause = "LEFT JOIN knowledge_meta km ON a.id = km.abstract_id"
        # 结构化过滤
        where_clauses = ["a.source_type = 'knowledge'"]
        params = []
        if filters.get("brand"):
            where_clauses.append("km.brand_name = ?")
            params.append(filters["brand"])
        if filters.get("category_l1"):
            where_clauses.append("km.category_l1 = ?")
            params.append(filters["category_l1"])
        if filters.get("category_l2"):
            where_clauses.append("km.category_l2 = ?")
            params.append(filters["category_l2"])
        if filters.get("target_audience"):
            where_clauses.append("km.target_audience LIKE ?")
            params.append(f"%{filters['target_audience']}%")
        if filters.get("brand_tier"):
            where_clauses.append("km.brand_tier = ?")
            params.append(filters["brand_tier"])
        if filters.get("audience_tag"):
            where_clauses.append("km.audience_tag LIKE ?")
            params.append(f"%{filters['audience_tag']}%")
    elif domain == "memory":
        join_clause = "LEFT JOIN memory_meta mm ON a.id = mm.abstract_id"
        where_clauses = ["a.source_type = 'memory'"]
        params = []
        if filters.get("decision_type"):
            where_clauses.append("mm.decision_type = ?")
            params.append(filters["decision_type"])
        if filters.get("project"):
            where_clauses.append("mm.project = ?")
            params.append(filters["project"])
        if filters.get("profile_tag"):
            where_clauses.append("mm.profile_tag = ?")
            params.append(filters["profile_tag"])
        if filters.get("project_type"):
            where_clauses.append("mm.project_type = ?")
            params.append(filters["project_type"])
    else:
        join_clause = ""
        where_clauses = []
        params = []

    where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

    try:
        sql = (
            "SELECT a.id, a.abstract, a.source_type, a.category, a.tags, "
            "a.weight, a.version, a.status, a.created_at, a.storage_path "
            f"FROM abstracts a {join_clause} "
            f"WHERE a.id IN (SELECT rowid FROM abstracts_fts WHERE abstracts_fts MATCH ?) "
            f"AND a.status IN ('active', 'downgraded', 'stale') AND {where_sql} "
            "ORDER BY "
            "((COALESCE(a.sliding_hit_count,0) * 5) + (COALESCE(a.hit_count,0) * 1) + "
            "(COALESCE(a.freshness_score,1.0) * 10 * CAST(a.weight AS REAL) / 100.0)) DESC "
            "LIMIT ?"
        )
        rows = db.execute(sql, [fts_query] + params + [limit]).fetchall()
    except sqlite3.OperationalError as e:
        logger.debug("FTS5 search failed: %s", e)

    # FTS5 失败或无结果 → LIKE 兜底
    if not rows and tokens:
        like_rows = []
        seen = set()
        for tok in tokens:
            if len(tok) < 2:
                continue
            try:
                sql = (
                    "SELECT a.id, a.abstract, a.source_type, a.category, a.tags, "
                    "a.weight, a.version, a.status, a.created_at, a.storage_path "
                    f"FROM abstracts a {join_clause} "
                    f"WHERE (a.abstract LIKE ? OR a.tags LIKE ?) AND a.status IN ('active', 'downgraded', 'stale') AND {where_sql} "
                    "ORDER BY a.weight DESC, a.created_at DESC "
                    "LIMIT ?"
                )
                r2 = db.execute(sql, [f"%{tok}%", f"%{tok}%"] + params + [limit]).fetchall()
                for r in r2:
                    if r["id"] not in seen:
                        like_rows.append(dict(r))
                        seen.add(r["id"])
            except sqlite3.OperationalError:
                pass
        rows = like_rows[:limit]

    return [dict(r) for r in rows]


def semantic_search(query: str, limit: int = 5, domain: str = None,
                    global_db: bool = False, profile: str = "default",
                    knowledge_db: bool = False) -> list[dict]:
    """基于余弦相似度的语义搜索。与 search() 返回格式一致。"""
    # 生成查询向量
    try:
        qvec = call_embedding(query, config_key="embed")
    except Exception as e:
        logger.warning("语义搜索: query embedding 失败: %s", e)
        return []

    db = get_db(global_db=global_db, profile=profile, knowledge_db=knowledge_db)
    if db is None:
        return []

    # 读取所有存储的向量
    import struct
    stored = db.execute(
        "SELECT e.source_id, e.source_type, e.dimension, e.vector, "
        "a.abstract, a.storage_path "
        "FROM embeddings e "
        "JOIN abstracts a ON e.source_id = a.id AND e.source_type = a.source_type "
        "WHERE a.status IN ('active', 'downgraded', 'stale')").fetchall()
    db.close()

    # cosine similarity（numpy 向量化）
    import numpy as np
    if not stored:
        db.close()
        return []
    dim = stored[0]["dimension"]
    qarr = np.array(qvec, dtype=np.float32)
    qnorm = np.linalg.norm(qarr)
    if qnorm == 0:
        db.close()
        return []
    # 批量解包所有向量
    sarr = np.array([
        list(struct.unpack(f"{r['dimension']}f", r["vector"]))
        for r in stored
    ], dtype=np.float32)
    snorms = np.linalg.norm(sarr, axis=1)
    scores = (sarr @ qarr) / (snorms * qnorm + 1e-10)
    top_n = min(len(scores), limit)
    top_idx = np.argsort(scores)[-top_n:][::-1]
    results = []
    for i in top_idx:
        row = stored[i]
        results.append({
            "id": row["source_id"],
            "abstract": row["abstract"],
            "source_type": row["source_type"],
            "storage_path": row["storage_path"],
            "score": round(float(scores[i]), 4),
        })
    return results


def rerank(query: str, results: list[dict], top_n: int = 5) -> list[dict]:
    """用 LLM 对搜索结果重排序。"""
    if not results:
        return results
    candidates = "\n".join(
        f"{i+1}. {r['abstract']}" for i, r in enumerate(results)
    )
    prompt = f"""查询: {query}

候选结果:
{candidates}

请根据与查询的相关性从高到低重新排列编号，只输出排序后的序号（逗号分隔），不输出其他内容。
示例: 3,1,4,2,5"""
    try:
        raw = call_llm(
            [{"role": "user", "content": prompt}],
            config_key="main",
            timeout=30,
            max_tokens=100,
            temperature=0,
        )
        indices = [int(x.strip()) - 1 for x in raw.split(",") if x.strip().isdigit()]
        reranked = [results[i] for i in indices if 0 <= i < len(results)]
        return reranked[:top_n]
    except Exception as e:
        logger.debug("Rerank 失败: %s", e)
        return results[:top_n]


def hybrid_search(query: str, limit: int = 5, domain: str = None,
                  global_db: bool = False, profile: str = "default",
                  knowledge_db: bool = False, **filters) -> list[dict]:
    """FTS5 + 语义搜索混合。FTS5 结果为主，语义补充。"""
    # 自动路由（与 search() 一致）
    if domain == "knowledge" and not knowledge_db:
        knowledge_db = True
    fts_results = search(query, limit, domain, global_db, profile, knowledge_db, **filters)
    sem_results = semantic_search(query, limit * 2, domain, global_db, profile, knowledge_db)

    # 去重合并：FTS5 在前，语义补充
    seen = set(r["id"] for r in fts_results)
    merged = list(fts_results)
    for r in sem_results:
        if r["id"] not in seen:
            r["_method"] = "semantic"
            merged.append(r)
            seen.add(r["id"])
        else:
            # 给 FTS5 命中项加语义分数
            for m in merged:
                if m["id"] == r["id"]:
                    m["score"] = r["score"]
                    break
    return merged[:limit]



def load_l1(storage_path: str) -> str | None:
    """加载 L1 详细摘要文件。"""
    if not storage_path:
        return None
    # storage_path 可能是相对路径，尝试多个位置
    p = Path(storage_path)
    if p.exists():
        return p.read_text(encoding="utf-8")
    # 尝试全局库路径
    gp = GLOBAL_VAULT / "overviews" / storage_path
    if gp.exists():
        return gp.read_text(encoding="utf-8")
    return None


def load_decisions(global_db: bool = True, profile: str = "default") -> str:
    """加载决策。从全局库 abstracts.db 的 decisions 表读取。"""
    lines = ["# 已记录的全局决策"]

    db = get_db(global_db=global_db)
    if db:
        try:
            rows = db.execute("SELECT slug, content FROM decisions ORDER BY id").fetchall()
            for r in rows:
                lines.append(f"- {r[0]}: {r[1]}")
            db.close()
        except Exception as e:
            logger.debug("load decisions failed: %s", e)

    return "\n".join(lines) if len(lines) > 1 else ""


def auto_inject(source_type: str = None, profile: str = "default") -> str:
    """新会话自动注入：加载全局库最近 L1 摘要 + 决策。"""
    parts = []
    decisions = load_decisions(profile=profile)
    if decisions:
        parts.append(decisions)

    # 加载全局记忆
    db = get_db(global_db=True)
    if db is None and not source_type:
        return "\n\n".join(parts)

    # 如果指定 knowledge 或未指定，也查知识库
    kb_db = None
    if source_type == "doc" or source_type is None:
        kb_db = get_db(knowledge_db=True)

    all_rows = []

    if db:
        type_filter = ""
        params = []
        if source_type == "session":
            type_filter = "AND source_type='memory'"

        rows = db.execute(
            f"SELECT a.id, a.abstract, a.source_type, a.storage_path, a.created_at "
            f"FROM abstracts a WHERE a.status IN ('active', 'downgraded', 'stale') {type_filter} "
            f"ORDER BY a.weight DESC, a.created_at DESC LIMIT ?",
            params + [CONFIG.get("retrieval", {}).get("auto_inject_limit", 5)],
        ).fetchall()
        all_rows.extend(rows)

    if kb_db:
        rows2 = kb_db.execute(
            "SELECT a.id, a.abstract, a.source_type, a.storage_path, a.created_at "
            "FROM abstracts a WHERE a.source_type='knowledge' AND a.status IN ('active', 'downgraded', 'stale') "
            "ORDER BY a.weight DESC, a.created_at DESC LIMIT ?",
            [CONFIG.get("retrieval", {}).get("auto_inject_limit", 3)],
        ).fetchall()
        all_rows.extend(rows2)
        kb_db.close()

    if db:
        db.close()


    shown = set()
    session_printed = False
    doc_printed = False

    for r in all_rows:
        # 尝试读 L1
        l1 = None
        if r["storage_path"]:
            l1 = load_l1(r["storage_path"])
        if not l1:
            continue
        if r["storage_path"] in shown:
            continue
        shown.add(r["storage_path"])

        if r["source_type"] == "knowledge" and not doc_printed:
            parts.append("\n# 📄 知识库")
            doc_printed = True
        elif r["source_type"] == "memory" and not session_printed:
            parts.append("\n# 💬 记忆")
            session_printed = True

        paragraphs = [p.strip() for p in l1.split("\n\n") if p.strip()][:3]
        parts.append(f"\n## {r['abstract']}\n\n" + "\n\n".join(paragraphs))

    return "\n\n".join(parts)


def health_check(profile: str = "default") -> dict:
    """检查三库状态。"""
    status = {
        "global_abstracts_db": False,
        "profile_abstracts_db": False,
        "knowledge_abstracts_db": False,
        "memory_l0": 0,
        "knowledge_l0": 0,
        "lmstudio": False,
    }

    gdb = get_db(global_db=True)
    if gdb:
        try:
            gdb.execute("SELECT COUNT(*) FROM abstracts")
            status["global_abstracts_db"] = True
            status["memory_l0"] = gdb.execute(
                "SELECT COUNT(*) FROM abstracts WHERE source_type='memory'"
            ).fetchone()[0]
            gdb.close()
        except Exception as e:
            logger.error("Global health check error: %s", e)

    kdb = get_db(knowledge_db=True)
    if kdb:
        try:
            kdb.execute("SELECT COUNT(*) FROM abstracts")
            status["knowledge_abstracts_db"] = True
            status["knowledge_l0"] = kdb.execute(
                "SELECT COUNT(*) FROM abstracts"
            ).fetchone()[0]
            kdb.close()
        except Exception as e:
            logger.error("Knowledge health check error: %s", e)

    pdb = get_db(profile=profile)
    if pdb:
        try:
            pdb.execute("SELECT COUNT(*) FROM abstracts")
            status["profile_abstracts_db"] = True
            pdb.close()
        except Exception:
            pass

    try:
        r = requests.get("http://localhost:1234/v1/models", timeout=3)
        status["lmstudio"] = "qwen3.6" in r.text
    except Exception:
        pass

    return status


if __name__ == "__main__":
    setup_logging()
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", help="检索关键词")
    ap.add_argument("--limit", type=int, default=5, help="返回结果数")
    ap.add_argument("--domain", choices=["memory", "knowledge"], help="限定域")
    ap.add_argument("--global-db", action="store_true", help="搜全局库（默认搜专属）")
    ap.add_argument("--profile", default="default", help="专属库的 profile 名")
    ap.add_argument("--auto-inject", action="store_true", help="自动注入")
    ap.add_argument("--health", action="store_true", help="健康检查")
    ap.add_argument("--decisions", action="store_true", help="仅输出决策")
    # 结构化过滤参数
    ap.add_argument("--brand", help="知识库: 品牌名")
    ap.add_argument("--l1", help="知识库: 一级类目")
    ap.add_argument("--l2", help="知识库: 二级类目")
    ap.add_argument("--audience", help="知识库: 目标人群")
    ap.add_argument("--brand-tier", help="知识库: 品牌归类(国货美妆/国际大牌)")
    ap.add_argument("--audience-tag", help="知识库: 人群归类(成分党/职场白领)")
    ap.add_argument("--decision-type", help="记忆: 决策类型(踩坑/经验/...)")
    ap.add_argument("--project-type", help="记忆: 项目类型(系统开发/数据分析/...)")
    ap.add_argument("--project", help="记忆: 项目名")
    ap.add_argument("--profile-tag", help="记忆: 环境标签(工作/个人)")
    ap.add_argument("--semantic", action="store_true", help="纯语义搜索（默认 FTS5）")
    ap.add_argument("--hybrid", action="store_true", help="FTS5 + 语义混合搜索")
    ap.add_argument("--include-obsolete", action="store_true", help="包含已 superseded 的旧版本（默认只返回 active + downgraded）")
    ap.add_argument("--rerank", action="store_true", help="用 LM Studio 对搜索结果重排序")
    args = ap.parse_args()

    if args.health:
        print(json.dumps(health_check(args.profile), ensure_ascii=False, indent=2))
        sys.exit(0)

    if args.decisions:
        print(load_decisions(profile=args.profile))
        sys.exit(0)

    if args.auto_inject:
        print(auto_inject(profile=args.profile))
        sys.exit(0)

    if args.query:
        filters = {}
        if args.brand:
            filters["brand"] = args.brand
        if args.l1:
            filters["category_l1"] = args.l1
        if args.l2:
            filters["category_l2"] = args.l2
        if args.audience:
            filters["target_audience"] = args.audience
        if args.brand_tier:
            filters["brand_tier"] = args.brand_tier
        if args.audience_tag:
            filters["audience_tag"] = args.audience_tag
        if args.decision_type:
            filters["decision_type"] = args.decision_type
        if args.project:
            filters["project"] = args.project
        if args.project_type:
            filters["project_type"] = args.project_type
        if args.profile_tag:
            filters["profile_tag"] = args.profile_tag

        if args.semantic:
            results = semantic_search(args.query, args.limit, args.domain,
                                      args.global_db, args.profile,
                                      knowledge_db=(args.domain == "knowledge"))
        elif args.hybrid:
            results = hybrid_search(args.query, args.limit, args.domain,
                                    args.global_db, args.profile, **filters)
        else:
            results = search(args.query, args.limit, args.domain,
                             args.global_db, args.profile, **filters)

        if args.rerank and results:
            old_len = len(results)
            results = rerank(args.query, results, args.limit * 2)
            print(f"  🔄 Rerank: {old_len}→{len(results)} 条", file=sys.stderr)

        if not results:
            print("未找到相关结果")
            sys.exit(1)
        for i, r in enumerate(results, 1):
            tag = "📄" if r.get("source_type") == "knowledge" else "💬"
            label = "知识库" if r.get("source_type") == "knowledge" else "记忆"
            weight = r.get("weight", "-")
            score = r.get("score", "")
            version = r.get("version", "")
            status = r.get("status", "")
            meta = []
            if version:
                meta.append(f"v{version}")
            if status and status != "active":
                meta.append(status)
            if score:
                meta.append(f"score={score}")
            meta_str = f" [{', '.join(meta)}]" if meta else ""
            print(f"{i}. {tag} [{label}] {r['abstract']} (weight={weight}{meta_str})")
            l1 = load_l1(r.get("storage_path", ""))
            if l1:
                preview = l1[:400].replace("\n", " ")
                print(f"   {preview}...")
            print()
        sys.exit(0)

    ap.print_help()
