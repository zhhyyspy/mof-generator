"""
M3 Meta-Meta Model: defines the modeling language itself.
All M2 templates and M1 models are composed exclusively of these constructs.

M3 Concepts: Class, Attribute, DataType, ComplexType, Association,
Multiplicity, Enumeration, EnumLiteral, Constraint, Package.
"""
from __future__ import annotations

from enum import Enum as PyEnum
from typing import Optional

from pydantic import BaseModel, Field

import uuid


def _uid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Primitive data types
# ---------------------------------------------------------------------------

class PrimitiveDataType(str, PyEnum):
    STRING = "String"
    FLOAT = "Float"
    INTEGER = "Integer"
    DATE = "Date"
    BOOLEAN = "Boolean"
    ENUM = "Enum"

    def __str__(self) -> str:
        return self.value


# ---------------------------------------------------------------------------
# Multiplicity
# ---------------------------------------------------------------------------

class Multiplicity(BaseModel):
    # AI often returns -1 for lower meaning "unspecified" — we clamp to 0
    lower: int = 1
    upper: int = 1

    def model_post_init(self, __context):
        # Clamp negative lower to 0 (AI confusion: thinks -1 means "unlimited")
        if self.lower < 0:
            self.lower = 0
        # upper=-1 means unlimited; anything else negative = unlimited
        if self.upper < -1:
            self.upper = -1
        # Ensure lower <= upper (unless upper is -1 meaning unlimited)
        if self.upper != -1 and self.lower > self.upper:
            self.lower = self.upper

    @classmethod
    def one(cls) -> Multiplicity:
        return cls(lower=1, upper=1)

    @classmethod
    def optional(cls) -> Multiplicity:
        return cls(lower=0, upper=1)

    @classmethod
    def many(cls) -> Multiplicity:
        return cls(lower=0, upper=-1)

    @classmethod
    def one_or_more(cls) -> Multiplicity:
        return cls(lower=1, upper=-1)

    def to_notation(self) -> str:
        u = "*" if self.upper == -1 else str(self.upper)
        if self.lower == self.upper:
            return f"[{self.lower}]"
        return f"[{self.lower}..{u}]"


# ---------------------------------------------------------------------------
# Enumeration
# ---------------------------------------------------------------------------

class EnumLiteral(BaseModel):
    id: str = Field(default_factory=_uid)
    name: str
    label: Optional[str] = None
    value: Optional[str] = None


class Enumeration(BaseModel):
    id: str = Field(default_factory=_uid)
    name: str
    label: Optional[str] = None
    description: Optional[str] = None
    literals: list[EnumLiteral] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Attribute
# ---------------------------------------------------------------------------

class Attribute(BaseModel):
    id: str = Field(default_factory=_uid)
    name: str
    label: Optional[str] = None
    description: Optional[str] = None
    data_type: PrimitiveDataType = PrimitiveDataType.STRING
    enum_ref: Optional[str] = None
    complex_type_ref: Optional[str] = None
    multiplicity: Multiplicity = Field(default_factory=Multiplicity.one)
    unit: Optional[str] = None
    default_value: Optional[str] = None
    is_inherited: bool = False


# ---------------------------------------------------------------------------
# ComplexType (structured attribute group)
# ---------------------------------------------------------------------------

class ComplexType(BaseModel):
    id: str = Field(default_factory=_uid)
    name: str
    label: Optional[str] = None
    description: Optional[str] = None
    attributes: list[Attribute] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Constraint
# ---------------------------------------------------------------------------

class Constraint(BaseModel):
    id: str = Field(default_factory=_uid)
    name: str
    expression: str
    target_attribute: Optional[str] = None
    description: Optional[str] = None


# ---------------------------------------------------------------------------
# Association
# ---------------------------------------------------------------------------

class AssociationEnd(BaseModel):
    class_ref: str
    class_name: Optional[str] = None
    role_name: Optional[str] = None
    multiplicity: Multiplicity = Field(default_factory=Multiplicity.one)
    navigable: bool = True


class Association(BaseModel):
    id: str = Field(default_factory=_uid)
    name: str
    label: Optional[str] = None
    description: Optional[str] = None
    source: AssociationEnd
    target: AssociationEnd
    association_type: str = "association"  # association | composition | aggregation


# ---------------------------------------------------------------------------
# MOFClass
# ---------------------------------------------------------------------------

class MOFClass(BaseModel):
    id: str = Field(default_factory=_uid)
    name: str
    label: Optional[str] = None
    description: Optional[str] = None
    parent_class_ref: Optional[str] = None
    parent_class_name: Optional[str] = None
    is_abstract: bool = False
    attributes: list[Attribute] = Field(default_factory=list)
    constraints: list[Constraint] = Field(default_factory=list)
    # Optional hint captured during M1 extraction — feeds later M2 hierarchy detection.
    # Not user-editable; purely a side-channel between extract_m1 and derive_m2.
    # Schema: {"theme_hint": str, "level_hint": str, "parent_name_hint": str}
    hierarchy_hint: Optional[dict] = None


# ---------------------------------------------------------------------------
# Package (top-level container)
# ---------------------------------------------------------------------------

class Package(BaseModel):
    id: str = Field(default_factory=_uid)
    name: str
    label: Optional[str] = None
    description: Optional[str] = None
    classes: list[MOFClass] = Field(default_factory=list)
    complex_types: list[ComplexType] = Field(default_factory=list)
    enumerations: list[Enumeration] = Field(default_factory=list)
    associations: list[Association] = Field(default_factory=list)
    sub_packages: list[Package] = Field(default_factory=list)
