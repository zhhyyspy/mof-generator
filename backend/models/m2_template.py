"""
M2 Meta Model Templates.
Pre-loaded from JSON files in backend/data/m2_templates/.
"""
from typing import Optional

from pydantic import BaseModel

from .m3_schema import Package


class M2Template(BaseModel):
    id: str
    name: str
    label: Optional[str] = None
    description: Optional[str] = None
    version: str = "1.0"
    domain: Optional[str] = None
    package: Package
