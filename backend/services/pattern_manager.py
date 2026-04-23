"""
Structural-pattern management — CRUD + M1 impact analysis + atomic migration.

Exposed:
  · build_pattern_from_request(m2_pkg, req)        → (new_pattern, sync_ops)
  · sync_meta_structure_on_classes(m2_pkg, pattern) → mutates m2_pkg in-place
  · auto_wire_hierarchy_edges(m2_pkg, pattern)     → mutates, returns [new_assoc_ids]
  · diff_patterns(old, new_req, m2_pkg)            → {safe_changes, hard_changes}
  · scan_m1_impact(hard_changes, m2_pkg, store)    → [{m1_model_id, m1_class_id, ...}]
  · apply_m1_migrations(migrations, store)         → {updated, failed}
  · AtomicModelWrite context manager               → multi-model transactional save

Design: the pattern is the single source of truth; derived fields
  (MOFClass.meta_structure_*, Association.is_hierarchy/hierarchy_order,
   pattern.root_class_id, pattern.participating_class_ids order)
are auto-synced from the pattern's `levels` payload.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from backend.models.m3_schema import (
    Package, MOFClass, Association, AssociationEnd,
    Multiplicity, StructuralPattern,
)
from backend.models.m1_model import M1Model
from backend.storage.file_store import store as _default_store, FileStore


VALID_ROLES = ("root", "intermediate", "leaf")
DEFAULT_CONSTRAINTS = ["no_cycle", "no_cross_level", "no_reverse", "root_fixed"]
DEFAULT_REC_ASSOC = "composition"

# M1 migration actions
ACTION_KEEP_PARENT = "keep_parent"
ACTION_REASSIGN = "reassign"
ACTION_NULL_PARENT = "null_parent"
ACTION_DELETE_M1 = "delete_m1"
ACTION_CASCADE_RENAME = "cascade_rename"

VALID_ACTIONS = (
    ACTION_KEEP_PARENT, ACTION_REASSIGN, ACTION_NULL_PARENT,
    ACTION_DELETE_M1, ACTION_CASCADE_RENAME,
)


# ============================================================================
#                     Request shape (from frontend editor)
# ============================================================================
# The editor POSTs a minimal description; we derive everything else.
#
#   {
#     "name": "InvestmentPattern",
#     "label": "投资管理层级模板",
#     "description": "...",
#     "recommended_assoc_type": "composition",
#     "constraints": ["no_cycle", "no_cross_level", "no_reverse", "root_fixed"],
#     "levels": [
#       {"level_name": "L1-设施", "class_id": "uuid...", "role": "root"},
#       {"level_name": "L2-系统", "class_id": "uuid...", "role": "intermediate"},
#       ...
#     ],
#     "hierarchy_association_overrides": {
#         # Optional: level i → pick a specific existing assoc id (instead of
#         # auto-picking or auto-creating). Format: "0": "assoc_uuid" means
#         # the i=0 transition (level 0 → level 1) uses assoc_uuid.
#     }
#   }


@dataclass
class PatternRequest:
    name: str
    label: Optional[str] = None
    description: Optional[str] = None
    recommended_assoc_type: str = DEFAULT_REC_ASSOC
    constraints: list[str] = field(default_factory=lambda: list(DEFAULT_CONSTRAINTS))
    levels: list[dict] = field(default_factory=list)
    hierarchy_association_overrides: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "PatternRequest":
        return cls(
            name=d.get("name") or "",
            label=d.get("label"),
            description=d.get("description"),
            recommended_assoc_type=d.get("recommended_assoc_type") or DEFAULT_REC_ASSOC,
            constraints=list(d.get("constraints") or DEFAULT_CONSTRAINTS),
            levels=list(d.get("levels") or []),
            hierarchy_association_overrides=dict(d.get("hierarchy_association_overrides") or {}),
        )


# ============================================================================
#                        Pattern construction + validation
# ============================================================================


def validate_request(req: PatternRequest, m2_pkg: Package) -> list[str]:
    """Return list of error messages; empty list = valid."""
    errors: list[str] = []
    if not req.name.strip():
        errors.append("元结构名称 (name) 不能为空")
    if len(req.levels) < 2:
        errors.append("层级数必须 ≥ 2")
    # Class ids exist in package
    class_by_id = {c.id: c for c in m2_pkg.classes}
    seen_class_ids: set[str] = set()
    for i, lvl in enumerate(req.levels):
        cid = lvl.get("class_id")
        if not cid:
            errors.append(f"L{i+1}: class_id 缺失")
            continue
        if cid not in class_by_id:
            errors.append(f"L{i+1}: class_id '{cid}' 在当前 M2 包中不存在")
            continue
        if cid in seen_class_ids:
            errors.append(f"L{i+1}: 类 '{class_by_id[cid].name}' 在同一元结构中重复参与")
            continue
        seen_class_ids.add(cid)
        lvl_name = (lvl.get("level_name") or "").strip()
        if not lvl_name:
            errors.append(f"L{i+1}: level_name 缺失")
        role = lvl.get("role")
        if role and role not in VALID_ROLES:
            errors.append(f"L{i+1}: role '{role}' 非法, 应为 {VALID_ROLES}")
    # Level names unique
    lvl_names = [(l.get("level_name") or "").strip() for l in req.levels]
    if len(set(lvl_names)) != len(lvl_names):
        errors.append("level_names 有重复, 每层必须命名唯一")
    # A class must not already belong to *another* pattern
    for lvl in req.levels:
        cid = lvl.get("class_id")
        if not cid or cid not in class_by_id:
            continue
        cls = class_by_id[cid]
        if cls.meta_structure_id:
            # Belongs to a pattern. Is it the one we're editing? If so OK.
            # Caller should handle that by passing excluded_pattern_id, but here we allow.
            pass  # Let higher-level check handle; this is merely informational
    return errors


def _derive_role(index: int, total: int, explicit: Optional[str]) -> str:
    """Compute canonical role based on level index, honoring explicit user choice."""
    if explicit in VALID_ROLES:
        return explicit
    if index == 0:
        return "root"
    if index == total - 1:
        return "leaf"
    return "intermediate"


def build_pattern_entity(req: PatternRequest, *, existing_id: Optional[str] = None) -> StructuralPattern:
    """Build a StructuralPattern entity from the request. Does NOT touch the package."""
    pattern_id = existing_id or str(uuid.uuid4())
    class_ids = [lvl.get("class_id") for lvl in req.levels]
    level_names = [(lvl.get("level_name") or "").strip() for lvl in req.levels]
    root_class_id = class_ids[0] if class_ids else None
    return StructuralPattern(
        id=pattern_id,
        name=req.name.strip(),
        label=(req.label or "").strip() or None,
        description=(req.description or "").strip() or None,
        participating_class_ids=list(class_ids),
        hierarchy_association_ids=[],   # filled by auto_wire_hierarchy_edges
        root_class_id=root_class_id,
        level_names=level_names,
        constraints=list(req.constraints or DEFAULT_CONSTRAINTS),
        recommended_assoc_type=req.recommended_assoc_type or DEFAULT_REC_ASSOC,
    )


# ============================================================================
#                    Sync derived fields on classes + associations
# ============================================================================


def sync_meta_structure_on_classes(m2_pkg: Package, pattern: StructuralPattern,
                                     req: PatternRequest) -> None:
    """After a pattern is built, set MOFClass.meta_structure_{id,role,level} on
    each participating class. Clear these fields on classes that were in the
    previous pattern but no longer participate.

    req.levels provides the explicit role override (if any); derive otherwise.
    """
    cls_by_id = {c.id: c for c in m2_pkg.classes}
    total = len(pattern.participating_class_ids)
    # Set on participating
    for i, cid in enumerate(pattern.participating_class_ids):
        c = cls_by_id.get(cid)
        if not c:
            continue
        explicit_role = None
        if i < len(req.levels):
            explicit_role = req.levels[i].get("role")
        c.meta_structure_id = pattern.id
        c.meta_structure_role = _derive_role(i, total, explicit_role)
        c.meta_structure_level = i + 1
    # Clear on any class that previously belonged to this pattern but is no longer in it
    participating_set = set(pattern.participating_class_ids)
    for c in m2_pkg.classes:
        if c.meta_structure_id == pattern.id and c.id not in participating_set:
            c.meta_structure_id = None
            c.meta_structure_role = None
            c.meta_structure_level = None


def auto_wire_hierarchy_edges(
    m2_pkg: Package,
    pattern: StructuralPattern,
    req: PatternRequest,
) -> dict[str, Any]:
    """Ensure there are N-1 hierarchy Association edges connecting the ordered
    participating classes, using the preferred strategy:
      1. If user override provides an existing assoc id for this transition: reuse it
      2. Else if any existing Association already connects levels[i].class → levels[i+1].class:
         promote it to is_hierarchy=true with hierarchy_order=i+1
      3. Else create a new composition Association

    All previously-hierarchy edges NO LONGER needed (because pattern shape changed)
    are demoted (is_hierarchy=false, hierarchy_order=None) but kept as data.

    Returns {created_assoc_ids, promoted_assoc_ids, demoted_assoc_ids, chain}.
    """
    cls_by_id = {c.id: c for c in m2_pkg.classes}
    participating = pattern.participating_class_ids
    overrides = req.hierarchy_association_overrides or {}

    # Previous hierarchy edges attached to this pattern
    prev_hier_ids = set(pattern.hierarchy_association_ids or [])
    new_chain_ids: list[str] = []
    created: list[str] = []
    promoted: list[str] = []

    # Build assoc index for lookup
    assoc_by_endpoints: dict[tuple[str, str], Association] = {}
    for a in m2_pkg.associations:
        if a.source and a.target:
            assoc_by_endpoints.setdefault((a.source.class_ref, a.target.class_ref), a)

    for i in range(len(participating) - 1):
        src_cid = participating[i]
        tgt_cid = participating[i + 1]
        src_cls = cls_by_id.get(src_cid)
        tgt_cls = cls_by_id.get(tgt_cid)
        if not (src_cls and tgt_cls):
            continue
        order = i + 1
        picked: Optional[Association] = None

        # 1. Explicit override
        override_id = overrides.get(str(i)) or overrides.get(i)
        if override_id:
            for a in m2_pkg.associations:
                if a.id == override_id:
                    picked = a
                    break

        # 2. Existing edge A→B
        if not picked:
            picked = assoc_by_endpoints.get((src_cid, tgt_cid))

        # 3. Create new
        if not picked:
            new_a = Association(
                id=str(uuid.uuid4()),
                name=f"{src_cls.name[:1].lower()}{src_cls.name[1:] if src_cls.name else ''}Has{tgt_cls.name}"
                     if src_cls.name else f"has_{tgt_cls.name}",
                label=f"{src_cls.label or src_cls.name} 包含 {tgt_cls.label or tgt_cls.name}",
                source=AssociationEnd(
                    class_ref=src_cid, class_name=src_cls.name,
                    role_name=(src_cls.name[:1].lower() + src_cls.name[1:]) if src_cls.name else "source",
                    multiplicity=Multiplicity(lower=1, upper=1), navigable=True,
                ),
                target=AssociationEnd(
                    class_ref=tgt_cid, class_name=tgt_cls.name,
                    role_name=(tgt_cls.name[:1].lower() + tgt_cls.name[1:] + "s") if tgt_cls.name else "targets",
                    multiplicity=Multiplicity(lower=0, upper=-1), navigable=True,
                ),
                association_type=pattern.recommended_assoc_type or DEFAULT_REC_ASSOC,
                is_hierarchy=True,
                hierarchy_order=order,
            )
            m2_pkg.associations.append(new_a)
            picked = new_a
            created.append(new_a.id)
        else:
            # Promote existing
            if not picked.is_hierarchy:
                promoted.append(picked.id)
            picked.is_hierarchy = True
            picked.hierarchy_order = order
            # Also sync source/target class_name if stale
            if picked.source and src_cls.name:
                picked.source.class_name = src_cls.name
            if picked.target and tgt_cls.name:
                picked.target.class_name = tgt_cls.name

        new_chain_ids.append(picked.id)

    # Demote previously-hierarchy assocs of this pattern that aren't in new chain
    demoted: list[str] = []
    new_chain_set = set(new_chain_ids)
    for aid in prev_hier_ids:
        if aid in new_chain_set:
            continue
        # Find + demote
        for a in m2_pkg.associations:
            if a.id == aid and a.is_hierarchy:
                a.is_hierarchy = False
                a.hierarchy_order = None
                demoted.append(aid)
                break

    pattern.hierarchy_association_ids = new_chain_ids
    return {"created": created, "promoted": promoted, "demoted": demoted, "chain": new_chain_ids}


# ============================================================================
#                        Diff old vs new pattern
# ============================================================================


@dataclass
class PatternChange:
    kind: str                    # "level_renamed" | "class_renamed" | "class_swapped" | ...
    severity: str                # "safe" | "soft" | "hard"
    description: str
    extra: dict[str, Any] = field(default_factory=dict)


def diff_patterns(
    old: Optional[StructuralPattern],
    req: PatternRequest,
    m2_pkg: Package,
) -> list[PatternChange]:
    """Compare the committed pattern (old) against the new request.
    Returns an ordered list of changes, each categorized by severity."""
    changes: list[PatternChange] = []
    cls_by_id = {c.id: c for c in m2_pkg.classes}

    # Create-from-scratch case
    if old is None:
        changes.append(PatternChange(
            kind="create", severity="safe",
            description=f"新建元结构 '{req.label or req.name}',含 {len(req.levels)} 层",
        ))
        return changes

    # label/description/recommended_assoc_type changes — all safe
    new_label = (req.label or "").strip() or None
    if new_label != old.label:
        changes.append(PatternChange(
            kind="label_changed", severity="safe",
            description=f"名称变更: '{old.label}' → '{new_label}'",
        ))
    new_desc = (req.description or "").strip() or None
    if new_desc != old.description:
        changes.append(PatternChange(
            kind="desc_changed", severity="safe",
            description="描述已更新",
        ))
    if req.recommended_assoc_type and req.recommended_assoc_type != old.recommended_assoc_type:
        changes.append(PatternChange(
            kind="rec_assoc_changed", severity="safe",
            description=f"推荐关联类型: '{old.recommended_assoc_type}' → '{req.recommended_assoc_type}'",
        ))

    # constraints
    old_cons = set(old.constraints or [])
    new_cons = set(req.constraints or [])
    added_cons = new_cons - old_cons
    removed_cons = old_cons - new_cons
    if added_cons or removed_cons:
        bits = []
        if added_cons: bits.append(f"新增 {sorted(added_cons)}")
        if removed_cons: bits.append(f"移除 {sorted(removed_cons)}")
        changes.append(PatternChange(
            kind="constraints_changed", severity="safe",
            description="约束变更: " + " / ".join(bits),
        ))

    # Levels comparison
    old_class_ids = list(old.participating_class_ids or [])
    new_class_ids = [l.get("class_id") for l in req.levels]
    old_level_names = list(old.level_names or [])
    new_level_names = [(l.get("level_name") or "").strip() for l in req.levels]

    # Level renames (where same class is at same position, just name changed)
    for i in range(min(len(old_class_ids), len(new_class_ids))):
        if old_class_ids[i] == new_class_ids[i]:
            if i < len(old_level_names) and i < len(new_level_names):
                if old_level_names[i] != new_level_names[i]:
                    changes.append(PatternChange(
                        kind="level_renamed", severity="safe",
                        description=f"L{i+1} 层级名: '{old_level_names[i]}' → '{new_level_names[i]}'",
                        extra={"level_index": i},
                    ))

    # Classes removed from the pattern (still exist in M2, just unbound)
    removed_class_ids = set(old_class_ids) - set(new_class_ids)
    for rid in removed_class_ids:
        c = cls_by_id.get(rid)
        cname = c.name if c else rid
        changes.append(PatternChange(
            kind="class_removed_from_pattern", severity="hard",
            description=f"从元结构移除参与类 '{c.label or cname if c else cname}'",
            extra={"class_id": rid, "class_name": cname},
        ))

    # Classes newly added
    added_class_ids = set(new_class_ids) - set(old_class_ids)
    for aid in added_class_ids:
        c = cls_by_id.get(aid)
        cname = c.name if c else aid
        changes.append(PatternChange(
            kind="class_added_to_pattern", severity="soft",
            description=f"新增参与类 '{c.label or cname if c else cname}'",
            extra={"class_id": aid, "class_name": cname},
        ))

    # Reorder detection (same set, different order)
    if (set(old_class_ids) == set(new_class_ids)) and old_class_ids != new_class_ids:
        changes.append(PatternChange(
            kind="reordered", severity="hard",
            description="层级顺序被调整,参与类相同但顺序改变",
            extra={"old": old_class_ids, "new": new_class_ids},
        ))

    return changes


# ============================================================================
#                        M1 impact scan
# ============================================================================


def scan_m1_impact(
    changes: list[PatternChange],
    m2_pkg: Package,
    m2_id: str,
    store: FileStore = _default_store,
) -> list[dict]:
    """Given a list of hard changes, find all M1 classes that reference
    impacted M2 classes by name. Returns a list of impact items:
      {
        change_kind, m2_class_name,
        m1_model_id, m1_model_label, m1_class_id, m1_class_name, m1_class_label,
        suggested_action, alternatives
      }
    """
    hard = [c for c in changes if c.severity == "hard"]
    if not hard:
        return []

    # Find all M1 models bound to this M2
    all_models = store.list_models()
    m1_ids = [m["id"] for m in all_models
              if m.get("m2_template_id") == m2_id and not m["id"].startswith("m2_")]

    cls_by_id = {c.id: c for c in m2_pkg.classes}
    cls_by_name = {c.name: c for c in m2_pkg.classes}

    impacts: list[dict] = []
    for m1_id in m1_ids:
        m1 = store.get_model(m1_id)
        if m1 is None:
            continue
        if not m1.versions:
            continue
        pkg = m1.versions[-1].package
        m1_label = m1.label or m1.name

        for cls in pkg.classes:
            parent_name = cls.parent_class_name
            if not parent_name:
                continue
            for ch in hard:
                m2_cname = ch.extra.get("class_name")
                if not m2_cname:
                    continue

                if ch.kind == "class_removed_from_pattern":
                    # Only flag if M1 parent matches the removed class
                    if parent_name == m2_cname:
                        impacts.append({
                            "change_kind": ch.kind,
                            "m2_class_name": m2_cname,
                            "m1_model_id": m1_id,
                            "m1_model_label": m1_label,
                            "m1_class_id": cls.id,
                            "m1_class_name": cls.name,
                            "m1_class_label": cls.label or cls.name,
                            "current_parent": parent_name,
                            "suggested_action": ACTION_KEEP_PARENT,
                            "alternatives": [ACTION_REASSIGN, ACTION_NULL_PARENT, ACTION_DELETE_M1],
                        })
                # future: other change kinds (rename, swap) can inject impacts here

    return impacts


def scan_cascade_renames(
    old_pkg: Package,
    new_pkg: Package,
    m2_id: str,
    store: FileStore = _default_store,
) -> list[dict]:
    """Detect M2 class renames (id identical, name changed) and return
    suggested cascade-rename ops for M1 classes whose parent_class_name
    matches the old name. Default action: ACTION_CASCADE_RENAME (auto-apply).
    """
    renames: dict[str, str] = {}
    old_by_id = {c.id: c for c in old_pkg.classes}
    for c in new_pkg.classes:
        oc = old_by_id.get(c.id)
        if oc and oc.name != c.name:
            renames[oc.name] = c.name
    if not renames:
        return []
    all_models = store.list_models()
    m1_ids = [m["id"] for m in all_models
              if m.get("m2_template_id") == m2_id and not m["id"].startswith("m2_")]
    impacts: list[dict] = []
    for m1_id in m1_ids:
        m1 = store.get_model(m1_id)
        if m1 is None or not m1.versions:
            continue
        pkg = m1.versions[-1].package
        for cls in pkg.classes:
            p = cls.parent_class_name
            if p and p in renames:
                impacts.append({
                    "change_kind": "class_renamed",
                    "m2_class_old_name": p,
                    "m2_class_new_name": renames[p],
                    "m1_model_id": m1_id,
                    "m1_model_label": m1.label or m1.name,
                    "m1_class_id": cls.id,
                    "m1_class_name": cls.name,
                    "m1_class_label": cls.label or cls.name,
                    "current_parent": p,
                    "suggested_action": ACTION_CASCADE_RENAME,
                    "alternatives": [ACTION_KEEP_PARENT, ACTION_NULL_PARENT],
                })
    return impacts


# ============================================================================
#                        Atomic multi-model save
# ============================================================================


class AtomicModelWrite:
    """Multi-model transaction. Captures pre-edit JSON on enter, rolls back on error.

    Usage:
        with AtomicModelWrite(store, [m2_id, m1a_id, m1b_id]) as tx:
            tx.save(m2_model)
            tx.save(m1a_model)
            tx.save(m1b_model)
        # all saves applied on __exit__ without exception, else all rolled back
    """
    def __init__(self, store: FileStore, model_ids: list[str]):
        self.store = store
        self.model_ids = list(set(model_ids))
        self._snapshots: dict[str, Optional[str]] = {}
        self._to_save: list[M1Model] = []

    def __enter__(self):
        # Snapshot each model's current on-disk JSON (or None if brand-new)
        for mid in self.model_ids:
            p = self.store._model_path(mid)
            self._snapshots[mid] = p.read_text(encoding="utf-8") if p.exists() else None
        return self

    def save(self, model: M1Model) -> None:
        """Defer the save until exit. Reject if model.id isn't in the transaction."""
        if model.id not in self._snapshots:
            raise ValueError(f"Model {model.id} not registered with transaction "
                             f"(allowed: {list(self._snapshots.keys())})")
        self._to_save.append(model)

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            # Unwind: restore all snapshots. _to_save was never applied.
            return False
        # Happy path: apply all saves. If any fails, rollback ALL (including prior successes).
        applied: list[str] = []
        try:
            for m in self._to_save:
                self.store.save_model(m)
                applied.append(m.id)
        except Exception:
            # Rollback applied saves
            for aid in applied:
                snap = self._snapshots.get(aid)
                if snap is None:
                    # Was a new model; delete the file we just created
                    p = self.store._model_path(aid)
                    if p.exists():
                        try: p.unlink()
                        except Exception: pass
                else:
                    self.store._model_path(aid).write_text(snap, encoding="utf-8")
            raise
        return False


def apply_m1_migrations_and_save(
    migrations: list[dict],
    m2_model,            # M1Model instance of the M2 being saved
    store: FileStore = _default_store,
    *,
    m2_pkg_for_rename: Optional[Package] = None,
) -> dict:
    """Integrated: load each affected M1 model, apply its migrations, then
    atomically save everything (M2 + all migrated M1s) or rollback all.

    Returns: {updated, failed, m2_saved, m1_saved}
    """
    # Group by M1 model
    by_model: dict[str, list[dict]] = {}
    for mig in migrations:
        by_model.setdefault(mig.get("m1_model_id", ""), []).append(mig)

    # Load each M1 model once, keep reference for mutation + save
    m1_models: dict[str, M1Model] = {}
    for m1_id in by_model.keys():
        if not m1_id:
            continue
        mm = store.get_model(m1_id)
        if mm is not None:
            m1_models[m1_id] = mm

    updated: list[dict] = []
    failed: list[dict] = []
    m2_cls_by_id = {c.id: c for c in (m2_pkg_for_rename.classes if m2_pkg_for_rename else [])}

    # Apply in-memory mutations
    for m1_id, migs in by_model.items():
        model = m1_models.get(m1_id)
        if not model or not model.versions:
            for mig in migs:
                failed.append({**mig, "error": "M1 模型不存在或无版本"})
            continue
        pkg = model.versions[-1].package
        cls_by_id = {c.id: c for c in pkg.classes}
        for mig in migs:
            action = mig.get("action")
            cid = mig.get("m1_class_id")
            cls = cls_by_id.get(cid)
            if not cls:
                failed.append({**mig, "error": f"M1 类 {cid} 不存在"})
                continue
            try:
                if action == ACTION_KEEP_PARENT:
                    pass
                elif action == ACTION_NULL_PARENT:
                    cls.parent_class_ref = None
                    cls.parent_class_name = None
                elif action == ACTION_REASSIGN:
                    tgt = mig.get("target_class_id")
                    if not tgt:
                        raise ValueError("reassign 缺 target_class_id")
                    cls.parent_class_ref = tgt
                    tgt_cls = m2_cls_by_id.get(tgt)
                    if tgt_cls:
                        cls.parent_class_name = tgt_cls.name
                    else:
                        cls.parent_class_name = mig.get("target_class_name") or cls.parent_class_name
                elif action == ACTION_DELETE_M1:
                    pkg.classes = [c for c in pkg.classes if c.id != cid]
                    pkg.associations = [
                        a for a in pkg.associations
                        if getattr(a.source, "class_ref", None) != cid
                        and getattr(a.target, "class_ref", None) != cid
                    ]
                elif action == ACTION_CASCADE_RENAME:
                    new_name = mig.get("target_class_name")
                    if not new_name:
                        raise ValueError("cascade_rename 缺 target_class_name")
                    cls.parent_class_name = new_name
                else:
                    raise ValueError(f"未知 action: {action}")
                updated.append({
                    "m1_model_id": m1_id, "m1_class_id": cid,
                    "action": action, "applied": True,
                })
            except Exception as e:
                failed.append({**mig, "error": str(e)})

    # Atomically save M2 + all affected M1s
    participating_ids = [m2_model.id] + list(m1_models.keys())
    try:
        with AtomicModelWrite(store, participating_ids) as tx:
            tx.save(m2_model)
            for mm in m1_models.values():
                tx.save(mm)
    except Exception as e:
        # Rolled back; report failure
        failed.append({"_global": True, "error": f"原子保存失败,已回滚: {e}"})
        return {"updated": [], "failed": failed, "m2_saved": False, "m1_saved_count": 0}
    return {
        "updated": updated,
        "failed": failed,
        "m2_saved": True,
        "m1_saved_count": len(m1_models),
    }
