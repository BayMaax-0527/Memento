#!/usr/bin/env python3
"""
hooks/recall.py — v2 手动检索入口

用法:
    recall.py 查XXX                                          专属库检索
    recall.py 查XXX --global                                  全局库检索
    recall.py 查XXX --domain memory                           仅搜记忆
    recall.py 查XXX --domain knowledge                        仅搜知识
    recall.py --l1 <id>                                       载入 L1 全文
    recall.py --decisions                                     输出决策
    recall.py --list-categories                               列出全局记忆类别
    recall.py --list-knowledge-categories                     列出知识库类别
    recall.py --open-raw <slug>                               打开知识库原始文档

结构化过滤:
    recall.py 珀莱雅面霜 --domain knowledge --brand 珀莱雅 --l2 面霜
    recall.py 踩坑 --domain memory --decision-type 踩坑 --profile-tag 工作
    recall.py 面霜 --domain knowledge --brand-tier 国货美妆 --audience-tag 成分党
    recall.py 项目 --domain memory --project-type 系统开发
"""

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))
from retriever import setup_logging, search, load_l1, load_decisions, health_check

setup_logging()


def parse_query_args(raw_args: list[str]) -> dict:
    """解析用户输入的检索参数。支持 --domain/--global/结构化字段/类别浏览。"""
    params = {"query": "", "domain": None, "global_db": False, "profile": "default",
              "limit": 5, "l1": None, "decisions": False,
              "list_categories": False, "list_knowledge_categories": False,
              "open_raw": None,
              "filters": {}}
    i = 0
    while i < len(raw_args):
        a = raw_args[i]
        if a == "--domain" and i + 1 < len(raw_args):
            params["domain"] = raw_args[i + 1]
            i += 2
        elif a == "--global":
            params["global_db"] = True
            i += 1
        elif a == "--profile" and i + 1 < len(raw_args):
            params["profile"] = raw_args[i + 1]
            i += 2
        elif a == "--limit" and i + 1 < len(raw_args):
            params["limit"] = int(raw_args[i + 1])
            i += 2
        elif a == "--l1" and i + 1 < len(raw_args):
            params["l1"] = raw_args[i + 1]
            i += 2
        elif a == "--decisions":
            params["decisions"] = True
            i += 1
        elif a == "--list-categories":
            params["list_categories"] = True
            i += 1
        elif a == "--list-knowledge-categories":
            params["list_knowledge_categories"] = True
            i += 1
        elif a == "--open-raw" and i + 1 < len(raw_args):
            params["open_raw"] = raw_args[i + 1]
            i += 2
        elif a == "--brand" and i + 1 < len(raw_args):
            params["filters"]["brand"] = raw_args[i + 1]
            i += 2
        elif a == "--l2" and i + 1 < len(raw_args):
            params["filters"]["category_l2"] = raw_args[i + 1]
            i += 2
        elif a == "--audience" and i + 1 < len(raw_args):
            params["filters"]["target_audience"] = raw_args[i + 1]
            i += 2
        elif a == "--brand-tier" and i + 1 < len(raw_args):
            params["filters"]["brand_tier"] = raw_args[i + 1]
            i += 2
        elif a == "--audience-tag" and i + 1 < len(raw_args):
            params["filters"]["audience_tag"] = raw_args[i + 1]
            i += 2
        elif a == "--decision-type" and i + 1 < len(raw_args):
            params["filters"]["decision_type"] = raw_args[i + 1]
            i += 2
        elif a == "--project" and i + 1 < len(raw_args):
            params["filters"]["project"] = raw_args[i + 1]
            i += 2
        elif a == "--project-type" and i + 1 < len(raw_args):
            params["filters"]["project_type"] = raw_args[i + 1]
            i += 2
        elif a == "--profile-tag" and i + 1 < len(raw_args):
            params["filters"]["profile_tag"] = raw_args[i + 1]
            i += 2
        else:
            if params["query"]:
                params["query"] += " " + a
            else:
                params["query"] = a
            i += 1
    return params


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    p = parse_query_args(sys.argv[1:])

    if p["list_categories"]:
        cat_dir = ROOT.parent / "Memory" / "categories"
        if cat_dir.exists():
            for f in sorted(cat_dir.glob("*.md")):
                print(f"📂 {f.stem}")
        else:
            print("全局记忆类别目录不存在")
        sys.exit(0)

    if p["list_knowledge_categories"]:
        cat_dir = ROOT.parent / "Knowledge" / "categories"
        if cat_dir.exists():
            for f in sorted(cat_dir.glob("*.md")):
                print(f"📂 {f.stem}")
        else:
            print("知识库类别目录不存在")
        sys.exit(0)

    if p["open_raw"]:
        raw_dir = ROOT.parent / "Knowledge" / "raw"
        matches = list(raw_dir.glob(f"*{p['open_raw']}*"))
        if not matches:
            print(f"未找到原始文档: {p['open_raw']}")
            sys.exit(1)
        for f in matches:
            print(f.read_text(encoding="utf-8", errors="ignore"))
        sys.exit(0)

    if p["decisions"]:
        print(load_decisions(profile=p["profile"]))
        sys.exit(0)

    if p["l1"]:
        knowledge_db = p["domain"] == "knowledge" or p.get("knowledge_db", False)
        result = search(p["l1"], limit=1, domain=p["domain"],
                        global_db=p["global_db"], profile=p["profile"],
                        knowledge_db=knowledge_db)
        if result and result[0].get("storage_path"):
            l1 = load_l1(result[0]["storage_path"])
            if l1:
                print(l1)
            else:
                print("未找到 L1 文件")
        else:
            print("未找到相关条目")
        sys.exit(0)

    if p["query"]:
        knowledge_db = p["domain"] == "knowledge" or p.get("knowledge_db", False)
        results = search(p["query"], limit=p["limit"], domain=p["domain"],
                         global_db=p["global_db"], profile=p["profile"],
                         knowledge_db=knowledge_db, **p["filters"])
        if not results:
            print("未找到相关结果")
            sys.exit(1)
        for i, r in enumerate(results, 1):
            tag = "📄" if r.get("source_type") == "knowledge" else "💬"
            label = "知识库" if r.get("source_type") == "knowledge" else "记忆"
            weight = r.get("weight", "-")
            print(f"{i}. {tag} [{label}] {r['abstract']} (weight={weight})")
            l1 = load_l1(r.get("storage_path", ""))
            if l1:
                preview = l1[:400].replace("\n", " ")
                print(f"   {preview}...")
            print()
        sys.exit(0)

    print(__doc__)


if __name__ == "__main__":
    main()
