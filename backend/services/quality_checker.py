"""
V3.1 Quality sanity checks.

Runs against an M1 Package and returns a list of findings (info/warn/error)
with hints. Used by the frontend to banner-warn users when the AI output
looks suspicious.

All checks are PURE — no side effects, safe to call repeatedly.
"""
from __future__ import annotations

from typing import Any


SEVERITY_INFO = "info"
SEVERITY_WARN = "warn"
SEVERITY_ERROR = "error"


def check_m1_package(pkg: dict, total_doc_chars: int = 0) -> list[dict]:
    """Run all M1 sanity checks. pkg is a package.model_dump() dict.

    Each finding: {severity, metric, value, expected_range, hint, hint_action}
    """
    findings: list[dict] = []
    classes = pkg.get("classes") or []
    associations = pkg.get("associations") or []
    n_classes = len(classes)
    n_assocs = len(associations)

    # --- 1. Extraction density vs doc size ---
    if total_doc_chars > 0 and n_classes > 0:
        chars_per_class = total_doc_chars / n_classes
        if chars_per_class < 500:   # too dense
            findings.append({
                "severity": SEVERITY_WARN,
                "metric": "extraction_density",
                "value": f"{chars_per_class:.0f} 字符/类",
                "expected_range": "≥ 500 字符/类",
                "hint": f"抽取密度过高:{n_classes} 个类源自仅 {total_doc_chars:,} 字符文档",
                "hint_action": "可能把大量 M0 实例误抽为类。建议检查 '⚠ 疑似 M0' 分组或运行同义类合并。",
            })
        elif chars_per_class > 8000:  # too sparse
            findings.append({
                "severity": SEVERITY_INFO,
                "metric": "extraction_density",
                "value": f"{chars_per_class:.0f} 字符/类",
                "expected_range": "500~8000 字符/类",
                "hint": f"抽取密度较低:{total_doc_chars:,} 字符文档仅产出 {n_classes} 个类",
                "hint_action": "文档可能以过程描述为主;考虑调整文档类型标签为 '业务过程'",
            })

    # --- 2. Average attributes per class ---
    if n_classes > 0:
        total_attrs = sum(len(c.get("attributes") or []) for c in classes)
        avg_attrs = total_attrs / n_classes
        # Count classes with 0 or 1 attrs (too light)
        light_classes = [c for c in classes if len(c.get("attributes") or []) <= 1]
        if avg_attrs < 2:
            findings.append({
                "severity": SEVERITY_WARN,
                "metric": "avg_attrs_per_class",
                "value": f"{avg_attrs:.1f}",
                "expected_range": "≥ 2",
                "hint": f"平均每类属性数仅 {avg_attrs:.1f};{len(light_classes)} 个类 ≤1 个属性",
                "hint_action": "这些 '空壳类' 很可能是 M0 实例或抽象不足的标签。检查疑似 M0 分组或审查裁剪。",
            })

    # --- 3. Composition / association density ---
    if n_classes > 0:
        compositions = [a for a in associations
                        if a.get("association_type") in ("composition", "aggregation")]
        comp_density = len(compositions) / n_classes if n_classes else 0
        if comp_density < 0.3 and n_classes >= 5:
            findings.append({
                "severity": SEVERITY_WARN,
                "metric": "composition_density",
                "value": f"{len(compositions)}/{n_classes} = {comp_density:.2f}",
                "expected_range": "≥ 0.3 条/类",
                "hint": f"仅 {len(compositions)} 条组合关系覆盖 {n_classes} 个类,层级树稀疏",
                "hint_action": "Phase 1.5 补边可能已部分缓解;若仍不足,检查源文档是否包含显式包含关系描述。",
            })

    # --- 4. Unparented M1 ratio ---
    if n_classes > 0:
        no_parent = [c for c in classes if not c.get("parent_class_name")]
        no_parent_ratio = len(no_parent) / n_classes
        # Unparented is fine pre-M2 derivation; but if model already has M2 and still >30% unparented, warn
        # Note: here we only check M1-local; assumption is M1 alone has some inheritance.
        # Skip this check unless user has already derived M2 (signalled externally if ever).

    # --- 5. Suspected M0 instance rate (already flagged upstream, but surface it) ---
    #     (handled by the review UI's 'suspected' badge, not re-run here)

    # --- 6. Enum orphan check ---
    enums = pkg.get("enumerations") or []
    used_enum_ids = set()
    for c in classes:
        for a in c.get("attributes") or []:
            if a.get("enum_ref"):
                used_enum_ids.add(a["enum_ref"])
    orphan_enums = [e for e in enums if e.get("id") not in used_enum_ids]
    if len(orphan_enums) >= 3:
        findings.append({
            "severity": SEVERITY_INFO,
            "metric": "orphan_enumerations",
            "value": f"{len(orphan_enums)}/{len(enums)}",
            "expected_range": "< 3",
            "hint": f"{len(orphan_enums)} 个枚举未被任何属性引用",
            "hint_action": "可能是 AI 过度生成;考虑在审查面板中取消勾选未使用的枚举。",
        })

    # --- 7. M2 metastructure depth (if structural_patterns present) ---
    sps = pkg.get("structural_patterns") or []
    for sp in sps:
        depth = len(sp.get("level_names") or [])
        if depth > 6:
            findings.append({
                "severity": SEVERITY_WARN,
                "metric": "metastructure_depth",
                "value": f"{depth} 级",
                "expected_range": "≤ 6 级",
                "hint": f"元结构 '{sp.get('label') or sp.get('name')}' 深度 {depth} 级",
                "hint_action": "层级过深通常是过度分类。考虑合并中间层级。",
            })

    return findings


def summarize(findings: list[dict]) -> dict:
    """Quick counts for UI banner."""
    return {
        "total": len(findings),
        "errors": sum(1 for f in findings if f["severity"] == SEVERITY_ERROR),
        "warnings": sum(1 for f in findings if f["severity"] == SEVERITY_WARN),
        "infos": sum(1 for f in findings if f["severity"] == SEVERITY_INFO),
    }
