"""Request/response schemas for API endpoints."""
from typing import Optional

from pydantic import BaseModel, Field


class DocumentUploadResponse(BaseModel):
    id: str
    filename: str
    content_preview: str
    char_count: int
    status: str


class ExtractionRequest(BaseModel):
    document_ids: list[str]
    m2_template_id: Optional[str] = None  # Not required for docs→M1 flow
    model_name: Optional[str] = None
    model_label: Optional[str] = None
    auto_start: bool = False  # If False, task waits until /start/{task_id} is called


class ExtractionProgress(BaseModel):
    task_id: str
    step: str
    progress: float
    message: str
    partial_result: Optional[dict] = None


class ExtractionResult(BaseModel):
    task_id: str
    status: str
    classes_found: int = 0
    attributes_found: int = 0
    associations_found: int = 0
    enumerations_found: int = 0
    model_id: Optional[str] = None
    model_draft: Optional[dict] = None
    confidence_notes: list[str] = Field(default_factory=list)
    error: Optional[str] = None


class RefineRequest(BaseModel):
    model_id: str
    user_message: str


class ValidationResult(BaseModel):
    is_valid: bool
    errors: list[dict] = Field(default_factory=list)
    warnings: list[dict] = Field(default_factory=list)


class ExportRequest(BaseModel):
    model_id: str
    version: Optional[str] = None
    format: str  # json | yaml | mof_text


class ModelCreateRequest(BaseModel):
    name: str
    label: Optional[str] = None
    description: Optional[str] = None
    m2_template_id: str


class ClassCreateRequest(BaseModel):
    name: str
    label: Optional[str] = None
    description: Optional[str] = None
    parent_class_ref: Optional[str] = None
    parent_class_name: Optional[str] = None


class AttributeCreateRequest(BaseModel):
    name: str
    label: Optional[str] = None
    description: Optional[str] = None
    data_type: str = "String"
    enum_ref: Optional[str] = None
    unit: Optional[str] = None
    multiplicity_lower: int = 1
    multiplicity_upper: int = 1
    default_value: Optional[str] = None


class AssociationCreateRequest(BaseModel):
    name: str
    label: Optional[str] = None
    source_class_id: str
    source_role: Optional[str] = None
    source_lower: int = 1
    source_upper: int = 1
    target_class_id: str
    target_role: Optional[str] = None
    target_lower: int = 0
    target_upper: int = -1
    association_type: str = "association"


class EnumerationCreateRequest(BaseModel):
    name: str
    label: Optional[str] = None
    description: Optional[str] = None
    literals: list[dict] = Field(default_factory=list)


class VersionCreateRequest(BaseModel):
    changelog: Optional[str] = None
