#!/usr/bin/env python3
"""
hooks/auto.py — v2 系统状态 & 注入输出

用法:
    auto.py status                   系统状态
    auto.py status --profile work    指定 profile
    auto.py inject                   注入输出
"""

import json, sqlite3, subprocess, sys
from pathlib import Path
import requests, yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))
from client import resolve_path
from retriever import setup_logging, get_db

setup_logging()

CONFIG = yaml.safe_load((ROOT.parent / "Memory" / "config.yaml").read_text())
GLOBAL_VAULT = Path(resolve_path(CONFIG["paths"]["global_vault"]))
KNOWLEDGE_VAULT = Path(resolve_path(CONFIG["paths"]["knowledge_base"]))
PROFILE_BASE = Path(resolve_path(CONFIG["paths"]["profile_vault_base"])) if CONFIG.get("paths", {}).get("profile_vault_base") else None


def status(profile: str = "default") -> dict:
    """三库状态统计。"""
    stat = {
        "profile": profile,
        "global_db": False,
        "profile_db": False,
        "knowledge_db": False,
        "memory_l0": 0,
        "knowledge_l0": 0,
        "profile_l0": 0,
        "overviews": 0,
        "doc_overviews": 0,
        "lmstudio": False,
    }

    # 全局库（只存 memory 记忆索引）
    gdb = GLOBAL_VAULT / "abstracts.db"
    if gdb.exists():
        try:
            db = sqlite3.connect(str(gdb))
            stat["global_db"] = True
            stat["memory_l0"] = db.execute(
                "SELECT COUNT(*) FROM abstracts WHERE source_type='memory'"
            ).fetchone()[0]
            db.close()
        except Exception:
            pass

    # 知识库（独立 DB）
    kdb = KNOWLEDGE_VAULT / "abstracts.db"
    if kdb.exists():
        try:
            db = sqlite3.connect(str(kdb))
            stat["knowledge_db"] = True
            stat["knowledge_l0"] = db.execute(
                "SELECT COUNT(*) FROM abstracts"
            ).fetchone()[0]
            db.close()
        except Exception:
            pass

    # 专属库
    pdb_path = None
    if PROFILE_BASE:
        pdb_path = PROFILE_BASE / profile / "memory-vault" / "abstracts.db"
    if pdb_path and pdb_path.exists():
        try:
            db = sqlite3.connect(str(pdb_path))
            stat["profile_db"] = True
            stat["profile_l0"] = db.execute(
                "SELECT COUNT(*) FROM abstracts"
            ).fetchone()[0]
            db.close()
        except Exception:
            pass

    # 文件统计（专属库 overviews + 知识库 overviews）
    if PROFILE_BASE:
        sess_dir = PROFILE_BASE / profile / "memory-vault" / "overviews" / "sessions"
        if sess_dir.exists():
            stat["overviews"] = len(list(sess_dir.glob("*.md")))
    doc_dir = KNOWLEDGE_VAULT / "overviews" / "docs"
    if doc_dir.exists():
        stat["doc_overviews"] = len(list(doc_dir.glob("*.md")))

    # LM Studio
    try:
        r = requests.get("http://localhost:1234/v1/models", timeout=3)
        stat["lmstudio"] = "qwen3.6" in r.text
    except Exception:
        pass

    return stat


def inject(source: str = "session", profile: str = "default"):
    """调用 retriever.auto_inject 并包装 memory-context。"""
    from retriever import auto_inject
    text = auto_inject(source_type=source, profile=profile)
    print(f"<memory-context>\n{text}\n</memory-context>")


def main():
    if len(sys.argv) < 2:
        print("用法: auto.py status|inject|recover|compact|list-archived [--source session|doc] [--profile name] [--id N] [--query ...]")
        sys.exit(1)

    cmd = sys.argv[1]
    source = "session"
    profile = "default"

    if "--source" in sys.argv:
        idx = sys.argv.index("--source")
        if idx + 1 < len(sys.argv):
            source = sys.argv[idx + 1]
    if "--profile" in sys.argv:
        idx = sys.argv.index("--profile")
        if idx + 1 < len(sys.argv):
            profile = sys.argv[idx + 1]

    if cmd == "status":
        print(json.dumps(status(profile), ensure_ascii=False, indent=2))
    elif cmd == "inject":
        inject(source, profile)
    elif cmd == "recover":
        from lifecycle import recover_by_id, recover_by_query, list_archived
        if "--id" in sys.argv:
            idx = sys.argv.index("--id")
            if idx + 1 < len(sys.argv):
                print(recover_by_id(int(sys.argv[idx + 1]), profile))
        elif "--query" in sys.argv:
            idx = sys.argv.index("--query")
            if idx + 1 < len(sys.argv):
                print(recover_by_query(sys.argv[idx + 1], profile))
        else:
            print(list_archived(profile))
    elif cmd == "compact":
        from lifecycle import compact as lc_compact
        print(lc_compact(profile))
    elif cmd == "list-archived":
        from lifecycle import list_archived
        print(list_archived(profile))
    else:
        print("未知命令:", cmd)


if __name__ == "__main__":
    main()
