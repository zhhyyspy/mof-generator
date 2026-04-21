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

        # --- Metastructure integrity checks (V3.0 methodology § 11.3.1) ---
        # For each StructuralPattern in the package, verify:
        #   1. ≥ 2 participating MetaClasses (single class is a FlatClass, not metastructure)
        #   2. hierarchy Association count = N - 1 (forms a chain)
        #   3. every non-root/non-leaf class is target of one edge and source of another
        #   4. root class is first in the chain (no incoming hierarchy edge)
        #   5. no cycle
        for sp in (package.structural_patterns or []):
            p_ids = list(sp.participating_class_ids or [])
            n = len(p_ids)
            h_ids = list(sp.hierarchy_association_ids or [])

            if n < 2:
                errors.append({
                    "element_id": sp.id,
                    "element_type": "structural_pattern",
                    "message": f"元结构 '{sp.label or sp.name}' 只有 {n} 个参与类, 至少需要 2 个 (方法论要求)",
                    "severity": "error",
                })
                continue

            if len(h_ids) != n - 1:
                errors.append({
                    "element_id": sp.id,
                    "element_type": "structural_pattern",
                    "message": f"元结构 '{sp.label or sp.name}' 的层级关联数为 {len(h_ids)}, 应为 {n - 1} (N-1 链式)",
                    "severity": "error",
                })

            # Map assoc id → (src_id, tgt_id) from package associations
            assoc_by_id = {a.id: a for a in package.associations}
            incoming: dict[str, int] = {cid: 0 for cid in p_ids}
            outgoing: dict[str, int] = {cid: 0 for cid in p_ids}
            seen_edges: set[tuple[str, str]] = set()
            for haid in h_ids:
                a = assoc_by_id.get(haid)
                if not a:
                    continue
                src = a.source.class_ref
                tgt = a.target.class_ref
                if src not in p_ids or tgt not in p_ids:
                    errors.append({
                        "element_id": haid,
                        "element_type": "hierarchy_association",
                        "message": f"层级关联 {a.name} 的端点不在元结构 '{sp.label or sp.name}' 内",
                        "severity": "error",
                    })
                    continue
                if (src, tgt) in seen_edges:
                    warnings.append({
                        "element_id": haid,
                        "element_type": "hierarchy_association",
                        "message": f"元结构 '{sp.label or sp.name}' 存在重复层级边 {a.name}",
                        "severity": "warning",
                    })
                seen_edges.add((src, tgt))
                outgoing[src] = outgoing.get(src, 0) + 1
                incoming[tgt] = incoming.get(tgt, 0) + 1

            # Root class (sp.root_class_id) should have no incoming hierarchy edges
            if sp.root_class_id:
                if incoming.get(sp.root_class_id, 0) > 0:
                    errors.append({
                        "element_id": sp.root_class_id,
                        "element_type": "structural_pattern_root",
                        "message": f"元结构 '{sp.label or sp.name}' 的根类有入向层级边 (违反 root_fixed 约束)",
                        "severity": "error",
                    })

            # Cycle detection via DFS on hierarchy edges
            adj: dict[str, list[str]] = {cid: [] for cid in p_ids}
            for haid in h_ids:
                a = assoc_by_id.get(haid)
                if a and a.source.class_ref in adj and a.target.class_ref in adj:
                    adj[a.source.class_ref].append(a.target.class_ref)

            VISITING, VISITED = 1, 2
            state: dict[str, int] = {}

            def _has_cycle(node: str) -> bool:
                if state.get(node) == VISITED:
                    return False
                if state.get(node) == VISITING:
                    return True
                state[node] = VISITING
                for nxt in adj.get(node, []):
                    if _has_cycle(nxt):
                        return True
                state[node] = VISITED
                return False

            for cid in p_ids:
                if state.get(cid) is None:
                    if _has_cycle(cid):
                        errors.append({
                            "element_id": sp.id,
                            "element_type": "structural_pattern",
                            "message": f"元结构 '{sp.label or sp.name}' 的层级链存在环路 (违反 no_cycle 约束)",
                            "severity": "error",
                        })
                        break

            # Intermediate nodes must have both incoming and outgoing (chain continuity)
            for cid in p_ids:
                if cid == sp.root_class_id:
                    continue
                # Leaf nodes are allowed to have no outgoing, but must have incoming
                is_leaf_like = outgoing.get(cid, 0) == 0
                if not is_leaf_like and (incoming.get(cid, 0) == 0 or outgoing.get(cid, 0) == 0):
                    warnings.append({
                        "element_id": cid,
                        "element_type": "structural_pattern_member",
                        "message": f"元结构 '{sp.label or sp.name}' 的参与类在链中既不是叶也不是根, 但缺少上下衔接边",
                        "severity": "warning",
                    })

        return ValidationResult(
            is_valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
        )


validator = ModelValidator()
