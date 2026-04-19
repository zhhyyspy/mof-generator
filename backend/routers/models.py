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
    m1_model = store.get_model(source_m1_id)
    if m1_model:
        m1_model.m2_template_id = m2_id

        # Build a per-M1-class lookup from class_mappings (includes level info from
        # Phase 2.5 hierarchy detection, if present):
        # { m1_class_name: {"m2_parent_name": str, "level": str | None, "m2_level_enum_id": str | None} }
        m1_mapping_lookup: dict[str, dict] = {}
        for m in class_mappings:
            n = m.get("m1_class_name")
            if not n:
                continue
            m1_mapping_lookup[n] = {
                "m2_parent_name": m.get("m2_parent_name"),
                "level": m.get("level"),
                "m2_level_enum_id": m.get("m2_level_enum_id"),
            }

        pkg_m1 = m1_model.versions[-1].package
        m2_class_ids = {c.name: c.id for c in pkg.classes}
        # For each M2 class, record its (attr_name -> attr_obj) map so we can copy
        # inherited attrs faithfully on M1 side (incl. the level enum attr).
        m2_attrs_by_class: dict[str, dict] = {
            c.name: {a.name: a for a in c.attributes} for c in pkg.classes
        }

        for cls in pkg_m1.classes:
            info = m1_mapping_lookup.get(cls.name)
            if not info:
                continue

            parent_name = info["m2_parent_name"]
            if not parent_name:
                continue

            cls.parent_class_name = parent_name
            cls.parent_class_ref = m2_class_ids.get(parent_name)

            m2_attrs = m2_attrs_by_class.get(parent_name, {})
            m2_attr_names = set(m2_attrs.keys())

            # Mark M1 attributes that share a name with M2 attributes as inherited
            existing_m1_attr_names = set()
            for attr in cls.attributes:
                existing_m1_attr_names.add(attr.name)
                if attr.name in m2_attr_names:
                    attr.is_inherited = True

            # ----- Backfill the `level` attribute when M2 has a hierarchy -----
            # If Phase 2.5 detected a hierarchy for this M2 class, M2 now has a
            # `level` enum attribute. Propagate it to the M1 child, with a default
            # value set to the assigned level (unless whole_tree).
            assigned_level = info.get("level")
            m2_level_attr = m2_attrs.get("level")
            if m2_level_attr is not None:
                # Respect any existing `level` attr the user may have already edited
                existing_level = next(
                    (a for a in cls.attributes if a.name == "level"), None
                )
                if existing_level is not None:
                    existing_level.is_inherited = True
                    existing_level.enum_ref = m2_level_attr.enum_ref
                    existing_level.data_type = m2_level_attr.data_type
                    # Only overwrite default if not previously set by user
                    if assigned_level and assigned_level != "whole_tree" and not existing_level.default_value:
                        existing_level.default_value = assigned_level
                else:
                    # Append as inherited — use same structure as the M2 attribute
                    new_attr = Attribute(
                        name="level",
                        label=m2_level_attr.label or "层级",
                        description=m2_level_attr.description,
                        data_type=m2_level_attr.data_type,
                        enum_ref=m2_level_attr.enum_ref,
                        multiplicity=Multiplicity(
                            lower=m2_level_attr.multiplicity.lower,
                            upper=m2_level_attr.multiplicity.upper,
                        ),
                        default_value=(
                            assigned_level
                            if assigned_level and assigned_level != "whole_tree"
                            else None
                        ),
                        is_inherited=True,
                    )
                    cls.attributes.append(new_attr)

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
                                "enum_ref", "unit", "default_value"):
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
    assoc = Association(
        id=str(uuid.uuid4()),
        name=req.name,
        label=req.label,
        source=AssociationEnd(
            class_ref=req.source_class_id,
            role_name=req.source_role,
            multiplicity=Multiplicity(lower=req.source_lower, upper=req.source_upper),
        ),
        target=AssociationEnd(
            class_ref=req.target_class_id,
            role_name=req.target_role,
            multiplicity=Multiplicity(lower=req.target_lower, upper=req.target_upper),
        ),
        association_type=req.association_type,
    )
    pkg.associations.append(assoc)
    store.save_model(model)
    return assoc.model_dump()


@router.delete("/{model_id}/associations/{assoc_id}")
async def delete_association(model_id: str, assoc_id: str):
    model = _get_model_or_404(model_id)
    pkg = _current_package(model)
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
