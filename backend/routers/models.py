"""M1 model CRUD endpoints."""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException

from backend.models.m3_schema import (
    Package, MOFClass, Attribute, Association, AssociationEnd,
    Multiplicity, Enumeration, EnumLiteral, Constraint, PrimitiveDataType,
)
from backend.models.m1_model import M1Model, M1ModelVersion
from backend.models.api_schemas import (
    ModelCreateRequest, ClassCreateRequest, AttributeCreateRequest,
    AssociationCreateRequest, EnumerationCreateRequest, VersionCreateRequest,
)
from backend.storage.file_store import store
from backend.services.model_validator import validator
from backend.services.version_manager import version_manager

router = APIRouter(prefix="/api/v1/models", tags=["models"])


def _get_model_or_404(model_id: str) -> M1Model:
    m = store.get_model(model_id)
    if m is None:
        raise HTTPException(404, f"Model {model_id} not found")
    return m


def _current_package(model: M1Model) -> Package:
    if not model.versions:
        raise HTTPException(400, "Model has no versions")
    return model.versions[-1].package


# ---- Model-level CRUD ----

@router.post("/")
async def create_model(req: ModelCreateRequest):
    """Create a new blank M1 model."""
    model_id = str(uuid.uuid4())[:8]
    pkg = Package(name=req.name, label=req.label, description=req.description)
    version = M1ModelVersion(
        version="1.0",
        created_at=datetime.now(),
        changelog="Initial version",
        package=pkg,
    )
    model = M1Model(
        id=model_id,
        name=req.name,
        label=req.label,
        description=req.description,
        m2_template_id=req.m2_template_id,
        current_version="1.0",
        versions=[version],
    )
    store.save_model(model)
    return model.model_dump()


@router.post("/save-m2")
async def save_m2_from_review(data: dict):
    """Save user-reviewed M2 model and update the source M1 to reference it."""
    package_data = data.get("package")
    source_m1_id = data.get("source_m1_id")
    class_mappings = data.get("m1_class_mappings", [])

    if not package_data:
        raise HTTPException(400, "Missing package data")
    if not source_m1_id:
        raise HTTPException(400, "Missing source_m1_id")

    m2_id = f"m2_{source_m1_id}"
    name = data.get("name", "M2MetaModel")
    label = data.get("label", "M2元模型")

    pkg = Package.model_validate(package_data)
    pkg.name = name
    pkg.label = label

    version = M1ModelVersion(
        version="1.0",
        created_at=datetime.now(),
        changelog="从M1推导 + 人工审查确认的M2元模型",
        package=pkg,
    )
    m2_model = M1Model(
        id=m2_id,
        name=name,
        label=label,
        description="从M1模型推导的M2元模型（通用抽象层）",
        m2_template_id="",
        current_version="1.0",
        versions=[version],
        status="draft",
    )
    store.save_model(m2_model)

    # Update M1 to reference this M2 (and mark inherited attributes)
    # V3.0 methodology: each M1 class points to a SPECIFIC M2 MetaClass (not a
    # single "theme" class with a level enum). m1_class_mappings now contains:
    #   {m1_class_name, m2_parent_name, m2_theme_name}
    # where m2_parent_name is the specific level MetaClass (e.g. "Facility") and
    # m2_theme_name is the outer structural theme (e.g. "EquipmentLedger") for
    # back-navigation. For flat (元类) themes, both names are the same.
    m1_model = store.get_model(source_m1_id)
    if m1_model:
        m1_model.m2_template_id = m2_id

        m1_mapping_lookup: dict[str, dict] = {}
        for m in class_mappings:
            n = m.get("m1_class_name")
            if not n:
                continue
            m1_mapping_lookup[n] = {
                "m2_parent_name": m.get("m2_parent_name"),
                "m2_theme_name": m.get("m2_theme_name") or m.get("m2_parent_name"),
            }

        pkg_m1 = m1_model.versions[-1].package
        m2_class_ids = {c.name: c.id for c in pkg.classes}
        m2_attrs_by_class: dict[str, dict] = {
            c.name: {a.name: a for a in c.attributes} for c in pkg.classes
        }

        for cls in pkg_m1.classes:
            info = m1_mapping_lookup.get(cls.name)
            if not info:
                continue

            parent_name = info["m2_parent_name"]
            if not parent_name or parent_name not in m2_class_ids:
                continue

            cls.parent_class_name = parent_name
            cls.parent_class_ref = m2_class_ids.get(parent_name)

            # Mark M1 attributes that share a name with the M2 class's attrs as inherited
            # Inheritance is now strictly by name-match with the SPECIFIC level MetaClass
            # the M1 belongs to (not an aggregate "theme" class).
            m2_attr_names = set(m2_attrs_by_class.get(parent_name, {}).keys())
            for attr in cls.attributes:
                if attr.name in m2_attr_names:
                    attr.is_inherited = True

        # M1 package publish status: transitioning to "published" happens via explicit
        # API call. save-m2 just wires up the M1→M2 inheritance, doesn't auto-publish.

        store.save_model(m1_model)

    return {"model_id": m2_id, "status": "saved"}


@router.post("/from-extraction")
async def create_from_extraction(data: dict):
    """Create M1 model from reviewed extraction results (user-selected entities only)."""
    package_data = data.get("package")
    if not package_data:
        raise HTTPException(400, "Missing package data")

    model_id = str(uuid.uuid4())[:8]
    name = data.get("name", f"M1_Model_{model_id}")
    label = data.get("label", "AI提取的M1模型")

    pkg = Package.model_validate(package_data)
    pkg.name = name
    pkg.label = label

    version = M1ModelVersion(
        version="1.0",
        created_at=datetime.now(),
        changelog="AI提取 + 人工审查确认",
        package=pkg,
    )
    model = M1Model(
        id=model_id,
        name=name,
        label=label,
        description=data.get("description", "AI从文档提取并经人工审查确认的M1模型"),
        m2_template_id="",
        source_document_ids=data.get("source_document_ids", []),
        current_version="1.0",
        versions=[version],
        status="draft",
    )
    store.save_model(model)
    return {"model_id": model_id, "status": "saved"}


@router.get("/")
async def list_models():
    return {"models": store.list_models()}


@router.get("/{model_id}")
async def get_model(model_id: str):
    return _get_model_or_404(model_id).model_dump()


@router.put("/{model_id}")
async def update_model(model_id: str, data: dict):
    """Update the entire current version's package."""
    model = _get_model_or_404(model_id)
    if "package" in data:
        pkg = Package.model_validate(data["package"])
        model.versions[-1].package = pkg
    if "name" in data:
        model.name = data["name"]
    if "label" in data:
        model.label = data["label"]
    if "description" in data:
        model.description = data["description"]
    if "status" in data:
        model.status = data["status"]
    store.save_model(model)
    return model.model_dump()


@router.delete("/{model_id}")
async def delete_model(model_id: str):
    if not store.delete_model(model_id):
        raise HTTPException(404, f"Model {model_id} not found")
    return {"status": "deleted"}


# ---- Class CRUD ----

@router.post("/{model_id}/classes")
async def add_class(model_id: str, req: ClassCreateRequest):
    model = _get_model_or_404(model_id)
    pkg = _current_package(model)
    cls = MOFClass(
        id=str(uuid.uuid4()),
        name=req.name,
        label=req.label,
        description=req.description,
        parent_class_ref=req.parent_class_ref,
        parent_class_name=req.parent_class_name,
    )
    pkg.classes.append(cls)
    store.save_model(model)
    return cls.model_dump()


@router.patch("/{model_id}/classes/{class_id}")
async def update_class(model_id: str, class_id: str, data: dict):
    model = _get_model_or_404(model_id)
    pkg = _current_package(model)
    for cls in pkg.classes:
        if cls.id == class_id:
            for key in ("name", "label", "description", "parent_class_ref",
                        "parent_class_name", "is_abstract"):
                if key in data:
                    setattr(cls, key, data[key])
            store.save_model(model)
            return cls.model_dump()
    raise HTTPException(404, f"Class {class_id} not found")


@router.delete("/{model_id}/classes/{class_id}")
async def delete_class(model_id: str, class_id: str):
    model = _get_model_or_404(model_id)
    pkg = _current_package(model)
    pkg.classes = [c for c in pkg.classes if c.id != class_id]
    store.save_model(model)
    return {"status": "deleted"}


# ---- Attribute CRUD ----

@router.post("/{model_id}/classes/{class_id}/attributes")
async def add_attribute(model_id: str, class_id: str, req: AttributeCreateRequest):
    model = _get_model_or_404(model_id)
    pkg = _current_package(model)
    for cls in pkg.classes:
        if cls.id == class_id:
            attr = Attribute(
                id=str(uuid.uuid4()),
                name=req.name,
                label=req.label,
                description=req.description,
                data_type=req.data_type,
                enum_ref=req.enum_ref,
                unit=req.unit,
                multiplicity=Multiplicity(lower=req.multiplicity_lower, upper=req.multiplicity_upper),
                default_value=req.default_value,
                logical_type=req.logical_type,  # V3.3
            )
            cls.attributes.append(attr)
            store.save_model(model)
            return attr.model_dump()
    raise HTTPException(404, f"Class {class_id} not found")


@router.patch("/{model_id}/classes/{class_id}/attributes/{attr_id}")
async def update_attribute(model_id: str, class_id: str, attr_id: str, data: dict):
    model = _get_model_or_404(model_id)
    pkg = _current_package(model)
    for cls in pkg.classes:
        if cls.id == class_id:
            for attr in cls.attributes:
                if attr.id == attr_id:
                    for key in ("name", "label", "description", "data_type",
                                "enum_ref", "unit", "default_value", "logical_type"):
                        if key in data:
                            setattr(attr, key, data[key])
                    if "multiplicity" in data:
                        attr.multiplicity = Multiplicity(**data["multiplicity"])
                    store.save_model(model)
                    return attr.model_dump()
            raise HTTPException(404, f"Attribute {attr_id} not found")
    raise HTTPException(404, f"Class {class_id} not found")


@router.delete("/{model_id}/classes/{class_id}/attributes/{attr_id}")
async def delete_attribute(model_id: str, class_id: str, attr_id: str):
    model = _get_model_or_404(model_id)
    pkg = _current_package(model)
    for cls in pkg.classes:
        if cls.id == class_id:
            cls.attributes = [a for a in cls.attributes if a.id != attr_id]
            store.save_model(model)
            return {"status": "deleted"}
    raise HTTPException(404, f"Class {class_id} not found")


# ---- Association CRUD ----

@router.post("/{model_id}/associations")
async def add_association(model_id: str, req: AssociationCreateRequest):
    model = _get_model_or_404(model_id)
    pkg = _current_package(model)
    # Resolve class_name from class_ref for display/search
    cls_by_id = {c.id: c for c in pkg.classes}
    src_cls = cls_by_id.get(req.source_class_id)
    tgt_cls = cls_by_id.get(req.target_class_id)
    assoc = Association(
        id=str(uuid.uuid4()),
        name=req.name,
        label=req.label,
        source=AssociationEnd(
            class_ref=req.source_class_id,
            class_name=src_cls.name if src_cls else None,
            role_name=req.source_role,
            multiplicity=Multiplicity(lower=req.source_lower, upper=req.source_upper),
        ),
        target=AssociationEnd(
            class_ref=req.target_class_id,
            class_name=tgt_cls.name if tgt_cls else None,
            role_name=req.target_role,
            multiplicity=Multiplicity(lower=req.target_lower, upper=req.target_upper),
        ),
        association_type=req.association_type,
    )
    pkg.associations.append(assoc)
    store.save_model(model)
    return assoc.model_dump()


@router.patch("/{model_id}/associations/{assoc_id}")
async def update_association(model_id: str, assoc_id: str, data: dict):
    """V3.2: edit arbitrary M2/M1 association fields.

    Accepts partial update; any of these keys are applied:
      name, label, description, association_type, is_hierarchy, hierarchy_order,
      source_class_id, source_role, source_lower, source_upper,
      target_class_id, target_role, target_lower, target_upper.
    """
    model = _get_model_or_404(model_id)
    pkg = _current_package(model)
    assoc = next((a for a in pkg.associations if a.id == assoc_id), None)
    if assoc is None:
        raise HTTPException(404, f"Association {assoc_id} not found")
    cls_by_id = {c.id: c for c in pkg.classes}

    if "name" in data: assoc.name = data["name"]
    if "label" in data: assoc.label = data["label"]
    if "description" in data: assoc.description = data["description"]
    if "association_type" in data: assoc.association_type = data["association_type"]
    if "is_hierarchy" in data: assoc.is_hierarchy = bool(data["is_hierarchy"])
    if "hierarchy_order" in data: assoc.hierarchy_order = data["hierarchy_order"]

    if "source_class_id" in data:
        cid = data["source_class_id"]
        assoc.source.class_ref = cid
        src_cls = cls_by_id.get(cid)
        if src_cls: assoc.source.class_name = src_cls.name
    if "source_role" in data: assoc.source.role_name = data["source_role"]
    if "source_lower" in data or "source_upper" in data:
        lo = data.get("source_lower", assoc.source.multiplicity.lower)
        up = data.get("source_upper", assoc.source.multiplicity.upper)
        assoc.source.multiplicity = Multiplicity(lower=lo, upper=up)

    if "target_class_id" in data:
        cid = data["target_class_id"]
        assoc.target.class_ref = cid
        tgt_cls = cls_by_id.get(cid)
        if tgt_cls: assoc.target.class_name = tgt_cls.name
    if "target_role" in data: assoc.target.role_name = data["target_role"]
    if "target_lower" in data or "target_upper" in data:
        lo = data.get("target_lower", assoc.target.multiplicity.lower)
        up = data.get("target_upper", assoc.target.multiplicity.upper)
        assoc.target.multiplicity = Multiplicity(lower=lo, upper=up)

    store.save_model(model)
    return assoc.model_dump()


@router.delete("/{model_id}/associations/{assoc_id}")
async def delete_association(model_id: str, assoc_id: str):
    model = _get_model_or_404(model_id)
    pkg = _current_package(model)
    # V3.2: also clean up references from StructuralPattern.hierarchy_association_ids
    for sp in (pkg.structural_patterns or []):
        if sp.hierarchy_association_ids and assoc_id in sp.hierarchy_association_ids:
            sp.hierarchy_association_ids = [x for x in sp.hierarchy_association_ids if x != assoc_id]
    pkg.associations = [a for a in pkg.associations if a.id != assoc_id]
    store.save_model(model)
    return {"status": "deleted"}


# ---- Enumeration CRUD ----

@router.post("/{model_id}/enumerations")
async def add_enumeration(model_id: str, req: EnumerationCreateRequest):
    model = _get_model_or_404(model_id)
    pkg = _current_package(model)
    enum = Enumeration(
        id=str(uuid.uuid4()),
        name=req.name,
        label=req.label,
        description=req.description,
        literals=[
            EnumLiteral(id=str(uuid.uuid4()), name=l.get("name", ""), label=l.get("label", ""))
            for l in req.literals
        ],
    )
    pkg.enumerations.append(enum)
    store.save_model(model)
    return enum.model_dump()


@router.delete("/{model_id}/enumerations/{enum_id}")
async def delete_enumeration(model_id: str, enum_id: str):
    model = _get_model_or_404(model_id)
    pkg = _current_package(model)
    pkg.enumerations = [e for e in pkg.enumerations if e.id != enum_id]
    store.save_model(model)
    return {"status": "deleted"}


# ---- Validation ----

@router.post("/{model_id}/validate")
async def validate_model(model_id: str):
    """Local structural validation (M3 schema rules — type checking, multiplicity, etc.)."""
    model = _get_model_or_404(model_id)
    pkg = _current_package(model)
    m2 = store.get_m2_template(model.m2_template_id)
    result = validator.validate(pkg, m2)
    return result.model_dump()


@router.post("/validate-mof")
async def validate_mof_with_llm(data: dict):
    """LLM-powered MOF compliance check: analyzes M1 inheritance from M2 against MOF principles.

    Request: { m1_id: str, m2_id: str }
    Returns: { compliant: [...], issues: [...], recommendations: [...] }
    """
    import json as _json
    from backend.services.llm_client import get_active_client

    m1_id = data.get("m1_id")
    m2_id = data.get("m2_id")
    if not m1_id or not m2_id:
        raise HTTPException(400, "需要提供 m1_id 和 m2_id")

    m1 = store.get_model(m1_id)
    m2 = store.get_model(m2_id)
    if m1 is None or m2 is None:
        raise HTTPException(404, "M1 或 M2 模型未找到")

    m1_pkg = m1.versions[-1].package
    m2_pkg = m2.versions[-1].package

    # Prepare a compact summary for the LLM
    def summarize(pkg, is_m2=False):
        lines = []
        lines.append(f"Package: {pkg.name} ({pkg.label or ''})")
        for c in pkg.classes:
            parent = f" extends {c.parent_class_name}" if c.parent_class_name else ""
            abstract = " (abstract)" if c.is_abstract else ""
            lines.append(f"  Class {c.name}{parent}{abstract}")
            for a in c.attributes[:15]:
                inh = " [inherited]" if a.is_inherited else ""
                lines.append(f"    - {a.name}: {a.data_type}{' ('+a.unit+')' if a.unit else ''}{inh}")
            if len(c.attributes) > 15:
                lines.append(f"    ... +{len(c.attributes) - 15} more attrs")
        for e in pkg.enumerations[:10]:
            lits = [l.name for l in e.literals[:5]]
            lines.append(f"  Enum {e.name}: {', '.join(lits)}{'...' if len(e.literals) > 5 else ''}")
        for a in pkg.associations[:10]:
            lines.append(f"  Assoc {a.name}: {a.source.class_name} → {a.target.class_name} ({a.association_type})")
        return "\n".join(lines)

    m1_summary = summarize(m1_pkg)
    m2_summary = summarize(m2_pkg, is_m2=True)

    prompt = f"""You are an expert on MOF (Meta-Object Facility) methodology. Analyze whether the following M1 model properly specializes the M2 meta-model, following MOF principles.

===== M2 Meta-Model =====
{m2_summary}

===== M1 Model =====
{m1_summary}

Evaluate MOF compliance with focus on:

1. **Inheritance correctness**: Does each M1 class declare proper `extends <M2Class>`? Are M1 classes specializing existing M2 abstract types?
2. **Attribute inheritance**: Do M1 classes include their M2 parent's attributes (marked as `[inherited]`)? Are any required M2 attributes missing?
3. **Domain vs generic distinction**: M2 should be abstract/generic (e.g., "Equipment"), M1 should be concrete/domain-specific (e.g., "PumpedStorageUnit"). Are boundaries respected?
4. **Attribute type safety**: Do attribute data types in M1 align with M2's definition where inherited?
5. **Enumeration sharing**: Shared enums should be defined once (in M2 if universal) and referenced in M1, not duplicated.
6. **Association semantics**: composition/aggregation/association types used correctly?

Return valid JSON:
{{
  "compliant": [
    "✓ M1 Class 'X' correctly extends M2 Class 'Y' with all inherited attributes preserved",
    "✓ Enumeration 'Z' is properly defined in M2 and referenced in M1"
  ],
  "issues": [
    {{"severity": "high|medium|low", "target": "具体类/属性名", "problem": "具体问题描述"}}
  ],
  "recommendations": [
    {{"target": "具体类/属性名", "suggestion": "具体改进建议"}}
  ],
  "overall_score": 0-100,
  "summary": "一段中文总结"
}}

Be honest and specific. Use Chinese for issue/suggestion text.
"""

    try:
        client = get_active_client()
        response_text = await client.chat(
            system="You are a MOF methodology expert. Return only valid JSON.",
            user=prompt,
            temperature=0,
            max_tokens=8192,
            purpose="mof_validation",
        )
        # Parse JSON with our robust extractor
        from backend.services.ai_extractor import _extract_json
        result = _extract_json(response_text)
        return result
    except Exception as e:
        raise HTTPException(500, f"MOF校验失败: {str(e)[:300]}")


# ---- Versions ----

@router.get("/{model_id}/versions")
async def list_versions(model_id: str):
    model = _get_model_or_404(model_id)
    return {
        "current_version": model.current_version,
        "versions": [
            {"version": v.version, "created_at": v.created_at.isoformat(), "changelog": v.changelog}
            for v in model.versions
        ],
    }


@router.post("/{model_id}/versions")
async def create_version(model_id: str, req: VersionCreateRequest):
    v = version_manager.create_version(model_id, req.changelog)
    return {"version": v.version, "created_at": v.created_at.isoformat()}


@router.post("/{model_id}/versions/{version}/activate")
async def switch_to_version(model_id: str, version: str):
    """Switch current_version to an older snapshot."""
    model = _get_model_or_404(model_id)
    matched = next((v for v in model.versions if v.version == version), None)
    if matched is None:
        raise HTTPException(404, f"Version {version} not found")
    model.current_version = version
    # Move the matched version to the end so it's treated as "current" latest
    model.versions = [v for v in model.versions if v.version != version] + [matched]
    store.save_model(model)
    return {"status": "switched", "current_version": version}


# ---- M1/M2 Package publish lifecycle (V3.0 § 2.4) ----
# Methodology requires M1/M2 Packages to have an explicit publish status so
# downstream Object Centers can differentiate "in-progress edits" from "official
# versions safe to reference". States: draft → review → published → deprecated.

_VALID_STATUSES = ("draft", "review", "published", "deprecated")
_ALLOWED_TRANSITIONS = {
    "draft":      ("review", "published"),        # skip-review also allowed (small team)
    "review":     ("draft", "published"),         # back to draft or promote
    "published":  ("deprecated",),                # published is immutable, only deprecate
    "deprecated": (),                             # terminal
}


@router.post("/{model_id}/publish-status")
async def set_publish_status(model_id: str, data: dict):
    """Transition the package's publish_status to a new state.

    Request body: {"target_status": "review|published|deprecated|draft",
                   "published_by": "optional user identifier"}
    """
    model = _get_model_or_404(model_id)
    target = (data or {}).get("target_status", "").strip()

    if target not in _VALID_STATUSES:
        raise HTTPException(
            400,
            f"target_status 必须是 {_VALID_STATUSES} 之一",
        )

    if not model.versions:
        raise HTTPException(400, "模型没有版本, 无法设置发布状态")

    pkg = model.versions[-1].package
    current = getattr(pkg, "publish_status", None) or "draft"

    if target == current:
        return {"status": "noop", "publish_status": current}

    # Validate transition
    allowed = _ALLOWED_TRANSITIONS.get(current, ())
    if target not in allowed:
        raise HTTPException(
            400,
            f"不允许从 '{current}' 直接转到 '{target}'。"
            f"允许的转换: {current} → {allowed}",
        )

    pkg.publish_status = target
    if target == "published":
        pkg.published_at = datetime.now().isoformat()
        pkg.published_by = (data or {}).get("published_by", "system")
    # If reverting from published (only via deprecated), keep the published_at record

    store.save_model(model)
    return {
        "status": "updated",
        "publish_status": target,
        "published_at": pkg.published_at,
        "published_by": pkg.published_by,
    }


@router.get("/{model_id}/publish-status")
async def get_publish_status(model_id: str):
    """Return the current publish status of the model's latest version Package."""
    model = _get_model_or_404(model_id)
    if not model.versions:
        return {"publish_status": "draft", "published_at": None, "published_by": None}
    pkg = model.versions[-1].package
    return {
        "publish_status": getattr(pkg, "publish_status", None) or "draft",
        "published_at": getattr(pkg, "published_at", None),
        "published_by": getattr(pkg, "published_by", None),
        "allowed_transitions": list(_ALLOWED_TRANSITIONS.get(
            getattr(pkg, "publish_status", None) or "draft", ()
        )),
    }


# ============================================================================
#                       V3.1 Synonym detection + merge
# ============================================================================

@router.post("/{model_id}/detect-synonyms")
async def detect_synonyms(model_id: str, use_llm: bool = False):
    """V3.1: find groups of M1 classes that look like synonyms (same concept).

    Layer 1 (rule) always runs: strips prefixes/suffixes + Levenshtein ≤ 2.
    Layer 2 (LLM) optional: one LLM call for semantic equivalence.
    Returns groups + per-class snapshot for UI rendering.
    """
    from backend.services.synonym_detector import (
        detect_synonyms_rule, detect_synonyms_llm, merge_groups,
    )
    model = _get_model_or_404(model_id)
    pkg = _current_package(model)
    # Snapshot classes as dicts (id/name/label/attrs count) for detector
    cls_dicts = [
        {"id": c.id, "name": c.name, "label": c.label,
         "attr_count": len(c.attributes or [])}
        for c in pkg.classes
    ]

    rule_groups = detect_synonyms_rule(cls_dicts)
    llm_groups = []
    if use_llm:
        try:
            llm_groups = await detect_synonyms_llm(cls_dicts)
        except Exception as e:
            llm_groups = []
    final_groups = merge_groups(rule_groups, llm_groups)

    # Attach class details for UI
    by_id = {c["id"]: c for c in cls_dicts}
    groups_rich = []
    for g in final_groups:
        items = [by_id[cid] for cid in g if cid in by_id]
        groups_rich.append({
            "class_ids": g,
            "classes": items,
            # Suggest keeping the class with the most attributes as default
            "suggest_keep_id": max(items, key=lambda x: x.get("attr_count", 0))["id"]
                if items else (g[0] if g else None),
        })

    return {
        "total_classes": len(cls_dicts),
        "rule_groups": len(rule_groups),
        "llm_groups": len(llm_groups),
        "groups": groups_rich,
        "used_llm": use_llm,
    }


# ============================================================================
#                  V3.2 Structural Pattern CRUD + M1 impact
# ============================================================================

def _find_pattern(pkg: Package, pattern_id: str):
    for sp in (pkg.structural_patterns or []):
        if sp.id == pattern_id:
            return sp
    return None


@router.post("/{m2_id}/structural-patterns/validate")
async def validate_pattern(m2_id: str, req: dict):
    """Pure validation — returns errors list, no side effect.
    Editor calls this on every change to show live feedback.
    """
    from backend.services.pattern_manager import PatternRequest, validate_request
    model = _get_model_or_404(m2_id)
    pkg = _current_package(model)
    pr = PatternRequest.from_dict(req)
    errors = validate_request(pr, pkg)
    return {"valid": len(errors) == 0, "errors": errors}


@router.post("/{m2_id}/structural-patterns/preview-impact")
async def preview_pattern_impact(m2_id: str, req: dict):
    """Dry-run: given a new/updated pattern request, diff against committed state
    and scan all bound M1 models for impacted classes. No store write.

    req = {
      "pattern_id": "...",           # optional; if present, edit; else new
      "pattern": { ...PatternRequest... }
    }
    """
    from backend.services.pattern_manager import (
        PatternRequest, validate_request, diff_patterns, scan_m1_impact,
    )
    model = _get_model_or_404(m2_id)
    pkg = _current_package(model)
    pattern_id = req.get("pattern_id")
    pr = PatternRequest.from_dict(req.get("pattern") or {})
    errors = validate_request(pr, pkg)
    if errors:
        return {"valid": False, "errors": errors, "changes": [], "m1_impacts": []}
    old = _find_pattern(pkg, pattern_id) if pattern_id else None
    changes = diff_patterns(old, pr, pkg)
    impacts = scan_m1_impact(changes, pkg, m2_id, store)
    return {
        "valid": True,
        "errors": [],
        "changes": [
            {"kind": c.kind, "severity": c.severity, "description": c.description, "extra": c.extra}
            for c in changes
        ],
        "m1_impacts": impacts,
    }


@router.post("/{m2_id}/structural-patterns")
async def create_pattern(m2_id: str, req: dict):
    """Create a new StructuralPattern in the M2 package.
    req: {
      pattern: { ...PatternRequest... },
      m1_migrations: [...]       # optional; applied via atomic transaction
    }
    Returns: {pattern_id, wire_info, migrations_summary}
    """
    from backend.services.pattern_manager import (
        PatternRequest, validate_request, build_pattern_entity,
        sync_meta_structure_on_classes, auto_wire_hierarchy_edges,
        apply_m1_migrations_and_save, AtomicModelWrite,
    )
    model = _get_model_or_404(m2_id)
    pkg = _current_package(model)
    pr = PatternRequest.from_dict(req.get("pattern") or {})
    errors = validate_request(pr, pkg)
    if errors:
        raise HTTPException(400, {"msg": "pattern 校验失败", "errors": errors})

    pattern = build_pattern_entity(pr)
    pkg.structural_patterns.append(pattern)
    sync_meta_structure_on_classes(pkg, pattern, pr)
    wire_info = auto_wire_hierarchy_edges(pkg, pattern, pr)

    migrations = req.get("m1_migrations") or []
    if migrations:
        result = apply_m1_migrations_and_save(
            migrations, model, store, m2_pkg_for_rename=pkg,
        )
        if not result.get("m2_saved"):
            raise HTTPException(500, {"msg": "原子保存失败已回滚", "details": result})
    else:
        # Just save M2
        with AtomicModelWrite(store, [model.id]) as tx:
            tx.save(model)
        result = {"updated": [], "failed": [], "m2_saved": True, "m1_saved_count": 0}

    return {
        "pattern_id": pattern.id,
        "wire_info": wire_info,
        "migrations": result,
    }


@router.put("/{m2_id}/structural-patterns/{pattern_id}")
async def update_pattern(m2_id: str, pattern_id: str, req: dict):
    """Update (replace) an existing StructuralPattern.
    req: {
      pattern: { ...PatternRequest... },
      m1_migrations: [...]
    }
    """
    from backend.services.pattern_manager import (
        PatternRequest, validate_request, build_pattern_entity,
        sync_meta_structure_on_classes, auto_wire_hierarchy_edges,
        apply_m1_migrations_and_save, AtomicModelWrite,
    )
    model = _get_model_or_404(m2_id)
    pkg = _current_package(model)
    old = _find_pattern(pkg, pattern_id)
    if old is None:
        raise HTTPException(404, f"Pattern {pattern_id} not found in M2 {m2_id}")

    pr = PatternRequest.from_dict(req.get("pattern") or {})
    errors = validate_request(pr, pkg)
    if errors:
        raise HTTPException(400, {"msg": "pattern 校验失败", "errors": errors})

    # Replace pattern in-place (keep id)
    new_pattern = build_pattern_entity(pr, existing_id=pattern_id)
    for i, sp in enumerate(pkg.structural_patterns):
        if sp.id == pattern_id:
            pkg.structural_patterns[i] = new_pattern
            break
    sync_meta_structure_on_classes(pkg, new_pattern, pr)
    wire_info = auto_wire_hierarchy_edges(pkg, new_pattern, pr)

    migrations = req.get("m1_migrations") or []
    if migrations:
        result = apply_m1_migrations_and_save(
            migrations, model, store, m2_pkg_for_rename=pkg,
        )
        if not result.get("m2_saved"):
            raise HTTPException(500, {"msg": "原子保存失败已回滚", "details": result})
    else:
        with AtomicModelWrite(store, [model.id]) as tx:
            tx.save(model)
        result = {"updated": [], "failed": [], "m2_saved": True, "m1_saved_count": 0}

    return {"pattern_id": new_pattern.id, "wire_info": wire_info, "migrations": result}


@router.delete("/{m2_id}/structural-patterns/{pattern_id}")
async def delete_pattern(m2_id: str, pattern_id: str, keep_classes: bool = True):
    """Remove a StructuralPattern. By default keeps the participating MOFClasses
    (just clears their meta_structure_* fields and demotes hierarchy assocs).
    Set keep_classes=false to remove the participating classes too (dangerous).
    """
    from backend.services.pattern_manager import AtomicModelWrite
    model = _get_model_or_404(m2_id)
    pkg = _current_package(model)
    old = _find_pattern(pkg, pattern_id)
    if old is None:
        raise HTTPException(404, f"Pattern {pattern_id} not found")

    part_ids = set(old.participating_class_ids or [])
    hier_ids = set(old.hierarchy_association_ids or [])

    # Demote hierarchy edges
    for a in pkg.associations:
        if a.id in hier_ids:
            a.is_hierarchy = False
            a.hierarchy_order = None

    # Clear meta_structure_* on classes (always)
    for c in pkg.classes:
        if c.meta_structure_id == pattern_id:
            c.meta_structure_id = None
            c.meta_structure_role = None
            c.meta_structure_level = None

    if not keep_classes:
        # Remove participating classes + their associations
        pkg.classes = [c for c in pkg.classes if c.id not in part_ids]
        pkg.associations = [
            a for a in pkg.associations
            if getattr(a.source, "class_ref", None) not in part_ids
            and getattr(a.target, "class_ref", None) not in part_ids
        ]

    # Remove pattern
    pkg.structural_patterns = [sp for sp in pkg.structural_patterns if sp.id != pattern_id]

    with AtomicModelWrite(store, [model.id]) as tx:
        tx.save(model)

    return {
        "status": "deleted",
        "pattern_id": pattern_id,
        "kept_classes": keep_classes,
        "classes_affected": len(part_ids),
        "edges_demoted": len(hier_ids),
    }


@router.get("/{model_id}/quality-check")
async def quality_check(model_id: str):
    """V3.1 Phase D: run quality sanity checks on the model's current package."""
    from backend.services.quality_checker import check_m1_package, summarize
    model = _get_model_or_404(model_id)
    pkg = _current_package(model)
    pkg_dump = pkg.model_dump() if hasattr(pkg, "model_dump") else pkg
    # Estimate total doc chars from sources
    total_chars = 0
    for did in (model.source_document_ids or []):
        m = store.get_document_meta(did)
        if m:
            total_chars += m.get("char_count", 0)
    findings = check_m1_package(pkg_dump, total_doc_chars=total_chars)
    return {"findings": findings, "summary": summarize(findings)}


@router.post("/{model_id}/merge-classes")
async def merge_classes(model_id: str, req: dict):
    """V3.1: apply merges. req = {"merges": [{"keep": id, "drop": [id, ...]}]}
    Returns summary with kept/dropped ids + refs_rewritten count.
    """
    from backend.services.synonym_detector import merge_classes_in_package
    model = _get_model_or_404(model_id)
    pkg = _current_package(model)
    merges = req.get("merges") or []
    if not merges:
        raise HTTPException(400, "merges array is required")

    summary = merge_classes_in_package(pkg, merges)
    store.save_model(model)
    return summary
