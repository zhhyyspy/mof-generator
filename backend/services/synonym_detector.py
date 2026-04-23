"""
Synonym class detection + merge utilities.

Two layers:
  - Rule layer: prefix/suffix/affix stripping + edit distance
  - LLM layer (optional): semantic grouping via a single classify-all call

Merge: rewrite all references across M1 package to the kept ID.
"""
from __future__ import annotations

import re
from typing import Optional

from backend.models.m3_schema import Package
from backend.services.llm_client import get_active_client


# --- 层 1: 规则法 ----------------------------------------------------------

# Chinese/English 常见修饰前后缀 — 剥离后比对核心名
_STRIP_PATTERNS = [
    (re.compile(r'^(主|副|辅|子|父|母|小|大)'), ''),  # 主水轮机 → 水轮机
    (re.compile(r'(设备|装置|系统|单元|组件|机组|组件)$'), ''),  # 水轮机设备 → 水轮机
    (re.compile(r'^(Main|Sub|Aux|Primary|Secondary)', re.I), ''),
    (re.compile(r'(Device|Unit|System|Component|Machine|Assembly)$', re.I), ''),
]


def _normalize(name: str) -> str:
    s = (name or "").strip()
    for rx, repl in _STRIP_PATTERNS:
        s = rx.sub(repl, s)
    return s.lower()


def _levenshtein(a: str, b: str) -> int:
    """Classic edit distance (O(m*n) space, fine for class-name lengths)."""
    if a == b: return 0
    if not a: return len(b)
    if not b: return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            cur.append(min(cur[-1] + 1, prev[j] + 1, prev[j - 1] + cost))
        prev = cur
    return prev[-1]


def detect_synonyms_rule(classes: list[dict]) -> list[list[str]]:
    """Return groups of class IDs whose names look like near-synonyms by rule.

    Grouping criteria (any match):
    1. Normalized names equal after stripping common affixes (水轮机 ≡ 主水轮机 ≡ 水轮机设备)
    2. Labels equal (after trim) — different English, same Chinese
    3. Levenshtein distance ≤ 2 AND both names ≥ 4 chars (catches typos like PumpTurbine vs PumpedTurbine)
    """
    # Map normalized key → [ids]
    by_norm: dict[str, list[str]] = {}
    by_label: dict[str, list[str]] = {}
    name_pairs: list[tuple[str, str]] = []  # (id, name) for pairwise edit-distance pass
    for c in classes:
        cid = c.get("id")
        nm = c.get("name") or ""
        lbl = (c.get("label") or "").strip()
        if not cid or not nm:
            continue
        norm = _normalize(nm)
        if norm:
            by_norm.setdefault(norm, []).append(cid)
        if lbl:
            by_label.setdefault(lbl, []).append(cid)
        name_pairs.append((cid, nm))

    # Merge into groups (union-find-ish but tiny scale, use dict of sets)
    parent: dict[str, str] = {}

    def find(x):
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for cid, nm in name_pairs:
        parent.setdefault(cid, cid)

    # Group by normalized name
    for ids in by_norm.values():
        if len(ids) >= 2:
            for j in range(1, len(ids)):
                union(ids[0], ids[j])

    # Group by same label (different English names)
    for ids in by_label.values():
        if len(ids) >= 2:
            for j in range(1, len(ids)):
                union(ids[0], ids[j])

    # Pairwise edit distance for typos (O(n²), n ≤ few hundred is fine)
    for i in range(len(name_pairs)):
        cid1, nm1 = name_pairs[i]
        if len(nm1) < 4:
            continue
        for j in range(i + 1, len(name_pairs)):
            cid2, nm2 = name_pairs[j]
            if len(nm2) < 4 or abs(len(nm1) - len(nm2)) > 2:
                continue
            if _levenshtein(nm1.lower(), nm2.lower()) <= 2:
                union(cid1, cid2)

    # Collect groups with ≥ 2 members
    groups_map: dict[str, list[str]] = {}
    for cid in parent:
        root = find(cid)
        groups_map.setdefault(root, []).append(cid)
    return [g for g in groups_map.values() if len(g) >= 2]


# --- 层 2: LLM 语义分组 (可选) ----------------------------------------------

async def detect_synonyms_llm(classes: list[dict]) -> list[list[str]]:
    """Ask LLM to group semantically equivalent classes. Single call.

    Returns list of [id, id, ...] groups. Empty on failure.
    """
    if not classes:
        return []
    # Compact representation (id is what we need to identify, name+label is for the LLM)
    compact = [
        {"id": c.get("id"), "name": c.get("name"), "label": c.get("label") or ""}
        for c in classes if c.get("id") and c.get("name")
    ]
    if len(compact) < 2:
        return []

    import json as _json
    prompt = f"""下面是一批 M1 类。请判断其中哪些类是**语义等价/近义词**,应当合并为同一个类。

判定原则:
- 名称不同但**描述的是同一业务概念** (如 "水轮机"/"水泵水轮机"/"机组水轮机" 通常是同一类型)
- 明显的同义词替换 (如 "电动机"/"马达"/"发电电动机" 是否指同一类需看业务)
- **不要**因为"都属于设备类"就合并 (这是分类,不是同义)
- **不要**因为前缀修饰 (1号/2号) 合并 (它们是实例)
- 如果不确定是否同义,**不要**放进同组

类列表 (共 {len(compact)} 个):
{_json.dumps(compact, ensure_ascii=False, indent=2)}

返回 JSON 格式 (仅输出 JSON,无其他文字):
{{
  "groups": [
    ["id1", "id2", "id3"],    // 这 3 个类是同义
    ["id7", "id9"]              // 这 2 个类是同义
  ]
}}

如果没有任何同义组,返回 {{"groups": []}}。
"""
    client = get_active_client()
    try:
        resp = await client.chat(prompt, max_tokens=2000)
        # Try to locate JSON
        m = re.search(r'\{[\s\S]*\}', resp or "")
        if not m:
            return []
        data = _json.loads(m.group(0))
        groups = data.get("groups") or []
        # Validate: each group has ≥ 2 known ids
        known_ids = {c["id"] for c in compact}
        out = []
        for g in groups:
            valid = [cid for cid in g if cid in known_ids]
            if len(valid) >= 2:
                out.append(valid)
        return out
    except Exception:
        return []


def merge_groups(rule_groups: list[list[str]], llm_groups: list[list[str]]) -> list[list[str]]:
    """Union rule-layer + LLM-layer groups, deduplicating overlapping groups."""
    # Simple union-find across all ids
    parent: dict[str, str] = {}

    def find(x):
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for g in rule_groups + llm_groups:
        for cid in g:
            parent.setdefault(cid, cid)
        for j in range(1, len(g)):
            union(g[0], g[j])

    groups_map: dict[str, list[str]] = {}
    for cid in parent:
        groups_map.setdefault(find(cid), []).append(cid)
    return [g for g in groups_map.values() if len(g) >= 2]


# --- 合并执行 ---------------------------------------------------------------

def merge_classes_in_package(pkg: Package, merges: list[dict]) -> dict:
    """Apply merges in-place on pkg. Each merge item: {keep: id, drop: [id, id, ...]}

    Rewrites:
      - Dropped classes removed from pkg.classes
      - Their attributes merged into the kept class (by attribute name, first wins)
      - All other classes' parent_class_ref/parent_class_name updated if they pointed
        to a dropped class
      - All Association source/target class_ref + class_name updated
      - StructuralPattern's participating_class_ids, root_class_id,
        hierarchy_association_ids updated

    Returns {kept, dropped, attrs_added_per_kept, refs_rewritten} summary.
    """
    classes_by_id = {c.id: c for c in pkg.classes}
    # Build: dropped_id → kept_id; also kept_name for name-based fields
    drop_to_keep: dict[str, str] = {}
    kept_new_names: dict[str, str] = {}
    for m in merges:
        keep_id = m.get("keep")
        drops = m.get("drop") or []
        if keep_id not in classes_by_id:
            continue
        kept_new_names[keep_id] = classes_by_id[keep_id].name
        for d in drops:
            if d in classes_by_id and d != keep_id:
                drop_to_keep[d] = keep_id

    # Merge attributes into kept (by attribute name — first wins to respect kept class as authoritative)
    attrs_added_per_kept: dict[str, int] = {}
    for drop_id, keep_id in drop_to_keep.items():
        dropped = classes_by_id[drop_id]
        kept = classes_by_id[keep_id]
        existing_attr_names = {a.name for a in kept.attributes}
        added = 0
        for a in dropped.attributes:
            if a.name and a.name not in existing_attr_names:
                kept.attributes.append(a)
                existing_attr_names.add(a.name)
                added += 1
        attrs_added_per_kept[keep_id] = attrs_added_per_kept.get(keep_id, 0) + added

    # Remove dropped classes
    pkg.classes = [c for c in pkg.classes if c.id not in drop_to_keep]

    # Build new name map (after removal) — by-id lookup for class_name consistency
    id_to_name = {c.id: c.name for c in pkg.classes}

    refs_rewritten = 0

    # Rewrite parent_class_ref / parent_class_name in remaining classes
    for c in pkg.classes:
        if c.parent_class_ref in drop_to_keep:
            new_id = drop_to_keep[c.parent_class_ref]
            c.parent_class_ref = new_id
            c.parent_class_name = id_to_name.get(new_id) or c.parent_class_name
            refs_rewritten += 1

    # Rewrite Association source + target
    for a in pkg.associations:
        if a.source and a.source.class_ref in drop_to_keep:
            new_id = drop_to_keep[a.source.class_ref]
            a.source.class_ref = new_id
            a.source.class_name = id_to_name.get(new_id) or a.source.class_name
            refs_rewritten += 1
        if a.target and a.target.class_ref in drop_to_keep:
            new_id = drop_to_keep[a.target.class_ref]
            a.target.class_ref = new_id
            a.target.class_name = id_to_name.get(new_id) or a.target.class_name
            refs_rewritten += 1

    # Rewrite StructuralPattern
    for sp in (pkg.structural_patterns or []):
        new_ids = []
        seen = set()
        for cid in sp.participating_class_ids or []:
            new_cid = drop_to_keep.get(cid, cid)
            if new_cid not in seen:
                new_ids.append(new_cid)
                seen.add(new_cid)
        sp.participating_class_ids = new_ids
        if sp.root_class_id in drop_to_keep:
            sp.root_class_id = drop_to_keep[sp.root_class_id]

    return {
        "kept_class_ids": list(kept_new_names.keys()),
        "dropped_class_ids": list(drop_to_keep.keys()),
        "attrs_added_per_kept": attrs_added_per_kept,
        "refs_rewritten": refs_rewritten,
    }
