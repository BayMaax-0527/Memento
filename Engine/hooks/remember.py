#!/usr/bin/env python3
"""
hooks/remember.py — v2 记忆注入入口

当你说"注入记忆"时执行（--source session，默认）：
  ① 读取当前会话 JSON → 拼接对话
  ② deepseek-v4-flash 压缩 → L2
  ③ qwen3.6:35b-mlx 提取 L0/L1/决策 + 自动归类 + 打标签 + 语义关联
  ④ 写专属 profile vault（L0+memory_meta+L1+L2+memory_links）
  ⑤ 同步写入全局库（含 categories/ 更新 + decisions 表）
  ⑥ 输出 <memory-context>

  当你说"注入知识"时执行（--source doc → 转调 remember_doc.py）:
  用法:
  cd Engine && python3 hooks/remember.py --source session
  cd Engine && python3 hooks/remember.py --source doc --file /path/to/doc.md
"""

import json, logging, os, re, sqlite3, subprocess, sys
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

import yaml
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))
from client import call_llm, call_embedding, resolve_path
from retriever import setup_logging
setup_logging()
logger = logging.getLogger("remember")

CONFIG = yaml.safe_load((ROOT.parent / "Memory" / "config.yaml").read_text())

# ── v2 路径 ─────────────────────────────────────────
GLOBAL_VAULT = Path(resolve_path(CONFIG["paths"]["global_vault"]))
KNOWLEDGE_BASE = Path(resolve_path(CONFIG["paths"]["knowledge_base"]))
PROFILE_BASE = Path(resolve_path(CONFIG["paths"]["profile_vault_base"])) if CONFIG.get("paths", {}).get("profile_vault_base") else None
CURRENT_PROFILE = CONFIG.get("v2", {}).get("profile", "default")

GLOBAL_DB = GLOBAL_VAULT / "abstracts.db"
CATEGORIES_DIR = GLOBAL_VAULT / "categories"


# ── 路径辅助 ────────────────────────────────────────

def profile_vault(profile: str = None) -> Path:
    if PROFILE_BASE is None:
        raise RuntimeError(
            "profile_vault_base 未配置，请在 Memory/config.yaml 中设置 paths.profile_vault_base\n"
            "示例: profile_vault_base: ~/.hermes/profiles"
        )
    p = profile or CURRENT_PROFILE
    return PROFILE_BASE / p / "memory-vault"


def profile_db(profile: str = None) -> Path:
    return profile_vault(profile) / "abstracts.db"


def profile_dir(profile: str = None, sub: str = None) -> Path:
    base = profile_vault(profile)
    if sub == "overviews":
        return base / "overviews" / "sessions"
    if sub == "storage":
        return base / "storage" / "sessions"
    return base


# ── 数据库连接 ──────────────────────────────────────

def get_db(profile: str = None):
    """连接专属库（默认当前 profile）。传 global=True 连接全局库。"""
    db_path = profile_db(profile)
    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def get_global_db():
    db = sqlite3.connect(str(GLOBAL_DB))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db

def get_knowledge_db():
    """连接知识库 DB。"""
    db_path = KNOWLEDGE_BASE / "abstracts.db"
    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


# ── ① 读取输入 ─────────────────────────────────────

def find_latest_session(session_dir: str = None) -> dict | None:
    """找最新的会话 JSON。

    Args:
        session_dir: 会话文件夹路径。默认 ~/.hermes/webui/sessions/。
                     可用 --session-dir 指定，或直接 --file 指定具体文件。
    """
    session_dir = Path(session_dir or Path.home() / ".hermes" / "webui" / "sessions")
    if not session_dir.exists():
        return None
    files = sorted(session_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for f in files:
        if f.name.startswith("_"):
            continue
        return json.loads(f.read_text(encoding="utf-8"))
    return None


def extract_conversation(data: dict) -> str:
    """从会话 JSON 提取用户+助手对话。"""
    messages = data.get("messages", [])
    lines = []
    for m in messages:
        role = m.get("role", "")
        content = m.get("content", "")
        if role in ("system", "tool"):
            continue
        if role in ("assistant", "user"):
            if isinstance(content, str) and content.strip():
                label = "用户" if role == "user" else "Assistant"
                lines.append(f"{label}：{content}")
    return "\n\n".join(lines)


# ── ② 压缩 ──────────────────────────────────────

DEEPSEEK_SYSTEM = "你是上下文压缩引擎。请将以下内容压缩为流畅的结构化摘要，保留关键决策、踩坑记录、技术选型、结论和待办事项。中文输出，极简风格，保留原始语气。注意：内容靠后的权重越高。"
DEEPSEEK_SYSTEM_DOC = "你是文档压缩引擎。请将以下文档内容压缩为流畅的结构化摘要，保留核心概念、技术原理、架构决策、数据流、关键步骤和重要结论。中文输出，极简风格。"


def deepseek_compress(text: str, source: str = "session") -> str:
    system = DEEPSEEK_SYSTEM_DOC if source == "doc" else DEEPSEEK_SYSTEM
    prompt = f"{system}\n\n请压缩以下内容：\n\n{text}"
    return call_llm(
        [{"role": "user", "content": prompt}],
        config_key="compress",
        timeout=120,
    )


# ── ③ 本地模型提取 ─────────────────────────────────

def lmstudio_chat(system: str, user: str, model: str = None) -> str:
    kwargs = {"model": model} if model else {}
    return call_llm(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        config_key="main",
        timeout=300,
        **kwargs,
    )


# ── Embedding 生成与存储 ──────────────────────────

def generate_embedding(text: str) -> list[float]:
    return call_embedding(text, config_key="embed")


def store_embedding(db, source_id: int, source_type: str, vector: list[float], model: str = "qwen3-embedding-4b-mxfp8"):
    """将向量存入 embeddings 表。"""
    import struct
    blob = struct.pack(f"{len(vector)}f", *vector)
    db.execute(
        "INSERT OR REPLACE INTO embeddings (source_id, source_type, model, dimension, vector) "
        "VALUES (?, ?, ?, ?, ?)",
        (source_id, source_type, model, len(vector), blob),
    )
    db.commit()



def parse_l0_l1(text: str) -> tuple[list[str], str, list[str]]:
    """解析本地模型输出的 L0/L1/决策。"""
    l0_lines = []
    l1_text = ""
    decisions = []

    l0_m = re.search(r"【L0】\s*\n(.*?)(?=【L1】|【决策匹配】|\Z)", text, re.DOTALL)
    if l0_m:
        for line in l0_m.group(1).strip().split("\n"):
            line = line.strip("- ").strip()
            if line:
                l0_lines.append(line)

    l1_m = re.search(r"【L1】\s*\n(.*?)(?=【决策匹配】|\Z)", text, re.DOTALL)
    if l1_m:
        l1_text = l1_m.group(1).strip()

    dm_m = re.search(r"【决策匹配】\s*\n(.*?)(?=\Z)", text, re.DOTALL)
    if dm_m:
        for line in dm_m.group(1).strip().split("\n"):
            line = line.strip("- ").strip()
            if line:
                decisions.append(line)

    return l0_lines, l1_text, decisions


def parse_structured_fields(text: str) -> dict:
    """解析 qwen 输出中的结构化字段和 tags。"""
    fields = {
        "category": "",
        "tags": "",
        "profile_tag": "",
        "project": "",
        "project_type": "",
        "decision_type": "",
        "importance": "medium",
        "topic": "",
    }
    cat_m = re.search(r"【类别】\s*[:：]?\s*(.*?)(?=\n)", text)
    if cat_m:
        fields["category"] = cat_m.group(1).strip()

    tags_m = re.search(r"【标签】\s*[:：]?\s*(.*?)(?=\n)", text)
    if tags_m:
        fields["tags"] = tags_m.group(1).strip()

    for key in ["profile_tag", "project", "project_type", "decision_type", "importance", "topic"]:
        m = re.search(f"【{key}】\\s*[:：]?\\s*(.*?)(?=\\n)", text)
        if m:
            fields[key] = m.group(1).strip()

    return fields


# ── 软去重 ─────────────────────────────────────────

def soft_dedup(new_abstract: str, source_type: str = "memory", profile: str = None, threshold=0.8, db=None) -> str:
    """返回 'merge' 或 'insert'。对比指定域的现有条目。"""
    _db = db or get_db(profile)
    existing = _db.execute(
        "SELECT abstract FROM abstracts WHERE source_type = ?",
        (source_type,)
    ).fetchall()
    if not db:
        _db.close()
    new_core = new_abstract.split("：", 1)[-1] if "：" in new_abstract else new_abstract
    for row in existing:
        old = row["abstract"]
        old_core = old.split("：", 1)[-1] if "：" in old else old
        if SequenceMatcher(None, new_core, old_core).ratio() > threshold:
            return "merge"
    return "insert"


# ── v2 写操作 ──────────────────────────────────────

def ensure_profile_dirs(profile: str):
    """确保专属 vault 目录存在。"""
    base = profile_vault(profile)
    (base / "overviews" / "sessions").mkdir(parents=True, exist_ok=True)
    (base / "storage" / "sessions").mkdir(parents=True, exist_ok=True)


def write_l2(compressed: str, slug: str, profile: str = None) -> str:
    """写入 L2 到专属库 storage/sessions/。"""
    now = datetime.now().strftime("%Y-%m-%d")
    p = profile or CURRENT_PROFILE
    path = profile_vault(p) / "storage" / "sessions" / f"{now}_{slug}.md"
    path.write_text(f"# 会话压缩 — {now}\n\n{compressed}", encoding="utf-8")
    return str(path)


def write_l1(l1_text: str, slug: str, profile: str = None) -> str:
    """写入 L1 到专属库 overviews/sessions/。"""
    now = datetime.now().strftime("%Y-%m-%d")
    p = profile or CURRENT_PROFILE
    path = profile_vault(p) / "overviews" / "sessions" / f"{now}_sess_{slug}.md"
    path.write_text(f"# 会话摘要 — {now}\n\n{l1_text}", encoding="utf-8")
    return str(path)


def _get_active_by_topic(db, topic: str, profile: str = None, source_type: str = "memory") -> tuple[int | None, bool]:
    """查找同 topic 的非 superseded 记录，返回 (id, is_downgraded) 或 (None, False)。"""
    if not topic:
        return None, False
    try:
        row = db.execute(
            "SELECT a.id, a.status FROM abstracts a "
            "JOIN memory_meta m ON a.id = m.abstract_id "
            "WHERE a.status IN ('active','downgraded') AND a.source_type=? AND m.topic=? AND m.profile=? "
            "LIMIT 1",
            (source_type, topic, profile or "default"),
        ).fetchone()
        if row:
            return row[0], row[1] == "downgraded"
        return None, False
    except Exception:
        return None, False


def _supersede_record(db, old_id: int, new_id: int):
    """将旧记录标记为 superseded。"""
    db.execute(
        "UPDATE abstracts SET status='superseded', superseded_by_id=? WHERE id=?",
        (new_id, old_id),
    )


def write_profile_l0_and_meta(
    l0_lines: list[str],
    fields: dict,
    slug: str,
    l1_path: str,
    profile: str = None,
):
    """写入专属库 L0 + memory_meta。支持 topic 版本覆盖。"""
    p = profile or CURRENT_PROFILE
    db = get_db(p)

    topic = fields.get("topic", "") or ""
    
    # 先查找同 topic 的旧记录，确定版本号
    old_id, is_downgraded = _get_active_by_topic(db, topic, p)
    old_version = 1
    if old_id:
        try:
            old_version = db.execute("SELECT version FROM abstracts WHERE id=?", (old_id,)).fetchone()[0]
        except Exception:
            pass

    abstract_ids = []
    for abstract in l0_lines:
        action = soft_dedup(abstract, source_type="memory", profile=p, db=db)
        if action == "insert":
            cur = db.execute(
                "INSERT INTO abstracts (source_type, abstract, category, tags, storage_path, version, status) "
                "VALUES ('memory', ?, ?, ?, ?, ?, 'active')",
                (abstract, fields.get("category", ""), fields.get("tags", ""), l1_path, old_version + 1 if topic else 1),
            )
            abstract_ids.append(cur.lastrowid)
        else:
            existing = db.execute(
                "SELECT id FROM abstracts WHERE source_type='memory' AND abstract LIKE ? LIMIT 1",
                (abstract[:20] + "%",)
            ).fetchone()
            if existing:
                abstract_ids.append(existing["id"])
                db.execute(
                    "UPDATE abstracts SET tags=?, storage_path=? WHERE id=?",
                    (fields.get("tags", ""), l1_path, existing["id"]),
                )

    # 二击保险：第一次 downgrade，第二次 supersede
    if topic and old_id:
        if is_downgraded:
            # 第二次确认 → 真正 supersede
            for aid in abstract_ids:
                _supersede_record(db, old_id, aid)
                old_id = None
        else:
            # 第一次 → 降级 downgraded（保险期）
            db.execute("UPDATE abstracts SET status='downgraded' WHERE id=?", (old_id,))

    # 写入 memory_meta（含 topic）
    for aid in abstract_ids:
        db.execute(
            "INSERT OR REPLACE INTO memory_meta "
            "(abstract_id, profile, profile_tag, project, project_type, decision_type, importance, topic) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (aid, p, fields.get("profile_tag", ""), fields.get("project", ""),
             fields.get("project_type", ""), fields.get("decision_type", ""),
             fields.get("importance", "medium"), topic),
        )

    db.commit()
    db.close()
    return abstract_ids


def write_global_l0(
    abstract_ids: list[int],
    l0_lines: list[str],
    fields: dict,
    slug: str,
    l1_path: str,
    profile: str = None,
) -> list[int]:
    """同步写入全局库 L0 + memory_meta + categories/。返回 global_ids。支持 topic 版本覆盖。"""
    p = profile or CURRENT_PROFILE
    gdb = get_global_db()
    
    topic = fields.get("topic", "") or ""

    # 查找同 topic 的旧记录
    old_id, is_downgraded = _get_active_by_topic(gdb, topic, p)
    old_version = 1
    if old_id:
        try:
            old_version = gdb.execute("SELECT version FROM abstracts WHERE id=?", (old_id,)).fetchone()[0]
        except Exception:
            pass

    # 写 L0（带版本+状态 + 软去重）
    global_ids = []
    for i, abstract in enumerate(l0_lines):
        action = soft_dedup(abstract, source_type="memory", db=gdb)
        if action == "merge":
            # 已存在，更新 storage_path
            existing = gdb.execute(
                "SELECT id FROM abstracts WHERE source_type='memory' AND abstract LIKE ? LIMIT 1",
                (abstract[:20] + "%",)
            ).fetchone()
            if existing:
                global_ids.append(existing["id"])
                gdb.execute(
                    "UPDATE abstracts SET tags=?, storage_path=? WHERE id=?",
                    (fields.get("tags", ""), l1_path, existing["id"]),
                )
            continue
        cur = gdb.execute(
            "INSERT INTO abstracts (source_type, abstract, category, tags, storage_path, version, status) "
            "VALUES ('memory', ?, ?, ?, ?, ?, 'active')",
            (abstract, fields.get("category", ""), fields.get("tags", ""), l1_path,
             old_version + 1 if topic else 1),
        )
        global_ids.append(cur.lastrowid)

    # 二击保险：第一次 downgrade，第二次 supersede
    if topic and old_id:
        if is_downgraded:
            for gid in global_ids:
                _supersede_record(gdb, old_id, gid)
                old_id = None
        else:
            gdb.execute("UPDATE abstracts SET status='downgraded' WHERE id=?", (old_id,))

    # 写 memory_meta（含 topic）
    for gid in global_ids:
        gdb.execute(
            "INSERT OR REPLACE INTO memory_meta "
            "(abstract_id, profile, profile_tag, project, project_type, decision_type, importance, topic) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (gid, p, fields.get("profile_tag", ""), fields.get("project", ""),
             fields.get("project_type", ""), fields.get("decision_type", ""),
             fields.get("importance", "medium"), topic),
        )

    gdb.commit()

    # 更新 categories/
    cat = fields.get("category", "")
    if cat:
        # 清理类别名：去掉路径分隔符，防止创建子目录
        cat_clean = cat.replace("/", "_").replace("\\", "_").strip()
        cat_file = CATEGORIES_DIR / f"{cat_clean}.md"
        if cat_file.exists():
            content = cat_file.read_text(encoding="utf-8")
            entry = f"- {p}:{slug}"
            if entry not in content:
                with open(cat_file, "a", encoding="utf-8") as f:
                    f.write(f"{entry}\n")
        else:
            cat_file.write_text(
                f"# {cat}\n\n{datetime.now().strftime('%Y-%m-%d')} 创建\n\n- {p}:{slug}\n",
                encoding="utf-8",
            )

    gdb.close()
    return global_ids


def _infer_transitive_links(db, source_ids: list[int], max_hops: int = 2):
    """BFS 传递闭包推理：新条目关联的目标，它们的目标也自动关联。"""
    if not source_ids:
        return
    seen = set(source_ids)
    for aid in source_ids:
        # 1 跳
        hop1 = db.execute(
            "SELECT target_abstract_id FROM memory_links WHERE source_id=?",
            (aid,),
        ).fetchall()
        for h1 in hop1:
            tid = h1[0]
            if tid in seen:
                continue
            seen.add(tid)
            if max_hops < 2:
                continue
            # 2 跳
            hop2 = db.execute(
                "SELECT target_abstract_id FROM memory_links WHERE source_id=? AND target_abstract_id != ?",
                (tid, aid),
            ).fetchall()
            for h2 in hop2:
                t2 = h2[0]
                if t2 in seen:
                    continue
                seen.add(t2)
                db.execute(
                    "INSERT OR IGNORE INTO memory_links (source_id, target_abstract_id, relation) "
                    "VALUES (?, ?, 'inferred')",
                    (aid, t2),
                )


def write_memory_links(abstract_ids: list[int], global_ids: list[int], decisions: list[str], profile: str = None):
    """写入语义关联到 memory_links。解析"关联:" + tag 重叠 + 传递闭包推理。"""
    if not abstract_ids and not global_ids:
        return
    link_ids = global_ids or abstract_ids
    
    import re as _re
    p = profile or CURRENT_PROFILE
    
    # 收集"关联: slug" + 新 memory 的 tags
    linked_slugs = []
    if decisions:
        for line in decisions:
            m = _re.search(r"关联[：:]\s*(.+?)(?:\s*[（(]|\s*$)", line)
            if m:
                linked_slugs.append(m.group(1).strip())
    
    # 获取新注入的 tags
    new_tags_str = ""
    try:
        db = get_db(p)
        first = db.execute("SELECT tags FROM abstracts WHERE id=? LIMIT 1", (link_ids[0],)).fetchone()
        if first:
            new_tags_str = first["tags"] or ""
        db.close()
    except Exception:
        pass
    new_tags = set(t.strip() for t in new_tags_str.split(",") if t.strip())
    
    try:
        gdb = get_global_db()
        
        # 1. 解析"关联:"行写入链接
        for slug in linked_slugs:
            rows = gdb.execute(
                "SELECT id FROM abstracts WHERE abstract LIKE ? OR tags LIKE ? LIMIT 3",
                (f"%{slug[:20]}%", f"%{slug[:20]}%")
            ).fetchall()
            for r in rows:
                for aid in link_ids:
                    gdb.execute(
                        "INSERT OR IGNORE INTO memory_links (source_id, target_abstract_id, relation) "
                        "VALUES (?, ?, 'related')",
                        (aid, r["id"])
                    )
        
        # 2. tag 重叠关联（无需 qwen 输出"关联:"）
        if new_tags:
            placeholders = ",".join("?" * len(link_ids))
            existing = gdb.execute(
                f"SELECT id, tags FROM abstracts WHERE source_type='memory' AND tags != '' AND id NOT IN ({placeholders})",
                link_ids
            ).fetchall()
            for row in existing:
                old_tags = set(t.strip() for t in (row["tags"] or "").split(",") if t.strip())
                if old_tags and new_tags:
                    overlap = len(new_tags & old_tags) / min(len(new_tags), len(old_tags))
                    if overlap >= 0.5:
                        for aid in link_ids:
                            gdb.execute(
                                "INSERT OR IGNORE INTO memory_links (source_id, target_abstract_id, relation) "
                                "VALUES (?, ?, 'tag_overlap')",
                                (aid, row["id"]),
                            )
        
        # 3. 传递闭包推理（2 跳以内）
        _infer_transitive_links(gdb, link_ids)
        
        gdb.commit()
        gdb.close()
    except Exception as e:
        logger.debug("write_memory_links 全局跳过: %s", e)

    # ── 同步写入 profile 库 ──
    if not abstract_ids:
        return
    try:
        pdb = get_db(p)
        p_link_ids = abstract_ids

        # 1. 关联 slug
        for slug in linked_slugs:
            rows = pdb.execute(
                "SELECT id FROM abstracts WHERE abstract LIKE ? OR tags LIKE ? LIMIT 3",
                (f"%{slug[:20]}%", f"%{slug[:20]}%")
            ).fetchall()
            for r in rows:
                for aid in p_link_ids:
                    pdb.execute(
                        "INSERT OR IGNORE INTO memory_links (source_id, target_abstract_id, relation) "
                        "VALUES (?, ?, 'related')",
                        (aid, r["id"])
                    )

        # 2. tag 重叠
        if new_tags:
            p_placeholders = ",".join("?" * len(p_link_ids))
            existing = pdb.execute(
                f"SELECT id, tags FROM abstracts WHERE source_type='memory' AND tags != '' AND id NOT IN ({p_placeholders})",
                p_link_ids
            ).fetchall()
            for row in existing:
                old_tags = set(t.strip() for t in (row["tags"] or "").split(",") if t.strip())
                if old_tags and new_tags:
                    overlap = len(new_tags & old_tags) / min(len(new_tags), len(old_tags))
                    if overlap >= 0.5:
                        for aid in p_link_ids:
                            pdb.execute(
                                "INSERT OR IGNORE INTO memory_links (source_id, target_abstract_id, relation) "
                                "VALUES (?, ?, 'tag_overlap')",
                                (aid, row["id"]),
                            )

        # 3. 传递闭包
        _infer_transitive_links(pdb, p_link_ids)

        pdb.commit()
        pdb.close()
    except Exception as e:
        logger.debug("write_memory_links profile 跳过: %s", e)


# ── qwen 提取 prompt（v2 增强版） ──────────────────

EXTRACT_SYSTEM_DOC = """你是知识提取引擎。从文档压缩文本中提取结构化信息和标签。

输出格式（请严格遵循）：

【品牌】品牌名称（如：珀莱雅/欧莱雅/无）
【品牌档次】品牌档次（如：国货美妆/国际大牌/无）
【一级类目】商品的一级类目（如：美妆护肤/食品饮料/保健品）
【二级类目】商品的二级类目或细分归类（如：面霜/精华液/乳制品）
【目标人群】目标人群（如：敏感肌/熬夜肌/减脂人群）
【人群标签】人群标签（如：成分党/职场白领/学生党）
【话题】稳定主题键，格式：{品牌}/{一级类目}/{二级类目}（如：珀莱雅/美妆护肤/面霜）
【关系】新旧关系（replaces=新替代旧/supplements=补充旧/refines=细化修正旧/independent=不同维度无关）
【标签】逗号分隔的关键词（3-5 个）

【L0】
- 主题: 一句话核心结论（<20字）
- 主题: 一句话核心结论（<20字）
... 3-5 条

【L1】
## 核心主题
## 关键结论
## 适用场景

不添加礼貌语、不添加emoji。纯中文输出。"""

EXTRACT_USER_TEMPLATE_DOC = """## 压缩文本
{compressed}

## 要求
分析文档内容，提取品牌、类目、人群等结构化信息，生成 3-5 条 L0 摘要和 L1 结构化摘要。"""


def parse_knowledge(text: str) -> tuple[list[str], str, dict]:
    """解析知识注入的 L0/L1/结构化字段。"""
    l0_lines = []
    l1_text = ""
    fields = {
        "brand_name": "", "brand_tier": "",
        "category_l1": "", "category_l2": "",
        "target_audience": "", "audience_tag": "",
        "topic": "", "relation": "",
        "tags": "",
    }

    l0_m = re.search(r"【L0】\s*\n(.*?)(?=【L1】|\Z)", text, re.DOTALL)
    if l0_m:
        for line in l0_m.group(1).strip().split("\n"):
            line = line.strip("- ").strip()
            if line:
                l0_lines.append(line)

    l1_m = re.search(r"【L1】\s*\n(.*?)(?=\Z)", text, re.DOTALL)
    if l1_m:
        l1_text = l1_m.group(1).strip()

    # 解析结构化字段
    mapping = {
        "品牌": "brand_name", "品牌档次": "brand_tier",
        "一级类目": "category_l1", "二级类目": "category_l2",
        "目标人群": "target_audience", "人群标签": "audience_tag",
        "话题": "topic", "关系": "relation",
        "标签": "tags",
    }
    for cn, en in mapping.items():
        m = re.search(f"【{cn}】\\s*[:：]?\\s*(.*?)(?=\\n|【|\\Z)", text)
        if m:
            val = m.group(1).strip().rstrip("。，, ")
            if val and val != "无":
                fields[en] = val

    return l0_lines, l1_text, fields


def write_knowledge_l0_l1(
    l0_lines: list[str],
    l1_text: str,
    knowledge_fields: dict,
    slug: str,
    l1_path: str,
) -> list[int]:
    """写入知识库的 L0 + knowledge_meta 到知识库 DB。支持 topic 版本覆盖。"""
    now = datetime.now().strftime("%Y-%m-%d")
    gdb = get_knowledge_db()
    
    topic = knowledge_fields.get("topic", "") or ""
    relation = knowledge_fields.get("relation", "") or ""
    
    # 二击保险逻辑
    old_ids = []
    old_version = 1
    already_downgraded = False
    if topic:
        try:
            rows = gdb.execute(
                "SELECT a.id, a.version, a.status FROM abstracts a "
                "JOIN knowledge_meta k ON a.id = k.abstract_id "
                "WHERE a.status IN ('active','downgraded') AND a.source_type='knowledge' AND k.topic=? "
                "ORDER BY a.id",
                (topic,),
            ).fetchall()
            for row in rows:
                if row[2] == "downgraded":
                    already_downgraded = True
                else:
                    old_ids.append(row[0])
                old_version = max(old_version, row[1])
        except Exception:
            pass

    knowledge_ids = []
    for abstract in l0_lines:
        action = soft_dedup(abstract, source_type="knowledge", db=gdb)
        if action == "insert":
            cur = gdb.execute(
                "INSERT INTO abstracts (source_type, abstract, category, tags, storage_path, version, status) "
                "VALUES ('knowledge', ?, '知识库', ?, ?, ?, 'active')",
                (abstract, knowledge_fields.get("tags", ""), l1_path, old_version + 1 if topic else 1),
            )
            aid = cur.lastrowid
        else:
            existing = gdb.execute(
                "SELECT id FROM abstracts WHERE source_type='knowledge' AND abstract LIKE ? LIMIT 1",
                (abstract[:20] + "%",),
            ).fetchone()
            if existing:
                aid = existing["id"]
                gdb.execute(
                    "UPDATE abstracts SET tags=?, storage_path=?, status='active' WHERE id=?",
                    (knowledge_fields.get("tags", ""), l1_path, aid),
                )
            else:
                cur = gdb.execute(
                    "INSERT INTO abstracts (source_type, abstract, category, tags, storage_path, version, status) "
                    "VALUES ('knowledge', ?, '知识库', ?, ?, ?, 'active')",
                    (abstract, knowledge_fields.get("tags", ""), l1_path, old_version + 1 if topic else 1),
                )
                aid = cur.lastrowid
        knowledge_ids.append(aid)

    # 二击保险：根据关系和状态处理旧知识
    need_supersede = relation in ("replaces", "refines")
    if topic and old_ids:
        if need_supersede and already_downgraded:
            # 第二次确认 → 真正 supersede
            for old_id in old_ids:
                _supersede_record(gdb, old_id, knowledge_ids[0] if knowledge_ids else None)
        elif need_supersede:
            # 第一次 → 降级为 downgraded（保险期）
            for old_id in old_ids:
                gdb.execute("UPDATE abstracts SET status='downgraded' WHERE id=?", (old_id,))

    # 写入 knowledge_meta（含 topic + relation）
    for aid in knowledge_ids:
        gdb.execute(
            "INSERT OR REPLACE INTO knowledge_meta "
            "(abstract_id, brand_name, brand_tier, category_l1, category_l2, target_audience, audience_tag, topic, relation_to_prev) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (aid,
             knowledge_fields.get("brand_name", ""),
             knowledge_fields.get("brand_tier", ""),
             knowledge_fields.get("category_l1", ""),
             knowledge_fields.get("category_l2", ""),
             knowledge_fields.get("target_audience", ""),
             knowledge_fields.get("audience_tag", ""),
             topic,
             relation),
        )

    # 更新 categories/（知识库独立分类）
    cat_name = knowledge_fields.get("category_l1", "知识库") or "知识库"
    cat_name = cat_name.replace("/", "_").replace("\\", "_").strip()
    cat_dir = KNOWLEDGE_BASE / "categories"
    cat_dir.mkdir(parents=True, exist_ok=True)
    cat_file = cat_dir / f"{cat_name}.md"
    if not cat_file.exists():
        cat_file.write_text(f"# {cat_name}\n\n{now} 创建\n\n- {slug}\n", encoding="utf-8")
    else:
        content = cat_file.read_text(encoding="utf-8")
        entry = f"- {slug}"
        if entry not in content:
            with open(cat_file, "a", encoding="utf-8") as f:
                f.write(f"{entry}\n")

    gdb.commit()
    gdb.close()
    return knowledge_ids

EXTRACT_SYSTEM_V2 = """你是记忆提取引擎。从压缩文本中提取结构化信息、标签、类别和语义关联。

输出格式（请严格遵循）：

【类别】类别名称（如：项目开发/薪酬分析/数据处理/工具配置/内容创作）
【profile_tag】工作 或 个人
【project】项目代号（如：hr-efficiency / payroll / memory-vault / xiaohongshu）
【project_type】项目类型（如：系统开发 / 工具配置 / 数据分析 / 内容创作）
|【decision_type】决策 或 踩坑 或 经验 或 待办
|【importance】high 或 medium 或 low
|【topic】稳定主题键，格式：{类别}/{project}/{decision_type}（如：工具配置/memory-vault/决策）
|【标签】逗号分隔的关键词标签（3-5 个）

【L0】
- 主题: 一句话核心结论（<20字）
- 主题: 一句话核心结论（<20字）
... 3-5 条

【L1】
## 核心主题
## 关键决策
## 踩坑记录
## 结论与待办

【决策匹配】
逐条对比已有决策：
- 匹配: slug名称（补充了新细节：xxx）
- 匹配: slug名称（无新变化）
- 关联: slug名称（语义相关，不是同一决策但有参考价值）
- 新增: slug-name（说明）

不添加礼貌语、不添加emoji。纯中文输出。"""

EXTRACT_USER_TEMPLATE_V2 = """## 现有决策（如果重复请匹配，关联请标注）
{decisions}

## 压缩文本
{compressed}

## 要求
分析压缩文本的主题，确定【类别】、【profile_tag】、【project】、【project_type】、【decision_type】、【importance】。
生成 3-5 条 L0 摘要、L1 结构化摘要。
逐条对比已有决策：匹配、关联或新增。"""


# ── 主流程 ─────────────────────────────────────────

def main():
    import argparse
    ap = argparse.ArgumentParser(description="v2 记忆注入")
    ap.add_argument("--source", choices=["session", "doc"], default="session")
    ap.add_argument("--file", help="直接指定输入文件（支持任意 JSON 格式的 messages 数组）")
    ap.add_argument("--session-dir",
                    default=str(Path.home() / ".hermes" / "webui" / "sessions"),
                    help="Hermes 会话目录（默认 ~/.hermes/webui/sessions/，--source session 且未指定 --file 时使用）")
    ap.add_argument("--slug", help="自定义 slug")
    ap.add_argument("--profile", default=CURRENT_PROFILE, help="注入到哪个 profile（默认 current）")
    ap.add_argument("--category", help="手动指定类别（覆盖自动归类）")
    args = ap.parse_args()

    # doc 注入：直接处理，不调 remember_doc.py（避免循环）
    if args.source == "doc":
        if not args.file:
            print("❌ --source doc 需要 --file 参数", file=sys.stderr)
            sys.exit(1)
        file_path = Path(args.file)
        if not file_path.exists():
            print(f"❌ 文件不存在: {file_path}", file=sys.stderr)
            sys.exit(1)
        slug = args.slug or file_path.stem.replace(" ", "_")
        input_text = file_path.read_text(encoding="utf-8", errors="ignore")
        if not input_text.strip():
            print("⏭ 文件为空，跳过", file=sys.stderr)
            sys.exit(0)
        print(f"📄 读取文档: {file_path.name} ({len(input_text)} 字符)", file=sys.stderr)

        # 输入的 .md 文件本身 → L2（格式转换后的原文）
        now = datetime.now().strftime("%Y-%m-%d")
        l2_dir = KNOWLEDGE_BASE / "storage" / "docs"
        l2_dir.mkdir(parents=True, exist_ok=True)
        l2_path = str(l2_dir / f"{now}_{slug}.md")
        Path(l2_path).write_text(input_text, encoding="utf-8")
        print(f"  ✅ L2 → {l2_path}", file=sys.stderr)

        # deepseek 压缩 → L1（对 L2 归纳总结）
        print("☁️  DeepSeek 压缩文档中...", file=sys.stderr)
        compressed = deepseek_compress(input_text, "doc")

        # L1 存储到 overviews/docs/
        l1_dir = KNOWLEDGE_BASE / "overviews" / "docs"
        l1_dir.mkdir(parents=True, exist_ok=True)
        l1_path = str(l1_dir / f"{now}_{slug}.md")
        Path(l1_path).write_text(f"# 文档归纳 — {now}\n\n{compressed}", encoding="utf-8")
        print(f"  ✅ L1 → {l1_path}", file=sys.stderr)

        # qwen 从 L1 提炼 → L0
        print("🖥️  本地模型提取知识结构...", file=sys.stderr)
        system = EXTRACT_SYSTEM_DOC
        user = EXTRACT_USER_TEMPLATE_DOC.format(compressed=compressed)
        raw = lmstudio_chat(system, user)
        l0, l1_detail, knowledge_fields = parse_knowledge(raw)

        # 写入 abstracts.db（L0）
        knowledge_ids = write_knowledge_l0_l1(l0, l1_detail, knowledge_fields, slug, l1_path)

        # ── 生成并存储知识向量 ──
        if knowledge_ids:
            kdb = get_knowledge_db()
            for kid, l0_line in zip(knowledge_ids, l0):
                try:
                    vec = generate_embedding(l0_line)
                    store_embedding(kdb, kid, "knowledge", vec)
                except Exception as e:
                    logger.warning("知识 Embedding 失败 (id=%d): %s", kid, e)
                    continue
            kdb.close()

        # ── 传递闭包推理（知识） ──
        if knowledge_ids:
            kdb2 = get_knowledge_db()
            if kdb2:
                _infer_transitive_links(kdb2, knowledge_ids)
                kdb2.commit()
                kdb2.close()
                print(f"  🔗 knowledge_links 推理 → {len(knowledge_ids)} 条", file=sys.stderr)

        # 输出结果
        print(f"  ✅ L0 → {len(l0)} 条 (source_type=knowledge)", file=sys.stderr)
        if knowledge_fields:
            print(f"  ✅ knowledge_meta → {knowledge_fields}", file=sys.stderr)

        print("\n📤 注入结果:", file=sys.stderr)
        result = subprocess.run(
            ["python3", str(ROOT / "hooks" / "auto.py"), "inject", "--source", "doc", "--profile", "default"],
            capture_output=True, text=True, timeout=10,
        )
        print(result.stdout)
        return

    # 检查压缩模型配置
    from client import resolve_provider
    try:
        cfg = resolve_provider("compress")
        compress_ok = bool(cfg.get("api_key") or cfg.get("base_url"))
        if not compress_ok:
            print("❌ 压缩模型未配置完整（缺少 api_key 或 base_url）", file=sys.stderr)
            sys.exit(1)
    except Exception as e:
        print(f"❌ 配置读取失败: {e}", file=sys.stderr)
        sys.exit(1)

    profile = args.profile or CURRENT_PROFILE  # noqa: F841
    # 快速检查 LM Studio 是否在线（非致命，仅跳过本地模型步骤）
    lmstudio_ok = True
    try:
        import requests as _req
        _probe = _req.get("http://localhost:1234/v1/models", timeout=3)
        if _probe.status_code != 200:
            lmstudio_ok = False
    except Exception:
        lmstudio_ok = False
    if not lmstudio_ok:
        print("⚠️  LM Studio 不可达，跳过本地模型提取（L2 已保存，后续可补提）", file=sys.stderr)
        logger.warning("LM Studio 不可达，跳过本地模型步骤")
    else:
        logger.info("LM Studio 在线检查通过")

    profile = args.profile or CURRENT_PROFILE
    ensure_profile_dirs(profile)

    try:
        # ── 输入 ──
        logger.info("开始 v2 注入: profile=%s", profile)

        if args.file and args.source == "doc":
            # 文档模式
            file_path = Path(args.file)
            if not file_path.exists():
                print(f"❌ 文件不存在: {args.file}", file=sys.stderr)
                sys.exit(1)
            slug = args.slug or file_path.stem.replace(" ", "_")
            input_text = file_path.read_text(encoding="utf-8")

        elif args.file and args.source == "session":
            # 指定 JSON 文件（通用模式，不依赖 Hermes）
            print(f"📖 读取文件: {args.file}", file=sys.stderr)
            try:
                data = json.loads(Path(args.file).read_text(encoding="utf-8"))
                input_text = extract_conversation(data)
                slug = args.slug or Path(args.file).stem.replace(" ", "_")
            except Exception as e:
                print(f"❌ 文件读取失败: {e}", file=sys.stderr)
                sys.exit(1)

        else:
            # 默认：从会话目录取最新（Hermes WebUI 兼容）
            print("📖 读取最新会话...", file=sys.stderr)
            session = find_latest_session(args.session_dir)
            if not session:
                print("❌ 未找到会话文件（可通过 --file 直接指定 JSON 文件）", file=sys.stderr)
                sys.exit(1)
            slug = args.slug or session.get("session_id", "unknown")
            input_text = extract_conversation(session)
        if len(input_text) > 300000:
            print(f"  会话过长 ({len(input_text)} 字符)，截断至 300000 字符", file=sys.stderr)
            input_text = input_text[:300000]
        if not input_text.strip():
            print("⏭ 会话为空，跳过", file=sys.stderr)
            sys.exit(0)
        print(f"  会话: {slug} ({len(input_text)} 字符)", file=sys.stderr)

        # ── ② deepseek 压缩 → L2 ──
        print("☁️  DeepSeek 压缩会话中...", file=sys.stderr)
        compressed = deepseek_compress(input_text, "session")
        l2_path = write_l2(compressed, slug, profile)
        print(f"  ✅ L2 → {l2_path}", file=sys.stderr)

        # ── ③ 本地模型提取结构（仅 LM Studio 在线时执行） ──
        abstract_ids = []
        if lmstudio_ok:
            print("🖥️  本地模型提取结构...", file=sys.stderr)
            gdb = get_global_db()
            existing = gdb.execute("SELECT slug, content FROM decisions ORDER BY id").fetchall()
            gdb.close()
            decisions_text = "\n".join(
                f"{chr(0x2460 + i)} {r['slug']}: {r['content']}"
                for i, r in enumerate(existing[:50])
            )

            user = EXTRACT_USER_TEMPLATE_V2.format(
                decisions=decisions_text or "（暂无）",
                compressed=compressed,
            )
            raw = lmstudio_chat(EXTRACT_SYSTEM_V2, user)
            print(f"  📝 qwen 输出 ({len(raw)} 字符)", file=sys.stderr)

            # ── 解析 ──
            fields = parse_structured_fields(raw)
            l0, l1, decisions = parse_l0_l1(raw)

            # 手动指定类别可覆盖自动归类
            if args.category:
                fields["category"] = args.category

            # 写入专属库
            print("  💾 写入专属库...", file=sys.stderr)
            l1_path = write_l1(l1, slug, profile)
            abstract_ids = write_profile_l0_and_meta(l0, fields, slug, l1_path, profile)

            # 同步全局库
            print("  🌐 同步全局库...", file=sys.stderr)
            try:
                global_ids = write_global_l0(abstract_ids, l0, fields, slug, l1_path, profile)

                # ── 生成并存储向量 ──
                if global_ids:
                    print("  🧮 生成语义向量...", file=sys.stderr)
                    gdb = get_global_db()
                    for i, (gid, abstract) in enumerate(zip(global_ids, l0)):
                        try:
                            vec = generate_embedding(abstract)
                            store_embedding(gdb, gid, "memory", vec)
                        except Exception as e:
                            logger.warning("Embedding 失败 (id=%d): %s", gid, e)
                            continue
                    gdb.close()
                    print(f"  ✅ embedding → {len(global_ids)} 条", file=sys.stderr)

                # 写入 decisions（inline 写入全局库）
                if decisions:
                    gdb = get_global_db()
                    for line in decisions:
                        m = re.search(r"新增[：:]\s*(.+?)(?:\s*[（(]|\s*$)", line)
                        if m:
                            slug_name = m.group(1).strip()
                            desc = line.split("）")[0].split("(")[-1] if "(" in line else ""
                            gdb.execute(
                                "INSERT OR IGNORE INTO decisions (slug, category, content, source_sessions) "
                                "VALUES (?, '', ?, json_array(?))",
                                (slug_name, desc or line, slug),
                            )
                    gdb.commit()
                    gdb.close()

                # ── 链写入（关联解析 + tag 重叠 + 传递闭包） ──
                write_memory_links(abstract_ids, global_ids, decisions, profile)

                # ── 权重更新 ──
                gdb = get_global_db()
                gdb.execute("UPDATE abstracts SET session_count = session_count + 1 "
                            "WHERE source_type='memory' AND session_count >= 0")
                if abstract_ids:
                    ids_placeholder = ",".join("?" * len(abstract_ids))
                    gdb.execute(
                        f"UPDATE abstracts SET weight = MIN(weight + 5, 100), "
                        f"last_accessed_at = datetime('now','localtime') "
                        f"WHERE id IN ({ids_placeholder}) AND source_type='memory'",
                        abstract_ids
                    )
                gdb.execute(
                    "UPDATE abstracts SET weight = MAX(weight - 2, 5) "
                    "WHERE source_type='memory' AND session_count >= 3 "
                    "AND last_accessed_at IS NULL"
                )
                gdb.execute(
                    "UPDATE abstracts SET weight = MAX(weight - 5, 5) "
                    "WHERE source_type='memory' AND session_count >= 3 "
                    "AND last_accessed_at IS NOT NULL "
                    "AND last_accessed_at < datetime('now', '-30 days')"
                )
                gdb.commit()
                gdb.close()
                logger.info("权重更新完成: session_count +1, weight adjusted")

                # ── 生命周期管理（增量追加） ──
                try:
                    from lifecycle import run as lifecycle_run
                    new_abstracts_dict = dict(zip(abstract_ids, l0))
                    lifecycle_run(
                        profile=profile,
                        new_abstracts=new_abstracts_dict,
                        new_global_ids=global_ids,
                        referenced_ids=abstract_ids,
                    )
                    logger.info("生命周期管理完成")
                except Exception as e:
                    logger.warning("生命周期管理跳过（非致命）: %s", e)
            except Exception as e:
                logger.error("全局库同步失败（专属库已写入）: %s", e, exc_info=True)
                print(f"  ⚠️  全局库同步失败，专属库已安全写入: {e}", file=sys.stderr)
                print(f"  💡 可执行注入以重试同步", file=sys.stderr)

            print(f"  ✅ L0 → {len(l0)} 条", file=sys.stderr)
            print(f"  ✅ L1 → {len(l1)} 字符", file=sys.stderr)
            print(f"  ✅ decisions → {len(decisions)} 条", file=sys.stderr)

        # ── 输出注入结果 ──
        print("\n📤 注入结果:", file=sys.stderr)
        from retriever import auto_inject
        result = auto_inject(source_type="memory")
        print(result)
        if lmstudio_ok:
            logger.info("v2 注入完成: profile=%s, L0=%d, decisions=%d", profile, len(l0), len(decisions))
        else:
            print("  ⏭ L0/L1/decisions 跳过（LM Studio 不可达），L2 已安全保存", file=sys.stderr)

    except Exception as e:
        logger.error("注入失败: %s", e, exc_info=True)
        print(f"❌ 注入失败: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
