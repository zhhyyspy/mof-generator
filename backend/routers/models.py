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
    model = _get_model_or_404(model_id)
    pkg = _current_package(model)
    m2 = store.get_m2_template(model.m2_template_id)
    result = validator.validate(pkg, m2)
    return result.model_dump()


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
