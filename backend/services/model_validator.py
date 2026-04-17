"""Validate an M1 model Package against M3 schema rules."""
from __future__ import annotations

from backend.models.m3_schema import Package, PrimitiveDataType
from backend.models.m2_template import M2Template
from backend.models.api_schemas import ValidationResult


class ModelValidator:

    def validate(self, package: Package, m2_template: M2Template | None = None) -> ValidationResult:
        errors = []
        warnings = []

        class_ids = {c.id for c in package.classes}
        class_names = set()
        enum_ids = {e.id for e in package.enumerations}
        complex_ids = {ct.id for ct in package.complex_types}

        # --- Class-level checks ---
        for cls in package.classes:
            # Unique class names
            if cls.name in class_names:
                errors.append({
                    "element_id": cls.id,
                    "element_type": "class",
                    "message": f"Duplicate class name: {cls.name}",
                    "severity": "error",
                })
            class_names.add(cls.name)

            # Must have a name
            if not cls.name.strip():
                errors.append({
                    "element_id": cls.id,
                    "element_type": "class",
                    "message": "Class name cannot be empty",
                    "severity": "error",
                })

            # --- Attribute-level checks ---
            attr_names_in_class = set()
            for attr in cls.attributes:
                # Unique attribute names within class
                if attr.name in attr_names_in_class:
                    errors.append({
                        "element_id": attr.id,
                        "element_type": "attribute",
                        "message": f"Duplicate attribute name '{attr.name}' in class '{cls.name}'",
                        "severity": "error",
                    })
                attr_names_in_class.add(attr.name)

                # Valid data type
                try:
                    PrimitiveDataType(attr.data_type)
                except ValueError:
                    errors.append({
                        "element_id": attr.id,
                        "element_type": "attribute",
                        "message": f"Invalid data type '{attr.data_type}' for attribute '{attr.name}'",
                        "severity": "error",
                    })

                # Enum ref must exist if type is Enum
                if attr.data_type == PrimitiveDataType.ENUM and attr.enum_ref:
                    if attr.enum_ref not in enum_ids:
                        warnings.append({
                            "element_id": attr.id,
                            "element_type": "attribute",
                            "message": f"Enum reference '{attr.enum_ref}' not found for attribute '{attr.name}'",
                            "severity": "warning",
                        })

                # Multiplicity validity
                if attr.multiplicity.upper != -1 and attr.multiplicity.lower > attr.multiplicity.upper:
                    errors.append({
                        "element_id": attr.id,
                        "element_type": "attribute",
                        "message": f"Invalid multiplicity for '{attr.name}': lower ({attr.multiplicity.lower}) > upper ({attr.multiplicity.upper})",
                        "severity": "error",
                    })

        # --- Enumeration checks ---
        enum_names = set()
        for enum in package.enumerations:
            if enum.name in enum_names:
                errors.append({
                    "element_id": enum.id,
                    "element_type": "enumeration",
                    "message": f"Duplicate enumeration name: {enum.name}",
                    "severity": "error",
                })
            enum_names.add(enum.name)

            if not enum.literals:
                warnings.append({
                    "element_id": enum.id,
                    "element_type": "enumeration",
                    "message": f"Enumeration '{enum.name}' has no literals",
                    "severity": "warning",
                })

        # --- Association checks ---
        for assoc in package.associations:
            if assoc.source.class_ref not in class_ids:
                errors.append({
                    "element_id": assoc.id,
                    "element_type": "association",
                    "message": f"Source class '{assoc.source.class_ref}' not found for association '{assoc.name}'",
                    "severity": "error",
                })
            if assoc.target.class_ref not in class_ids:
                errors.append({
                    "element_id": assoc.id,
                    "element_type": "association",
                    "message": f"Target class '{assoc.target.class_ref}' not found for association '{assoc.name}'",
                    "severity": "error",
                })

        # --- M2 inheritance checks ---
        if m2_template:
            m2_class_names = {c.name for c in m2_template.package.classes}
            for cls in package.classes:
                if cls.parent_class_name and cls.parent_class_name not in m2_class_names:
                    warnings.append({
                        "element_id": cls.id,
                        "element_type": "class",
                        "message": f"Parent class '{cls.parent_class_name}' not found in M2 template",
                        "severity": "warning",
                    })

        return ValidationResult(
            is_valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
        )


validator = ModelValidator()
