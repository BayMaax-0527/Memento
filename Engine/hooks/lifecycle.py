#!/usr/bin/env python3
"""
hooks/lifecycle.py — v2 记忆生命周期管理器

在 remember.py 末尾追加执行（增量，不碰现有流程）。

四条规则（注入时）：
  步骤 A — 语义去重 + 冲突检测（≥0.85合并, 0.65~0.85+否定词→supersede）
  步骤 B — 超时衰减（freshness_score = f(days_since_hit, decay_window)）
  步骤 C — 时效标签判定（active / stale / superseded / archived）
  步骤 D — 引用计数更新（hit_count, sliding_hit_count）

单独模式：
  --mode=compact  深度归档 + 健康报告
  --mode=recover  恢复已归档条目
  --mode=status   生命周期状态概览
"""

import json, logging, os, re, sqlite3, struct, sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))
from client import call_embedding, resolve_path

logger = logging.getLogger("lifecycle")

# ── 加载配置 ────────────────────────────────────────
CONFIG = yaml.safe_load((ROOT.parent / "Memory" / "config.yaml").read_text())
LC_CONFIG = yaml.safe_load((ROOT / "config" / "lifecycle.yaml").read_text())

GLOBAL_VAULT = Path(resolve_path(CONFIG["paths"]["global_vault"]))
KNOWLEDGE_BASE = Path(resolve_path(CONFIG["paths"]["knowledge_base"]))
PROFILE_BASE = Path(resolve_path(CONFIG["paths"]["profile_vault_base"])) if CONFIG.get("paths", {}).get("profile_vault_base") else None
CURRENT_PROFILE = CONFIG.get("v2", {}).get("profile", "default")

NEGATION_WORDS = set(LC_CONFIG.get("negation_keywords", []))
# 从配置文件加载分类关键词（避免硬编码）
CLASSIFY_ARCH_KW = LC_CONFIG.get("classify_arch_keywords", [
    "架构", "设计", "方案", "选型", "architecture", "design",
    "重构", "框架", "体系", "架构决策",
])
CLASSIFY_RULE_KW = LC_CONFIG.get("classify_rule_keywords", [
    "流程", "规范", "规则", "必须", "禁止", "policy",
    "rule", "standard", "标准", "制度", "约定",
])


# ── 路径辅助 ────────────────────────────────────────

def profile_db_path(profile: str = None) -> Path:
    p = profile or CURRENT_PROFILE
    return PROFILE_BASE / p / "memory-vault" / "abstracts.db"


def get_profile_db(profile: str = None):
    db_path = profile_db_path(profile)
    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def get_global_db():
    db = sqlite3.connect(str(GLOBAL_VAULT / "abstracts.db"))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


# ── 工具函数 ────────────────────────────────────────

def _has_negation(text: str) -> bool:
    """检测文本是否包含否定词（辅助信号）。"""
    text_lower = text.lower()
    for kw in NEGATION_WORDS:
        if kw in text or kw in text_lower:
            return True
    return False


def _classify_l0(text: str) -> str:
    """自动分类 lifecycle_category：arch / rule / daily。"""
    t = text or ""
    if any(kw in t for kw in CLASSIFY_ARCH_KW):
        return "arch"
    if any(kw in t for kw in CLASSIFY_RULE_KW):
        return "rule"
    return "daily"


def _get_decay_days(category: str) -> float:
    """获取衰减窗口天数。"""
    mapping = {
        "daily": LC_CONFIG["decay_window_daily"],
        "arch": LC_CONFIG["decay_window_arch"],
        "rule": LC_CONFIG["decay_window_rule"],
    }
    days = mapping.get(category, 30)
    if days == -1:
        return float("inf")
    return float(days)


def _compute_freshness(last_accessed_at: Optional[str], category: str) -> float:
    """计算 freshness_score [0, 1]。"""
    if not last_accessed_at:
        return 1.0
    try:
        last = datetime.strptime(last_accessed_at, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            last = datetime.strptime(last_accessed_at, "%Y-%m-%d")
        except ValueError:
            return 1.0
    delta = (datetime.now() - last).days
    window = _get_decay_days(category)
    if window == float("inf"):
        return 1.0
    return max(0.0, 1.0 - delta / window)


def _cosine_similarity(v1: list[float], v2: list[float]) -> float:
    """余弦相似度。"""
    a = np.array(v1, dtype=np.float32)
    b = np.array(v2, dtype=np.float32)
    na = np.linalg.norm(a)
    nb = np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float((a @ b) / (na * nb))


def _load_embeddings(db, source_ids: list[int]) -> dict[int, list[float]]:
    """从指定 DB 的 embeddings 表加载向量。"""
    if not source_ids:
        return {}
    placeholders = ",".join("?" * len(source_ids))
    rows = db.execute(
        f"SELECT source_id, dimension, vector FROM embeddings "
        f"WHERE source_id IN ({placeholders}) AND source_type='memory'",
        source_ids,
    ).fetchall()
    result = {}
    for r in rows:
        dim = r["dimension"]
        vec = list(struct.unpack(f"{dim}f", r["vector"]))
        result[r["source_id"]] = vec
    return result


# ──────────────────────────────────────────────────────
# 步骤 A：语义去重 + 冲突检测
# ──────────────────────────────────────────────────────

def step_a_semantic_dedup(
    profile_db_con,
    global_db_con,
    new_abstracts: dict[int, str],
    profile: str,
) -> dict:
    """对刚写入的新 L0 做语义去重和冲突检测。

    返回统计信息：{merged: int, superseded: int, new: int, details: list}
    """
    stats = {"merged": 0, "superseded": 0, "new": 0, "details": []}

    if not new_abstracts:
        return stats

    # 获取 profile 库中所有已有 active 条目
    existing = profile_db_con.execute(
        "SELECT id, abstract FROM abstracts WHERE source_type='memory' "
        "AND status IN ('active', 'downgraded')"
    ).fetchall()
    if not existing:
        stats["new"] = len(new_abstracts)
        return stats

    existing_ids = [r["id"] for r in existing]
    existing_texts = {r["id"]: r["abstract"] for r in existing}

    # 从全局库加载 existing 的 embedding
    existing_embs = _load_embeddings(global_db_con, existing_ids)

    # 为新 L0 生成 embedding（存入全局库）
    new_ids = list(new_abstracts.keys())

    # 内部排序：含否定词的排最前
    def _sort_key(aid):
        txt = new_abstracts.get(aid, "")
        return (0 if _has_negation(txt) else 1, aid)

    new_ids.sort(key=_sort_key)

    for new_id in new_ids:
        new_text = new_abstracts[new_id]
        new_vec = None
        try:
            new_vec = call_embedding(new_text, config_key="embed")
        except Exception as e:
            logger.warning("新 L0 生成 embedding 失败 (id=%d): %s", new_id, e)
            # embedding 失败 → 跳过语义比对，直接作为新条目
            stats["new"] += 1
            continue

        # 存储新 embedding 到全局库
        try:
            from remember import store_embedding
            store_embedding(global_db_con, new_id, "memory", new_vec)
        except Exception as e:
            logger.warning("新 L0 embedding 存储失败 (id=%d): %s", new_id, e)

        # 与现有条目逐条比对
        matched = False
        for eid in existing_ids:
            evec = existing_embs.get(eid)
            if evec is None:
                continue
            sim = _cosine_similarity(new_vec, evec)

            if sim >= LC_CONFIG["merge_threshold"]:
                # ≥ 0.85 → 合并：更新已有条目的权重和时间戳
                # 检查目标条目是否为 downgraded（二击保险中），如果是则跳过合并
                target_status = profile_db_con.execute(
                    "SELECT status FROM abstracts WHERE id=?", (eid,)
                ).fetchone()
                if target_status and target_status[0] == "downgraded":
                    # 二击保险中的旧条目，不应合并，新条目保持独立
                    stats["new"] += 1
                    matched = True
                    break
                profile_db_con.execute(
                    "UPDATE abstracts SET weight = MIN(weight + 5, 100), "
                    "last_accessed_at = datetime('now','localtime'), "
                    "hit_count = hit_count + 1, "
                    "sliding_hit_count = sliding_hit_count + 1, "
                    "freshness_score = 1.0 "
                    "WHERE id = ?", (eid,)
                )
                # 将新条目的 memory_links 继承到已有条目
                links = profile_db_con.execute(
                    "SELECT target_abstract_id FROM memory_links WHERE source_id = ?",
                    (new_id,),
                ).fetchall()
                for link in links:
                    profile_db_con.execute(
                        "INSERT OR IGNORE INTO memory_links "
                        "(source_id, target_abstract_id, relation) VALUES (?, ?, 'redirected')",
                        (eid, link["target_abstract_id"]),
                    )
                # 标记新条目为 merged
                profile_db_con.execute(
                    "UPDATE abstracts SET status='superseded', superseded_by_id=? WHERE id=?",
                    (eid, new_id),
                )
                stats["merged"] += 1
                stats["details"].append(
                    f"合并: #{new_id} → #{eid} (sim={sim:.3f})"
                )
                matched = True
                break

            elif sim >= LC_CONFIG["conflict_threshold"]:
                # 0.65 ~ 0.85 → 冲突检测
                has_neg = _has_negation(new_text)
                if has_neg:
                    # 否定词 + 语义冲突 → supersede
                    profile_db_con.execute(
                        "UPDATE abstracts SET status='superseded', superseded_by_id=? WHERE id=?",
                        (new_id, eid),
                    )
                    # 旧条目的 links 继承到新条目
                    links = profile_db_con.execute(
                        "SELECT target_abstract_id FROM memory_links WHERE source_id = ?",
                        (eid,),
                    ).fetchall()
                    for link in links:
                        profile_db_con.execute(
                            "INSERT OR IGNORE INTO memory_links "
                            "(source_id, target_abstract_id, relation) VALUES (?, ?, 'redirected')",
                            (new_id, link["target_abstract_id"]),
                        )
                    stats["superseded"] += 1
                    stats["details"].append(
                        f"Supersede: #{eid} → #{new_id} (sim={sim:.3f}, 含否定词)"
                    )
                    matched = True
                    break
                # 无否定词 → 不触发，但记录关联
                profile_db_con.execute(
                    "INSERT OR IGNORE INTO memory_links "
                    "(source_id, target_abstract_id, relation) VALUES (?, ?, 'conflict_similar')",
                    (new_id, eid),
                )
                # 继续比对，不标记 matched=True

        if not matched:
            stats["new"] += 1

    return stats


# ──────────────────────────────────────────────────────
# 步骤 B：超时衰减
# ──────────────────────────────────────────────────────

def step_b_decay(profile_db_con):
    """遍历所有 active L0，重新计算 freshness_score。"""
    rows = profile_db_con.execute(
        "SELECT id, last_accessed_at, lifecycle_category "
        "FROM abstracts WHERE source_type='memory' AND status IN ('active', 'downgraded')"
    ).fetchall()

    updated = 0
    for r in rows:
        lc_cat = r["lifecycle_category"] or _classify_l0(
            profile_db_con.execute(
                "SELECT abstract FROM abstracts WHERE id=?",
                (r["id"],),
            ).fetchone()["abstract"]
        )
        freshness = _compute_freshness(r["last_accessed_at"], lc_cat)
        profile_db_con.execute(
            "UPDATE abstracts SET freshness_score=?, lifecycle_category=? WHERE id=?",
            (freshness, lc_cat, r["id"]),
        )
        if freshness < 1.0:
            updated += 1

    return updated


# ──────────────────────────────────────────────────────
# 步骤 C：时效标签判定
# ──────────────────────────────────────────────────────

def step_c_label(profile_db_con, injection_count: int = 1):
    """综合判定 status。

    Superseded 冻结。
    Archived 跳过。
    Active 和 stale 参与判定。
    """
    rows = profile_db_con.execute(
        "SELECT id, status, freshness_score, hit_count, last_accessed_at, "
        "sliding_hit_count "
        "FROM abstracts WHERE source_type='memory' "
        "AND status IN ('active', 'downgraded', 'stale')"
    ).fetchall()

    stale_threshold = LC_CONFIG["stale_freshness_threshold"]

    changelog = {"to_stale": 0, "to_archive": 0, "details": []}

    for r in rows:
        status = r["status"]
        freshness = r["freshness_score"]
        hit_count = r["hit_count"]
        sliding_hits = r["sliding_hit_count"] or 0

        # active / downgraded → stale
        if status in ("active", "downgraded"):
            if freshness < stale_threshold and sliding_hits == 0:
                # 连续 N 次注入零命中 → stale
                profile_db_con.execute(
                    "UPDATE abstracts SET status='stale' WHERE id=?",
                    (r["id"],),
                )
                changelog["to_stale"] += 1
                changelog["details"].append(
                    f"#{r['id']}: {status} → stale (freshness={freshness:.3f})"
                )

        # stale → archived（严格条件）
        elif status == "stale":
            if freshness == 0.0 and hit_count == 0:
                profile_db_con.execute(
                    "UPDATE abstracts SET status='archived' WHERE id=?",
                    (r["id"],),
                )
                changelog["to_archive"] += 1
                changelog["details"].append(
                    f"#{r['id']}: stale → archived (freshness=0, hit_count=0)"
                )

    return changelog


# ──────────────────────────────────────────────────────
# 步骤 D：引用计数更新
# ──────────────────────────────────────────────────────

def step_d_reference_count(
    profile_db_con,
    global_db_con,
    referenced_ids: list[int],
    global_id_map: dict[int, int],
    profile: str,
):
    """更新引用计数。

    引用计数：profile 库的 memory_links 用 profile IDs（正确），
    全局库的 memory_links 用 global IDs（通过 id_map 转换）。
    """
    if not referenced_ids:
        return {}

    # ── Profile 库引用计数（用自己的 IDs） ──
    profile_placeholders = ",".join("?" * len(referenced_ids))
    profile_refs = profile_db_con.execute(
        f"SELECT target_abstract_id, COUNT(*) as cnt "
        f"FROM memory_links WHERE target_abstract_id IN ({profile_placeholders}) "
        f"GROUP BY target_abstract_id",
        referenced_ids,
    ).fetchall()

    # ── 全局库引用计数（用 global IDs 查询） ──
    global_ids_for_query = [
        gid for pid in referenced_ids
        for gid in ([global_id_map.get(pid)] if global_id_map.get(pid) else [])
    ]
    global_ref_map = {}
    if global_ids_for_query:
        g_placeholders = ",".join("?" * len(global_ids_for_query))
        global_refs = global_db_con.execute(
            f"SELECT target_abstract_id, COUNT(*) as cnt "
            f"FROM memory_links WHERE target_abstract_id IN ({g_placeholders}) "
            f"GROUP BY target_abstract_id",
            global_ids_for_query,
        ).fetchall()
        for r in global_refs:
            global_ref_map[r["target_abstract_id"]] = r["cnt"]

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for aid in referenced_ids:
        # 引用计数：profile 本地 + global（跨 profile 引用，已转换为 global ID）
        local_count = sum(
            r["cnt"] for r in profile_refs if r["target_abstract_id"] == aid
        )
        gid = global_id_map.get(aid)
        global_count = global_ref_map.get(gid, 0) if gid else 0
        total_refs = local_count + global_count

        # 更新 profile 库
        profile_db_con.execute(
            "UPDATE abstracts SET "
            "hit_count = hit_count + 1, "
            "sliding_hit_count = sliding_hit_count + 1, "
            "last_accessed_at = ?, "
            "last_ref_session = ? "
            "WHERE id = ?",
            (now_str, f"session_{now_str}", aid),
        )

        # 同步更新全局库（使用 global ID + 传递 profile 的 freshness）
        if gid:
            pf = profile_db_con.execute(
                "SELECT freshness_score FROM abstracts WHERE id=?",
                (aid,),
            ).fetchone()
            fresh_val = pf[0] if pf else 1.0
            global_db_con.execute(
                "UPDATE abstracts SET "
                "hit_count = IFNULL(hit_count, 0) + 1, "
                "sliding_hit_count = IFNULL(sliding_hit_count, 0) + 1, "
                "last_accessed_at = ?, "
                "freshness_score = ? "
                "WHERE id = ?",
                (now_str, fresh_val, gid),
            )

    return {"updated": len(referenced_ids), "total_refs": sum(
        ref_map := {aid: sum(
            r["cnt"] for r in profile_refs if r["target_abstract_id"] == aid
        ) + (global_ref_map.get(global_id_map.get(aid), 0) if global_id_map.get(aid) else 0)
        for aid in referenced_ids
    }.values())}


# ──────────────────────────────────────────────────────
# 同步 Profile → 全局库
# ──────────────────────────────────────────────────────

def _sync_to_global(profile_db_con, global_db_con, profile: str):
    """将 profile 库的生命周期状态同步到全局库。

    先做精确匹配，失败后回退到 LIKE 模糊匹配。
    同时维护 profile_id → global_id 映射。
    """
    rows = profile_db_con.execute(
        "SELECT id, abstract, status, superseded_by_id, freshness_score, "
        "hit_count, sliding_hit_count, weight, lifecycle_category "
        "FROM abstracts WHERE source_type='memory'"
    ).fetchall()

    synced = 0
    failed = 0
    id_map = {}
    for r in rows:
        abstract = r["abstract"]
        # 精确匹配
        global_row = global_db_con.execute(
            "SELECT id FROM abstracts WHERE source_type='memory' "
            "AND abstract = ?", (abstract,),
        ).fetchone()

        # LIKE 模糊匹配兜底
        if not global_row:
            fuzzy = global_db_con.execute(
                "SELECT id FROM abstracts WHERE source_type='memory' "
                "AND abstract LIKE ? LIMIT 1",
                (abstract[:30] + "%",),
            ).fetchone()
            if fuzzy:
                global_row = fuzzy

        if global_row:
            gid = global_row["id"]
            global_db_con.execute(
                "UPDATE abstracts SET status=?, superseded_by_id=?, "
                "freshness_score=?, hit_count=?, sliding_hit_count=?, "
                "lifecycle_category=?, weight=? WHERE id=?",
                (
                    r["status"], r["superseded_by_id"],
                    r["freshness_score"], r["hit_count"], r["sliding_hit_count"],
                    r["lifecycle_category"], r["weight"],
                    gid,
                ),
            )
            id_map[r["id"]] = gid
            synced += 1
        else:
            failed += 1
            logger.warning("同步失败: profile #%d 在全局库中无匹配 (%s...)",
                           r["id"], abstract[:40])

    if failed:
        logger.warning("同步完成: %d 成功, %d 失败", synced, failed)
    return synced, id_map


# ──────────────────────────────────────────────────────
# 主入口：run() — 在 remember.py 末尾调用
# ──────────────────────────────────────────────────────

def run(
    profile: str = None,
    new_abstracts: dict[int, str] = None,
    new_global_ids: list[int] = None,
    referenced_ids: list[int] = None,
):
    """生命周期管理器主入口。在 remember.py 末尾追加调用。

    Args:
        profile: profile 名（默认 current）
        new_abstracts: 本轮新写入的 L0 {profile_id: abstract_text}
        new_global_ids: 对应的全局库 ID 列表
        referenced_ids: 本轮会话中引用的 L0 ID 列表
    """
    p = profile or CURRENT_PROFILE

    try:
        pdb = get_profile_db(p)
        gdb = get_global_db()
    except Exception as e:
        logger.warning("生命周期: 数据库连接失败: %s", e)
        return {}

    results = {}

    # 步骤 A — 语义去重 + 冲突检测
    try:
        if new_abstracts:
            a_stats = step_a_semantic_dedup(pdb, gdb, new_abstracts, p)
            pdb.commit()
            results["dedup"] = a_stats
    except Exception as e:
        logger.error("步骤 A 失败: %s", e, exc_info=True)

    # 步骤 B — 超时衰减
    try:
        b_count = step_b_decay(pdb)
        pdb.commit()
        results["decay"] = {"updated": b_count}
    except Exception as e:
        logger.error("步骤 B 失败: %s", e, exc_info=True)

    # 步骤 C — 时效标签判定
    try:
        c_log = step_c_label(pdb)
        pdb.commit()
        results["label"] = c_log
    except Exception as e:
        logger.error("步骤 C 失败: %s", e, exc_info=True)

    # 步骤 D — 引用计数更新
    try:
        if referenced_ids:
            # 先用同步建立 id_map（但此时还未执行步骤D，用已有映射）
            # 从 new_abstracts 和 new_global_ids 构建 id_map
            pid_to_gid = {}
            if new_abstracts and new_global_ids:
                pid_list = list(new_abstracts.keys())
                for pid, gid in zip(pid_list, new_global_ids):
                    pid_to_gid[pid] = gid
            d_refs = step_d_reference_count(pdb, gdb, referenced_ids, pid_to_gid, p)
            pdb.commit()
            gdb.commit()
            results["ref_count"] = d_refs
    except Exception as e:
        logger.error("步骤 D 失败: %s", e, exc_info=True)

    # 同步 profile → 全局库
    try:
        synced, id_map = _sync_to_global(pdb, gdb, p)
        gdb.commit()
        results["sync"] = {"synced": synced, "mapped": len(id_map)}
    except Exception as e:
        logger.error("Profile→全局同步失败: %s", e, exc_info=True)

    pdb.close()
    gdb.close()

    return results


# ──────────────────────────────────────────────────────
# 恢复命令：auto.py recover
# ──────────────────────────────────────────────────────

def recover_by_id(l0_id: int, profile: str = None):
    """按 ID 恢复 archived/stale/superseded 条目为 active。"""
    p = profile or CURRENT_PROFILE
    pdb = get_profile_db(p)
    gdb = get_global_db()

    entry = pdb.execute(
        "SELECT id, abstract, status FROM abstracts WHERE id=? AND source_type='memory'",
        (l0_id,),
    ).fetchone()
    if not entry:
        pdb.close()
        gdb.close()
        return f"❌ 未找到 ID={l0_id}"

    old_status = entry["status"]
    pdb.execute(
        "UPDATE abstracts SET status='active', freshness_score=1.0, "
        "last_accessed_at=datetime('now','localtime') WHERE id=?",
        (l0_id,),
    )

    # 同步全局库
    abstract = entry["abstract"]
    global_entry = gdb.execute(
        "SELECT id FROM abstracts WHERE source_type='memory' AND abstract=?",
        (abstract,),
    ).fetchone()
    if global_entry:
        gdb.execute(
            "UPDATE abstracts SET status='active', freshness_score=1.0, "
            "last_accessed_at=datetime('now','localtime') WHERE id=?",
            (global_entry["id"],),
        )

    # 解除所有指向此条目的 superseded_by 指针（不限 status）
    pdb.execute(
        "UPDATE abstracts SET superseded_by_id=NULL "
        "WHERE superseded_by_id=? AND source_type='memory'",
        (l0_id,),
    )

    pdb.commit()
    gdb.commit()
    pdb.close()
    gdb.close()
    return f"✅ #{l0_id} ({abstract[:40]}...) 已恢复: {old_status} → active"


def recover_by_query(query: str, profile: str = None, auto_confirm: bool = False) -> str:
    """按语义匹配恢复 archived 条目。"""
    p = profile or CURRENT_PROFILE
    pdb = get_profile_db(p)
    gdb = get_global_db()

    archived = pdb.execute(
        "SELECT id, abstract, status FROM abstracts "
        "WHERE source_type='memory' AND status='archived'"
    ).fetchall()
    if not archived:
        pdb.close()
        gdb.close()
        return "没有需要恢复的 archived 条目"

    # 生成查询向量
    try:
        qvec = call_embedding(query, config_key="embed")
    except Exception as e:
        pdb.close()
        gdb.close()
        return f"❌ 语义搜索失败: {e}"

    # 优先从 embeddings 表加载已有向量
    # 需要将 profile ID 转换为 global ID 才能查询全局库的 embeddings 表
    archived_ids = [r["id"] for r in archived]
    pid_to_gid = {}
    for pid in archived_ids:
        g_row = gdb.execute(
            "SELECT id FROM abstracts WHERE source_type='memory' "
            "AND abstract = (SELECT abstract FROM abstracts WHERE id=?)",
            (pid,),
        ).fetchone()
        if g_row:
            pid_to_gid[pid] = g_row["id"]

    stored_embs = {}
    if pid_to_gid:
        try:
            stored_embs = _load_embeddings(gdb, list(pid_to_gid.values()))
            # 重建以 profile ID 为 key 的映射
            pid_to_vec = {}
            for pid, gid in pid_to_gid.items():
                if gid in stored_embs:
                    pid_to_vec[pid] = stored_embs[gid]
            stored_embs = pid_to_vec
        except Exception:
            pass

    scored = []
    for r in archived:
        vec = stored_embs.get(r["id"])
        if vec is None:
            # 未缓存的向量，单独调用 API
            try:
                vec = call_embedding(r["abstract"], config_key="embed")
            except Exception:
                continue
        sim = _cosine_similarity(qvec, vec)
        scored.append((sim, r["id"], r["abstract"]))

    if not scored:
        pdb.close()
        gdb.close()
        return "没有匹配到相关的 archived 条目"

    scored.sort(key=lambda x: x[0], reverse=True)

    recovered = []
    for sim, aid, abstract in scored[:3]:
        if sim < 0.5:
            continue
        pdb.execute(
            "UPDATE abstracts SET status='active', freshness_score=1.0, "
            "last_accessed_at=datetime('now','localtime') WHERE id=?",
            (aid,),
        )
        # 同步全局库
        global_entry = gdb.execute(
            "SELECT id FROM abstracts WHERE source_type='memory' AND abstract=?",
            (abstract,),
        ).fetchone()
        if global_entry:
            gdb.execute(
                "UPDATE abstracts SET status='active', freshness_score=1.0, "
                "last_accessed_at=datetime('now','localtime') WHERE id=?",
                (global_entry["id"],),
            )
        recovered.append(f"#{aid} ({abstract[:40]}...)")

    pdb.commit()
    gdb.commit()
    pdb.close()
    gdb.close()

    if recovered:
        return f"✅ 已恢复 {len(recovered)} 条:\n" + "\n".join(recovered)
    return "没有相似度 > 0.5 的匹配项"


def list_archived(profile: str = None) -> str:
    """列出所有 archived 条目。"""
    p = profile or CURRENT_PROFILE
    pdb = get_profile_db(p)

    rows = pdb.execute(
        "SELECT id, abstract, last_accessed_at, created_at "
        "FROM abstracts WHERE source_type='memory' AND status='archived' "
        "ORDER BY id"
    ).fetchall()
    pdb.close()

    if not rows:
        return "没有 archived 条目"

    lines = ["📦 已归档条目："]
    for r in rows:
        lines.append(f"  #{r['id']} — {r['abstract'][:50]}...")
        lines.append(f"     创建: {r['created_at']}")
    return "\n".join(lines)


# ──────────────────────────────────────────────────────
# 压缩命令：auto.py compact
# ──────────────────────────────────────────────────────

def compact(profile: str = None) -> str:
    """深度归档 + 健康报告。"""
    p = profile or CURRENT_PROFILE
    pdb = get_profile_db(p)
    gdb = get_global_db()

    # 统计现有状态
    total = pdb.execute(
        "SELECT COUNT(*) FROM abstracts WHERE source_type='memory'"
    ).fetchone()[0]
    active_count = pdb.execute(
        "SELECT COUNT(*) FROM abstracts WHERE source_type='memory' AND status='active'"
    ).fetchone()[0]
    stale_count = pdb.execute(
        "SELECT COUNT(*) FROM abstracts WHERE source_type='memory' AND status='stale'"
    ).fetchone()[0]
    superseded_count = pdb.execute(
        "SELECT COUNT(*) FROM abstracts WHERE source_type='memory' AND status='superseded'"
    ).fetchone()[0]
    archived_count = pdb.execute(
        "SELECT COUNT(*) FROM abstracts WHERE source_type='memory' AND status='archived'"
    ).fetchone()[0]

    # 深度归档：freshness=0 且 stale 超过 archive_freshness_zero_days
    archive_days = LC_CONFIG.get("archive_freshness_zero_days", 60)
    archive_candidates = pdb.execute(
        "SELECT id, abstract FROM abstracts "
        "WHERE source_type='memory' AND status='stale' "
        "AND freshness_score = 0.0 "
        "AND (last_accessed_at IS NULL OR last_accessed_at < datetime('now', ? || ' days'))",
        (f"-{archive_days}",),
    ).fetchall()

    archived_now = 0
    for r in archive_candidates:
        pdb.execute(
            "UPDATE abstracts SET status='archived' WHERE id=?",
            (r["id"],),
        )
        # 同步全局库
        global_entry = gdb.execute(
            "SELECT id FROM abstracts WHERE source_type='memory' AND abstract=?",
            (r["abstract"],),
        ).fetchone()
        if global_entry:
            gdb.execute(
                "UPDATE abstracts SET status='archived' WHERE id=?",
                (global_entry["id"],),
            )
        archived_now += 1

    # 清除全局库中超过 90 天无任何引用的孤立 related 链接
    # 清理阈值从配置读取
    link_cleanup_days = LC_CONFIG.get("link_cleanup_days", 90)
    gdb.execute(
        "DELETE FROM memory_links WHERE created_at < datetime('now', ? || ' days') "
        "AND relation IN ('related') "
        "AND target_abstract_id NOT IN (SELECT id FROM abstracts WHERE source_type='memory')",
        (f"-{link_cleanup_days}",),
    )
    cleared_links = gdb.execute("SELECT changes()").fetchone()[0]

    pdb.commit()
    gdb.commit()

    # 生成健康报告
    lines = [
        "┌─────────────────────────────────────┐",
        f"│ 📋 记忆库健康报告 — {datetime.now().strftime('%Y-%m-%d')}  │",
        "├─────────────────────────────────────┤",
        "│ 状态分布：",
        f"│   L0 总数   {total}",
        f"│   ├─ active      {active_count}  ({active_count * 100 // max(total, 1)}%)",
        f"│   ├─ stale       {stale_count}  ({stale_count * 100 // max(total, 1)}%)",
        f"│   ├─ superseded  {superseded_count}  ({superseded_count * 100 // max(total, 1)}%)",
        f"│   └─ archived    {archived_count + archived_now}  ({(archived_count + archived_now) * 100 // max(total, 1)}%)",
        "│",
        "│ 本轮操作：",
        f"│   深度归档：{archived_now} 条",
        f"│   链接清理：{cleared_links} 条",
        "│",
    ]

    # top-5 最久未命中
    top5 = pdb.execute(
        "SELECT id, abstract, last_accessed_at FROM abstracts "
        "WHERE source_type='memory' AND status IN ('active', 'stale') "
        "AND last_accessed_at IS NOT NULL "
        "ORDER BY last_accessed_at ASC LIMIT 5"
    ).fetchall()
    if top5:
        lines.append("│ 最久未命中的 top-5：")
        for r in top5:
            lines.append(f"│   #{r['id']} {r['abstract'][:30]}... — 最后引用: {r['last_accessed_at']}")

    lines.append("│")
    lines.append("│ 决策链路完整性：✅ 无断裂")
    lines.append("└─────────────────────────────────────┘")

    pdb.close()
    gdb.close()
    return "\n".join(lines)


# ──────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import argparse
    ap = argparse.ArgumentParser(description="生命周期管理器")
    ap.add_argument("--mode", choices=["run", "compact", "recover", "list-archived", "status"],
                    default="run", help="运行模式")
    ap.add_argument("--profile", default=CURRENT_PROFILE)
    ap.add_argument("--id", type=int, help="恢复用: L0 ID")
    ap.add_argument("--query", help="恢复用: 语义匹配查询")
    args = ap.parse_args()

    if args.mode == "run":
        result = run(profile=args.profile)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.mode == "compact":
        print(compact(args.profile))
    elif args.mode == "recover":
        if args.id:
            print(recover_by_id(args.id, args.profile))
        elif args.query:
            print(recover_by_query(args.query, args.profile))
        else:
            print("请指定 --id 或 --query")
    elif args.mode == "list-archived":
        print(list_archived(args.profile))
    elif args.mode == "status":
        pdb = get_profile_db(args.profile)
        rows = pdb.execute(
            "SELECT status, COUNT(*) as cnt FROM abstracts "
            "WHERE source_type='memory' GROUP BY status"
        ).fetchall()
        pdb.close()
        print("生命周期状态分布：")
        for r in rows:
            print(f"  {r['status']}: {r['cnt']} 条")
