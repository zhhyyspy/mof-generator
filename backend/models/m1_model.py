"""
M1 Model: a versioned, domain-specific specialization of an M2 template.
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from .m3_schema import Package


class M1ModelVersion(BaseModel):
    version: str
    created_at: datetime = Field(default_factory=datetime.now)
    created_by: str = "system"
    changelog: Optional[str] = None
    package: Package


class M1Model(BaseModel):
    id: str
    name: str
    label: Optional[str] = None
    description: Optional[str] = None
    m2_template_id: str
    source_document_ids: list[str] = Field(default_factory=list)
    current_version: str = "1.0"
    versions: list[M1ModelVersion] = Field(default_factory=list)
    status: str = "draft"  # draft | review | published
