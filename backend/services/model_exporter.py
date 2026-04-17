"""Export M1 models to JSON, YAML, and MOF text formats."""
from __future__ import annotations

import json
from typing import Optional

import yaml

from backend.models.m1_model import M1Model
from backend.models.m3_schema import Package, MOFClass, Attribute, Association, Enumeration


class ModelExporter:

    def export(self, model: M1Model, fmt: str, version: Optional[str] = None) -> tuple[str, str]:
        """Returns (content_string, filename)."""
        pkg = self._get_version_package(model, version)
        ver = version or model.current_version

        if fmt == "json":
            return self._to_json(pkg), f"{model.name}_v{ver}.json"
        elif fmt == "yaml":
            return self._to_yaml(pkg), f"{model.name}_v{ver}.yaml"
        elif fmt == "mof_text":
            return self._to_mof_text(model, pkg), f"{model.name}_v{ver}.mof"
        else:
            raise ValueError(f"Unsupported export format: {fmt}")

    def _get_version_package(self, model: M1Model, version: Optional[str]) -> Package:
        if version:
            for v in model.versions:
                if v.version == version:
                    return v.package
            raise ValueError(f"Version {version} not found")
        if model.versions:
            return model.versions[-1].package
        raise ValueError("Model has no versions")

    def _to_json(self, pkg: Package) -> str:
        return json.dumps(pkg.model_dump(), indent=2, ensure_ascii=False)

    def _to_yaml(self, pkg: Package) -> str:
        return yaml.dump(
            pkg.model_dump(),
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        )

    def _to_mof_text(self, model: M1Model, pkg: Package) -> str:
        lines = []
        lines.append(f"// M1 Model: {model.label or model.name}")
        lines.append(f"// Version: {model.current_version}")
        lines.append(f"// M2 Template: {model.m2_template_id}")
        lines.append(f"// Status: {model.status}")
        lines.append("")
        lines.append(f"package {pkg.name} {{")
        lines.append("")

        # Enumerations
        for enum in pkg.enumerations:
            lines.append(f"  enumeration {enum.name} {{")
            if enum.label:
                lines.append(f"    // {enum.label}")
            for lit in enum.literals:
                label_comment = f"  // {lit.label}" if lit.label else ""
                lines.append(f"    {lit.name}{label_comment}")
            lines.append("  }")
            lines.append("")

        # ComplexTypes
        for ct in pkg.complex_types:
            lines.append(f"  complexType {ct.name} {{")
            if ct.label:
                lines.append(f"    // {ct.label}")
            for attr in ct.attributes:
                lines.append(f"    {self._format_attribute(attr)}")
            lines.append("  }")
            lines.append("")

        # Classes
        for cls in pkg.classes:
            extends = f" extends {cls.parent_class_name}" if cls.parent_class_name else ""
            abstract = "abstract " if cls.is_abstract else ""
            lines.append(f"  {abstract}class {cls.name}{extends} {{")
            if cls.label:
                lines.append(f"    // {cls.label}")
            if cls.description:
                lines.append(f"    // {cls.description}")
            lines.append("")
            for attr in cls.attributes:
                inherited = " [inherited]" if attr.is_inherited else ""
                lines.append(f"    {self._format_attribute(attr)}{inherited}")
            if cls.constraints:
                lines.append("")
                for con in cls.constraints:
                    lines.append(f"    constraint {con.name} {{ {con.expression} }}")
            lines.append("  }")
            lines.append("")

        # Associations
        for assoc in pkg.associations:
            src_mult = assoc.source.multiplicity.to_notation()
            tgt_mult = assoc.target.multiplicity.to_notation()
            src_name = assoc.source.class_name or assoc.source.class_ref
            tgt_name = assoc.target.class_name or assoc.target.class_ref
            atype = assoc.association_type
            lines.append(f"  {atype} {assoc.name} {{")
            if assoc.label:
                lines.append(f"    // {assoc.label}")
            lines.append(f"    {src_name} {src_mult} --> {tgt_name} {tgt_mult}")
            lines.append("  }")
            lines.append("")

        lines.append("}")
        return "\n".join(lines)

    def _format_attribute(self, attr: Attribute) -> str:
        mult = attr.multiplicity.to_notation()
        unit = f' {{ unit = "{attr.unit}" }}' if attr.unit else ""
        enum_note = f" -> {attr.enum_ref}" if attr.enum_ref else ""
        dt = getattr(attr.data_type, 'value', attr.data_type)
        return f"attribute {attr.name} : {dt} {mult}{enum_note}{unit}"


exporter = ModelExporter()
