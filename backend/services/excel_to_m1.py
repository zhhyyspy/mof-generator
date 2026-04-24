"""
V3.4: AI-powered Excel table → M1 class extraction.

Pipeline:
  Phase 1  (workbook-level)  AI classifies each sheet: data-table vs metadata/toc
  Phase 2  (table-level)     AI detects: header rows, data range, summary rows,
                              ignored cols, column logical types
  Phase 3  (user confirms)    UI shows AI output, user adjusts
  Phase 4  (generate)         Post-confirmed spec → M1 classes + attributes

This module exposes:
  · analyze_workbook(raw) → [{sheet, is_data, confidence, reason}]
  · analyze_table_structure(raw_sheet, sheet_name) → table_spec
  · build_m1_classes_from_specs(specs, user_overrides) → [MOFClass]
  · optionally store first N rows as sample_instances

All LLM calls route through the existing active client (llm_client).
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Any, Optional

from backend.models.m3_schema import MOFClass, Attribute, Multiplicity
from backend.services.llm_client import get_active_client


_EXCEL_SYSTEM_PROMPT = (
    "你是一个业务表格解读专家。严格按照用户要求的 JSON 格式输出, "
    "不要输出任何解释文字、markdown 代码围栏或注释。"
)


# ============================================================================
#                    Phase 1: Sheet-level classification
# ============================================================================

async def analyze_workbook(raw: dict) -> list[dict]:
    """Ask LLM to classify each sheet in the workbook.

    Input: result of excel_reader.read_structured_file
    Output: [{sheet_name, is_data, confidence, reason}]

    A "data" sheet has tabular rows that represent business objects.
    Non-data sheets: table of contents, summary dashboards, schemas/notes,
    cover pages, empty placeholder sheets.
    """
    sheets = raw.get("sheets", {})
    if not sheets:
        return []

    # Build a compact digest of each sheet (first ~15 rows × first ~8 cols)
    previews = []
    for sheet_name, sd in sheets.items():
        cells = sd.get("cells", [])[:15]
        max_col = min(sd.get("max_col", 0), 8)
        sample_rows = []
        for r_idx, row in enumerate(cells):
            row_text = " | ".join(
                str(c.get("v", "")) if (c and c.get("v") is not None) else "(空)"
                for c in row[:max_col]
            )
            sample_rows.append(f"行{r_idx+1}: {row_text}")
        previews.append({
            "sheet": sheet_name,
            "preview": "\n".join(sample_rows),
            "total_rows": sd.get("max_row", 0),
            "total_cols": sd.get("max_col", 0),
        })

    if len(previews) == 1:
        # Single sheet — no need for classification, assume it's data
        return [{
            "sheet_name": previews[0]["sheet"],
            "is_data": True, "confidence": "high",
            "reason": "仅一个 sheet, 默认视为数据表",
        }]

    prompt = _workbook_classify_prompt(previews)
    client = get_active_client()
    try:
        resp = await client.chat(
            system=_EXCEL_SYSTEM_PROMPT,
            user=prompt, max_tokens=1500, temperature=0,
        )
        data = _extract_json(resp)
        results = data.get("sheets", [])
        # Normalize
        out = []
        for r in results:
            out.append({
                "sheet_name": r.get("sheet_name") or r.get("sheet"),
                "is_data": bool(r.get("is_data")),
                "confidence": r.get("confidence") or "medium",
                "reason": r.get("reason") or "",
            })
        return out
    except Exception as e:
        # Fall back: assume all sheets are data
        return [{
            "sheet_name": name, "is_data": True, "confidence": "low",
            "reason": f"AI 分类失败, 默认全部视为数据表: {str(e)[:100]}",
        } for name in sheets.keys()]


def _workbook_classify_prompt(previews: list[dict]) -> str:
    sheets_txt = "\n\n".join(
        f"=== Sheet {i+1}: {p['sheet']} ({p['total_rows']} 行 × {p['total_cols']} 列) ===\n{p['preview']}"
        for i, p in enumerate(previews)
    )
    return f"""分析下面这个 Excel 工作簿中每个 sheet 的用途。判断哪些是真正的"数据表"(需要抽取为 M1 类),哪些是"元信息"(目录/说明/仪表盘/空白, 应跳过)。

判定依据:
- 数据表 (is_data=true): 有规律的表格, 每行代表一个业务对象 (设备/人员/合同/项目/...)
- 元信息 (is_data=false): 目录 (Table of Contents)、使用说明、封面、数据字典、汇总仪表盘 (只有几行汇总数字)、空 sheet

工作簿内容摘录:
{sheets_txt}

返回 JSON (不要任何其他文字):
{{
  "sheets": [
    {{
      "sheet_name": "Sheet1",
      "is_data": true,
      "confidence": "high|medium|low",
      "reason": "表头明确, 有 100+ 行设备记录"
    }},
    ...
  ]
}}"""


# ============================================================================
#                  Phase 2: Single-sheet structure detection
# ============================================================================

async def analyze_table_structure(raw_sheet: dict, sheet_name: str) -> dict:
    """Given one sheet's raw cell grid, ask LLM to identify:
      - title_row (decorative rows to skip, if any)
      - header_rows (can be multiple for hierarchical headers)
      - data_start / data_end (1-indexed)
      - summary_rows (totals/averages to skip)
      - ignored_cols (decorative columns like 序号)
      - columns: [{col, chinese_label, english_name, logical_type, unit?, is_identifier}]
      - row_semantic (what each row represents — e.g. "一台发电机设备")
      - english_class_name (PascalCase class name)
      - confidence

    Returns a dict with all above + `raw_sheet_name` for reference.
    """
    cells = raw_sheet.get("cells", [])
    merged = raw_sheet.get("merged_ranges", [])
    max_col = raw_sheet.get("max_col", 0)

    # Serialize the grid compactly for the prompt
    grid_lines = []
    for r_idx, row in enumerate(cells):
        parts: list[str] = []
        for c_idx in range(max_col):
            cell = row[c_idx] if c_idx < len(row) else None
            if cell is None:
                parts.append("(空)")
            elif cell.get("m"):
                parts.append("(合并)")
            else:
                v = cell.get("v")
                bold = "★" if cell.get("b") else ""
                parts.append(f"{bold}{v}")
        grid_lines.append(f"行{r_idx+1}: [" + " | ".join(parts) + "]")

    merge_lines = []
    for m in merged[:20]:
        merge_lines.append(
            f"  · 行{m['min_row']}-{m['max_row']} 列{m['min_col']}-{m['max_col']}: "
            f"值='{m.get('value') or ''}' "
        )

    prompt = _structure_prompt(sheet_name, grid_lines, merge_lines)
    client = get_active_client()
    try:
        resp = await client.chat(
            system=_EXCEL_SYSTEM_PROMPT,
            user=prompt, max_tokens=4000, temperature=0,
        )
        data = _extract_json(resp)
        data["sheet_name"] = sheet_name
        return data
    except Exception as e:
        # Fallback: naive assumption (first row = header, rest = data)
        return {
            "sheet_name": sheet_name,
            "title_row": None,
            "header_rows": [1],
            "data_start": 2,
            "data_end": raw_sheet.get("max_row", 1),
            "summary_rows": [],
            "ignored_cols": [],
            "row_semantic": "一行数据",
            "english_class_name": _pascal_from_sheet_name(sheet_name),
            "columns": [],
            "confidence": "low",
            "ai_error": str(e)[:200],
        }


def _structure_prompt(sheet_name: str, grid_lines: list[str], merge_lines: list[str]) -> str:
    grid_txt = "\n".join(grid_lines)
    merge_txt = "\n".join(merge_lines) if merge_lines else "(无合并单元格)"
    return f"""你是一个业务表格解读专家。分析下面 Excel sheet 的结构, 识别真正的数据表边界。

=== 已知情况 ===
- 第 1-2 行常常是"标题/表名/空白", 不是表头
- 表头可能在第 3 行或第 4 行, 有时是 2-3 行合并的多级表头
- 最后 1-3 行常常是"合计/平均/小计"等汇总行, 不是真实数据
- 第一列常常是"序号", 是装饰列应跳过
- ★ 标记的值表示该单元格是加粗的 (常见于表头)

=== Sheet 名: {sheet_name} ===

合并单元格:
{merge_txt}

单元格网格 (行号从 1 开始; "(空)"=空单元格; "(合并)"=被上方合并覆盖):
{grid_txt}

任务: 输出一个 JSON 描述这张表的结构。

每一列, 请推断:
- chinese_label: 中文属性名 (基于表头内容)
- english_name: camelCase 英文名 (从中文语义推, 如"额定功率" → ratedPower)
- logical_type: 业务数据类型, 必须是以下之一:
  · "text"     文本字段 (名称, 描述, 地址, 编号...)
  · "number"   数字 (数量, 台数...)
  · "quantity" 带单位的物理量或金额 (要填 unit)
  · "date"     日期
  · "boolean"  是/否, 布尔值
  · "enum"     固定几个选项的枚举 (需要根据实际值推断)
- unit: 如 logical_type=quantity, 必须给出单位 (MW, kV, m³, 万元, °C 等)
- is_identifier: true 如果这列看起来像主键 (如"设备编号""工号")

row_semantic: 用一句中文说"每行代表什么" (将成为 M1 类的中文标签)
english_class_name: PascalCase 英文类名 (如 "Generator", "Contract", "Employee")

严格按下面 JSON 格式返回, 不要任何解释文字:
{{
  "title_row": 1,
  "header_rows": [3, 4],
  "data_start": 5,
  "data_end": 124,
  "summary_rows": [125, 126],
  "ignored_cols": [1],
  "row_semantic": "一台发电机设备",
  "english_class_name": "Generator",
  "columns": [
    {{
      "col": 2, "chinese_label": "设备名称", "english_name": "equipmentName",
      "logical_type": "text", "is_identifier": true
    }},
    {{
      "col": 5, "chinese_label": "额定功率", "english_name": "ratedPower",
      "logical_type": "quantity", "unit": "MW"
    }}
  ],
  "confidence": "high"
}}"""


# ============================================================================
#               Phase 3+4: Apply user-confirmed spec → M1 classes
# ============================================================================


LOGICAL_TO_DATA_TYPE = {
    "text": "String",
    "number": "Integer",
    "quantity": "Float",
    "date": "Date",
    "boolean": "Boolean",
    "enum": "Enum",
}


def build_m1_class_from_spec(
    spec: dict,
    description: Optional[str] = None,
) -> tuple[MOFClass, list[dict]]:
    """Construct a MOFClass from a confirmed spec.

    spec = {
      "english_class_name": "Generator",
      "row_semantic": "一台发电机设备",
      "columns": [{col, chinese_label, english_name, logical_type, unit?, is_identifier}],
      (may include user overrides)
    }

    Returns (class_obj, sample_data_placeholder_list)
    The caller is responsible for adding the class to the package.
    """
    class_name = (spec.get("english_class_name") or "NewClass").strip()
    class_label = (spec.get("row_semantic") or class_name).strip()
    class_id = str(uuid.uuid4())

    attrs: list[Attribute] = []
    used_names: set[str] = set()
    for col_spec in spec.get("columns", []):
        if col_spec.get("skip"):
            continue
        lt = col_spec.get("logical_type") or "text"
        dt = LOGICAL_TO_DATA_TYPE.get(lt, "String")
        name = (col_spec.get("english_name") or "").strip()
        if not name:
            name = f"col{col_spec.get('col', 0)}"
        # Ensure unique name
        base = name
        suffix = 1
        while name in used_names:
            suffix += 1
            name = f"{base}{suffix}"
        used_names.add(name)
        attrs.append(Attribute(
            id=str(uuid.uuid4()),
            name=name,
            label=col_spec.get("chinese_label") or name,
            description=None,
            data_type=dt,
            unit=col_spec.get("unit"),
            multiplicity=Multiplicity(lower=1, upper=1),
            logical_type=lt,
        ))

    cls = MOFClass(
        id=class_id,
        name=class_name,
        label=class_label,
        description=description or f"源自 Excel 表格 (sheet: {spec.get('sheet_name', '')})",
        attributes=attrs,
    )
    return cls, []


# ============================================================================
#                    Phase 4 (Stats table): group-by detection
# ============================================================================

async def analyze_statistics_table(raw_sheet: dict, sheet_name: str) -> dict:
    """For a statistics/summary table, identify GROUP-BY fields and AGGREGATE fields.

    Example input: a pivot-table-style sheet summarizing detail records by
    facility, where each row = one facility, columns = counts/sums.

    Output:
      {
        "group_by_columns": [{col, chinese_label, english_name, ...}],
        "aggregate_columns": [{col, chinese_label, english_name, agg_function: "sum/count/avg", base_field}],
        "suggested_parent_class": "Facility",   # the group-by dimension becomes a parent class
        "suggested_child_count": 4,              # rows count
        "row_semantic": "一个设施的统计汇总"
      }
    """
    # Reuse Phase 2 structure detection first
    base_spec = await analyze_table_structure(raw_sheet, sheet_name)

    cells = raw_sheet.get("cells", [])
    max_col = raw_sheet.get("max_col", 0)
    grid_lines = []
    for r_idx, row in enumerate(cells[:20]):
        parts = []
        for c_idx in range(max_col):
            cell = row[c_idx] if c_idx < len(row) else None
            if cell is None:
                parts.append("(空)")
            elif cell.get("m"):
                parts.append("(合并)")
            else:
                parts.append(str(cell.get("v", "")))
        grid_lines.append(f"行{r_idx+1}: [" + " | ".join(parts) + "]")

    grid_txt = "\n".join(grid_lines)
    prompt = f"""这是一份"统计表"(聚合/汇总表, 非明细表)。请分析哪些列是**分组字段**, 哪些是**聚合结果字段**。

典型特征:
- 分组字段: 通常在左边几列, 值不重复 (如"厂区A/厂区B/厂区C")
- 聚合字段: 通常在右边, 值是数字 (如"设备数量""额定功率总和")
- 最后可能有"总计"行

=== Sheet: {sheet_name} ===
{grid_txt}

返回 JSON:
{{
  "group_by_columns": [
    {{"col": 1, "chinese_label": "厂区", "english_name": "facility"}}
  ],
  "aggregate_columns": [
    {{"col": 2, "chinese_label": "设备数量", "english_name": "equipCount",
      "agg_function": "count", "base_field": "equipment"}},
    {{"col": 3, "chinese_label": "总功率", "english_name": "totalPower",
      "agg_function": "sum", "unit": "MW", "base_field": "ratedPower"}}
  ],
  "suggested_parent_class": "Facility",
  "suggested_parent_label": "厂区",
  "row_semantic": "一个厂区的设备统计汇总"
}}"""
    client = get_active_client()
    try:
        resp = await client.chat(
            system=_EXCEL_SYSTEM_PROMPT,
            user=prompt, max_tokens=2000, temperature=0,
        )
        data = _extract_json(resp)
        # Merge with base structure info
        data["sheet_name"] = sheet_name
        data["base_structure"] = base_spec
        data["suggested_child_count"] = max(
            0, base_spec.get("data_end", 0) - base_spec.get("data_start", 0) + 1
        )
        return data
    except Exception as e:
        return {
            "sheet_name": sheet_name,
            "group_by_columns": [],
            "aggregate_columns": [],
            "ai_error": str(e)[:200],
            "base_structure": base_spec,
        }


# ============================================================================
#                                 Helpers
# ============================================================================


def _extract_json(text: str) -> dict:
    """Locate and parse a JSON object inside an arbitrary string."""
    if not text:
        return {}
    # Strip markdown code fence if present
    text = re.sub(r"^```(?:json)?\s*", "", text.strip())
    text = re.sub(r"\s*```$", "", text.strip())
    # Find first { to last }
    i = text.find("{")
    j = text.rfind("}")
    if i < 0 or j <= i:
        return {}
    return json.loads(text[i:j+1])


def _pascal_from_sheet_name(s: str) -> str:
    """Convert sheet name like 'Sheet1' or '设备台账' to a PascalCase fallback."""
    # Chinese → fallback
    if not re.search(r"[A-Za-z]", s):
        return "TableClass"
    s = re.sub(r"[^\w\s]", " ", s).strip()
    parts = s.split()
    return "".join(p[0].upper() + p[1:] if p else "" for p in parts) or "TableClass"
