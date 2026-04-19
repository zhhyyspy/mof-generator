"""
Visualization exporter — produces draw.io (editable) and SVG (static) diagrams
for the M1 / M2 review package.

Two primary diagram types (P0):
  1. M2 Overview       — one page showing all M2 base classes, grouped by "family"
                         if we can infer clusters (we just use a simple grid by default).
  2. Per-theme M1 Tree — one page per M2 base class, showing its M1 children
                         arranged by level if the M2 has a hierarchy.

The draw.io file is a single multi-page `.drawio` so reviewers can flip between
all pages in one open document. SVG is generated independently per page.

Design notes:
- Layout math is simple grid-based. Reviewers can "Arrange → Layout → Auto"
  in drawio if they want to re-lay out.
- Color system matches the app's live graph (M2 purple, M1 blue, hierarchy tiers).
- Review stamps (✓ / ✗ / 💬) are injected as a right-side mini palette on every page.
"""
from __future__ import annotations

import html
import os
from xml.sax.saxutils import escape as xml_escape


# ==============================================================
# CJK font registration (for both SVG font-family references and PDF canvas)
# ==============================================================
# svglib reads `font-family` from SVG and maps it to a reportlab font name.
# If the requested font isn't registered, it silently falls back to Helvetica,
# which has no Chinese glyphs -> the PDF shows tofu (■■■) instead of Chinese.
#
# Fix: register a CJK-capable TTF at module import time under the name "CJKSans",
# and generate SVGs with `font-family="CJKSans, ..."` so svglib picks it up.
# Browsers & Word do their own font resolution via the fallback chain, so they
# still render Chinese correctly using Microsoft YaHei / system CJK font.

_CJK_FONT_NAME: str | None = None


def _register_cjk_font() -> str | None:
    """Try to register a CJK-capable font with reportlab. Returns the font name or None."""
    global _CJK_FONT_NAME
    if _CJK_FONT_NAME:
        return _CJK_FONT_NAME
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
    except ImportError:
        return None

    # Candidate paths (first match wins)
    candidates = [
        "C:/Windows/Fonts/msyh.ttc",           # Microsoft YaHei (Windows)
        "C:/Windows/Fonts/msyh.ttf",
        "C:/Windows/Fonts/simhei.ttf",         # SimHei (Windows)
        "C:/Windows/Fonts/simsun.ttc",         # SimSun (Windows)
        "/System/Library/Fonts/PingFang.ttc",  # macOS
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",  # Linux (Noto)
        "/usr/share/fonts/truetype/arphic/uming.ttc",              # Linux (AR PL)
    ]
    for path in candidates:
        if not os.path.exists(path):
            continue
        try:
            # subfontIndex=0 picks the first font in a .ttc collection (usually the regular weight)
            pdfmetrics.registerFont(TTFont("CJKSans", path, subfontIndex=0))
            _CJK_FONT_NAME = "CJKSans"
            return _CJK_FONT_NAME
        except Exception:
            continue
    return None


# Register at import time so _wrap_svg can see the result immediately
_register_cjk_font()


# ==============================================================
# Shared helper — force all reportlab String nodes to use CJK font
# ==============================================================
# svglib's font-family mapping doesn't consult reportlab's registered fonts.
# For ANY rasterization/rendering path (PDF via renderPDF, PNG via renderPM),
# Chinese text will show as ■■■ unless we walk the parsed Drawing and override
# fontName on each text node. Used by both PDF generation and PNG rasterization.

def _force_font_recursive(node, font_name):
    if font_name is None:
        return
    try:
        from reportlab.graphics.shapes import String
    except ImportError:
        return
    if isinstance(node, String):
        node.fontName = font_name
    children = getattr(node, "contents", None)
    if children:
        for child in children:
            _force_font_recursive(child, font_name)


# ==============================================================
# Public helper — rasterize an SVG string to a PNG bytes for Word embedding
# ==============================================================

def svg_to_png_bytes(svg_str: str, dpi: int = 144) -> bytes | None:
    """Render an SVG string to PNG bytes with CJK font support.

    Used by the Word report builder to embed diagrams inline (instead of
    merely referencing them). Returns None if required libs are missing or
    the SVG cannot be parsed — caller should fall back gracefully.
    """
    if not svg_str:
        return None
    try:
        import io as _io
        from svglib.svglib import svg2rlg
        from reportlab.graphics import renderPM
    except ImportError:
        return None

    try:
        drawing = svg2rlg(_io.BytesIO(svg_str.encode("utf-8")))
    except Exception:
        return None
    if drawing is None:
        return None

    # Same font-forcing as PDF generation — svglib would otherwise keep
    # Helvetica and produce tofu for Chinese text.
    _force_font_recursive(drawing, _CJK_FONT_NAME)

    try:
        return renderPM.drawToString(drawing, fmt="PNG", dpi=dpi, bg=0xFFFFFF)
    except Exception:
        return None


# --- Visual style constants (kept stable so draw.io renders consistently) ---

COLORS = {
    # M2
    "m2_fill": "#e1d5e7",
    "m2_stroke": "#9673a6",
    # M1 by level (0/1/2/3 index into tier)
    "m1_tier": [
        ("#dae8fc", "#6c8ebf"),   # top level (e.g. 设施)
        ("#d4e1f5", "#8aa8d0"),   # second level (e.g. 功能分组)
        ("#d5e8d4", "#82b366"),   # third level (e.g. 设备)
        ("#ffe6cc", "#d79b00"),   # fourth level (e.g. 部件)
        ("#fff2cc", "#d6b656"),   # extra level 5
        ("#f8cecc", "#b85450"),   # extra level 6
    ],
    # M1 when M2 has no hierarchy (flat)
    "m1_flat_fill": "#dae8fc",
    "m1_flat_stroke": "#6c8ebf",
    # theme-band background (for overview)
    "band_fill": "#fff2cc",
    "band_stroke": "#d6b656",
    # edges
    "inherit_stroke": "#9673a6",
    "contains_stroke": "#d79b00",
    # stamp colors
    "stamp_pass_fill": "#d5e8d4",
    "stamp_pass_stroke": "#82b366",
    "stamp_fail_fill": "#f8cecc",
    "stamp_fail_stroke": "#b85450",
    "stamp_comment_fill": "#fff2cc",
    "stamp_comment_stroke": "#d6b656",
}

# --- Node sizing ---
M2_W, M2_H = 200, 110
M1_W, M1_H = 150, 60
BAND_PAD = 30


# ==============================================================
# Public API
# ==============================================================

def build_review_diagrams(m1_pkg: dict, m2_pkg: dict, m1_class_mappings: list) -> dict:
    """Returns a dict with:
        drawio_xml: str    (multi-page .drawio file content)
        svg_by_name: dict[filename, svg_str]   (one svg per page)
        pdf_bytes: bytes | None   (single multi-page PDF rendered from SVGs)
    """
    pages = []  # list of {name, title, cells_xml, svg}

    # --- Page 1: M2 Overview ---
    overview_page = _build_m2_overview_page(m2_pkg, m1_pkg, m1_class_mappings)
    pages.append(overview_page)

    # --- Page 2+: Per-theme M1 tree ---
    m2_classes = m2_pkg.get("classes", []) or []
    # Sort by child count descending for stable, meaningful page order
    child_count_by_m2 = _count_children_by_m2(m1_class_mappings)
    sorted_m2 = sorted(
        m2_classes,
        key=lambda c: (-child_count_by_m2.get(c.get("name", ""), 0), c.get("name", "")),
    )
    for m2_cls in sorted_m2:
        page = _build_theme_page(m2_cls, m1_pkg, m2_pkg, m1_class_mappings)
        pages.append(page)

    # --- Assemble full drawio file ---
    drawio_xml = _assemble_drawio(pages)

    # --- SVG per page ---
    svg_by_name = {}
    for i, p in enumerate(pages):
        fname = _svg_filename(i, p["name"])
        svg_by_name[fname] = p["svg"]

    # --- Multi-page PDF (rendered from the SVGs so business reviewers who don't
    #     have draw.io can still open the diagrams in any PDF viewer) ---
    pdf_bytes = _build_pdf_from_pages(pages)

    return {
        "drawio_xml": drawio_xml,
        "svg_by_name": svg_by_name,
        "pdf_bytes": pdf_bytes,
    }


def _build_pdf_from_pages(pages: list) -> bytes | None:
    """Render each page's SVG into a multi-page PDF using reportlab + svglib.

    Returns the PDF bytes, or None if the required libraries aren't installed
    (graceful degradation — everything else in the review package still ships).
    """
    try:
        import io as _io
        from svglib.svglib import svg2rlg
        from reportlab.graphics import renderPDF
        from reportlab.pdfgen import canvas
    except ImportError:
        return None

    # CJK font was registered at module import time (see _register_cjk_font above).
    cjk_font_name = _register_cjk_font()

    pdf_buf = _io.BytesIO()
    # Default A4-ish start; each page will set its own size below.
    c = canvas.Canvas(pdf_buf)

    for page in pages:
        svg_str = page.get("svg", "")
        if not svg_str:
            continue
        try:
            drawing = svg2rlg(_io.BytesIO(svg_str.encode("utf-8")))
        except Exception:
            # If any single page fails, skip it; don't abort the whole PDF
            continue
        if drawing is None:
            continue

        # Override every text node's font to our registered CJK font so Chinese
        # glyphs come from the embedded TTF instead of Helvetica's tofu.
        _force_font_recursive(drawing, cjk_font_name)

        # Page layout: title strip at top, then the drawing
        title_strip = 30
        margin = 20
        page_w = max(400, drawing.width + margin * 2)
        page_h = max(400, drawing.height + margin * 2 + title_strip)
        c.setPageSize((page_w, page_h))

        # Page title (page name, e.g. "② 设备台账")
        title_text = str(page.get("name", ""))
        try:
            c.setFont(cjk_font_name or "Helvetica-Bold", 12)
            c.drawString(margin, page_h - margin - 5, title_text)
        except Exception:
            pass

        # Render SVG below the title strip
        try:
            renderPDF.draw(drawing, c, margin, margin)
        except Exception:
            # If rendering fails, leave a blank page with just the title
            pass

        c.showPage()

    try:
        c.save()
    except Exception:
        return None

    return pdf_buf.getvalue()


# ==============================================================
# Page 1: M2 Overview
# ==============================================================

def _build_m2_overview_page(m2_pkg: dict, m1_pkg: dict, mappings: list) -> dict:
    m2_classes = m2_pkg.get("classes", []) or []
    child_count = _count_children_by_m2(mappings)
    has_hierarchy_map = _has_hierarchy_map(m2_pkg)

    # Simple grid: 4 columns wide
    cols = 4
    cell_w, cell_h = 240, 150
    gap_x, gap_y = 30, 30
    start_x, start_y = 60, 100

    # Collect node infos first
    nodes = []
    for idx, c in enumerate(m2_classes):
        row, col = divmod(idx, cols)
        x = start_x + col * (cell_w + gap_x)
        y = start_y + row * (cell_h + gap_y)
        name = c.get("name", "")
        label = c.get("label", "")
        n_children = child_count.get(name, 0)
        has_h = has_hierarchy_map.get(name, False)
        levels_str = ""
        if has_h:
            lvls = _hierarchy_levels(m2_pkg, name)
            levels_str = "层级: " + " → ".join(lvls) if lvls else ""
        nodes.append({
            "id": f"m2_{idx}",
            "name": name,
            "label": label,
            "n_children": n_children,
            "has_h": has_h,
            "levels_str": levels_str,
            "x": x,
            "y": y,
            "w": cell_w,
            "h": cell_h,
        })

    # --- drawio XML cells ---
    cells = []
    cells.append(_title_cell("m2_overview_title", "M2 元模型 · 业务主题总览", 60, 30, 960, 50))

    for n in nodes:
        val = (
            f"<b>{xml_escape(n['label'] or n['name'])}</b>"
            f"<br><span style='font-size:10px;color:#666'>{xml_escape(n['name'])}</span>"
            f"<br><br>📦 {n['n_children']} 个 M1 子类"
            f"<br>{'🌳 ' + xml_escape(n['levels_str']) if n['has_h'] else '📄 扁平主题 (无层级)'}"
        )
        style = (
            f"rounded=1;whiteSpace=wrap;html=1;"
            f"fillColor={COLORS['m2_fill']};strokeColor={COLORS['m2_stroke']};"
            f"fontSize=12;verticalAlign=top;spacingTop=4;"
        )
        cells.append(_vertex_cell(n["id"], val, style, n["x"], n["y"], n["w"], n["h"]))

    # Stamp palette on the right
    cells.extend(_review_stamps_palette(start_x_offset=start_x + cols * (cell_w + gap_x) + 30, start_y=100))

    # --- SVG ---
    # Compute page size
    total_rows = (len(nodes) + cols - 1) // cols
    page_w = start_x + cols * (cell_w + gap_x) + 240   # include stamp palette
    page_h = start_y + total_rows * (cell_h + gap_y) + 60

    svg_nodes = []
    svg_nodes.append(_svg_title("M2 元模型 · 业务主题总览", 60, 60))
    for n in nodes:
        lines = [
            n["label"] or n["name"],
            n["name"],
            f"📦 {n['n_children']} 个 M1 子类",
            (n["levels_str"] or "📄 扁平主题"),
        ]
        svg_nodes.append(_svg_box(n["x"], n["y"], n["w"], n["h"],
                                  COLORS["m2_fill"], COLORS["m2_stroke"], lines,
                                  title_font_size=13))

    svg = _wrap_svg(page_w, page_h, "\n".join(svg_nodes))

    return {
        "name": "① M2 总览",
        "title": "M2 元模型 · 业务主题总览",
        "cells_xml": "\n".join(cells),
        "svg": svg,
    }


# ==============================================================
# Pages 2+: Per-theme M1 tree
# ==============================================================

def _build_theme_page(m2_cls: dict, m1_pkg: dict, m2_pkg: dict, mappings: list) -> dict:
    m2_name = m2_cls.get("name", "")
    m2_label = m2_cls.get("label", m2_name)

    # Members of this M2
    member_map_list = [m for m in mappings if m.get("m2_parent_name") == m2_name]
    member_names = [m.get("m1_class_name") for m in member_map_list if m.get("m1_class_name")]
    m1_by_name = {c.get("name", ""): c for c in (m1_pkg.get("classes", []) or [])}
    members = [m1_by_name[n] for n in member_names if n in m1_by_name]

    # Determine hierarchy
    levels = _hierarchy_levels(m2_pkg, m2_name)
    level_by_m1 = {}
    if levels:
        for m in member_map_list:
            lvl = m.get("level")
            if lvl and lvl != "whole_tree":
                level_by_m1[m.get("m1_class_name")] = lvl

    # Layout
    top_y = 100
    m2_x = 40
    m2_y = top_y
    m2_box_w = M2_W
    m2_box_h = M2_H

    # If hierarchy: lay M1 in rows by level
    # Else: lay M1 in grid below M2
    cells = []
    cells.append(_title_cell(
        "theme_title",
        f"{m2_label} · 主题归属图",
        40, 30, 900, 50,
    ))

    # M2 node
    m2_id = "theme_m2"
    m2_val = (
        f"<b>{xml_escape(m2_label or m2_name)}</b>"
        f"<br><span style='font-size:10px;color:#666'>{xml_escape(m2_name)}</span>"
        f"<br><br>包含 {len(member_names)} 个 M1 子类"
        f"<br>{'层级: ' + ' → '.join(levels) if levels else '扁平主题'}"
    )
    m2_style = (
        f"rounded=1;whiteSpace=wrap;html=1;"
        f"fillColor={COLORS['m2_fill']};strokeColor={COLORS['m2_stroke']};"
        f"fontSize=13;verticalAlign=top;spacingTop=4;fontStyle=1;"
    )
    cells.append(_vertex_cell(m2_id, m2_val, m2_style, m2_x, m2_y, m2_box_w, m2_box_h))

    # SVG collection in parallel
    svg_nodes = [
        _svg_title(f"{m2_label} · 主题归属图", 40, 60),
        _svg_box(m2_x, m2_y, m2_box_w, m2_box_h,
                 COLORS["m2_fill"], COLORS["m2_stroke"],
                 [m2_label or m2_name, m2_name,
                  f"包含 {len(member_names)} 个 M1 子类",
                  f"层级: {' → '.join(levels)}" if levels else "扁平主题"],
                 title_font_size=14),
    ]

    max_width = m2_x + m2_box_w
    max_height = m2_y + m2_box_h

    if levels:
        # --- Hierarchical: one row per level ---
        row_top_y = m2_y + m2_box_h + 80
        level_gap_y = 30
        per_level = {}
        orphans = []  # M1 assigned to no specific level (whole_tree) or unassigned
        for cls in members:
            lvl = level_by_m1.get(cls.get("name"))
            if lvl and lvl in levels:
                per_level.setdefault(lvl, []).append(cls)
            else:
                orphans.append(cls)

        current_y = row_top_y
        m1_idx = 0
        for i, lvl in enumerate(levels):
            group = per_level.get(lvl, [])
            # Level band geometry — compute FINAL size BEFORE emitting the cell
            # (previously band_h was emitted with default 80, then later M1 nodes
            # would overflow the band visually because band_h wasn't updated
            # in the already-written cell; fix: compute rows_used first.)
            band_x = m2_x
            per_row = max(4, min(8, len(group) if group else 4))
            cols_used = min(per_row, max(1, len(group)))
            band_w = max(600, cols_used * (M1_W + 20) + 60)
            n_cols = max(1, (band_w - 60) // (M1_W + 20))
            rows_used = (len(group) + n_cols - 1) // n_cols if group else 0
            # Layout budget: 30px top padding for band title, then rows of M1 nodes
            # each row = (M1_H + 10) px, then 10px bottom padding.
            band_h = max(80, 30 + rows_used * (M1_H + 10) + 10)

            fill, stroke = COLORS["m1_tier"][min(i, len(COLORS["m1_tier"]) - 1)]
            cells.append(_vertex_cell(
                f"band_{i}",
                f"<b>{xml_escape(lvl)}</b>  <span style='font-size:10px;color:#666'>({len(group)} 个 M1)</span>",
                (
                    "rounded=0;whiteSpace=wrap;html=1;"
                    f"fillColor={fill};strokeColor={stroke};"
                    "fontSize=12;verticalAlign=top;spacingTop=4;"
                ),
                band_x, current_y, band_w, band_h,
            ))
            svg_nodes.append(_svg_label_band(
                band_x, current_y, band_w, band_h, lvl, len(group), fill, stroke,
            ))

            # Place M1 nodes inside the band
            for j, cls in enumerate(group):
                row_j, col_j = divmod(j, n_cols)
                x = band_x + 30 + col_j * (M1_W + 20)
                y = current_y + 30 + row_j * (M1_H + 10)
                m1_val = _m1_box_value(cls, lvl)
                m1_id = f"m1_{m1_idx}"
                m1_idx += 1
                cells.append(_vertex_cell(
                    m1_id, m1_val,
                    (
                        "rounded=1;whiteSpace=wrap;html=1;"
                        f"fillColor={fill};strokeColor={stroke};"
                        "fontSize=11;verticalAlign=middle;"
                    ),
                    x, y, M1_W, M1_H,
                ))
                # Edge M1 -> M2 (inheritance)
                cells.append(_edge_cell(
                    f"e_m1_{m1_idx}", m1_id, m2_id,
                    (
                        "edgeStyle=orthogonalEdgeStyle;rounded=0;endArrow=block;"
                        f"strokeColor={COLORS['inherit_stroke']};dashed=1;"
                        "endFill=0;"
                    ),
                ))
                # SVG
                svg_nodes.append(_svg_box(
                    x, y, M1_W, M1_H, fill, stroke,
                    [cls.get("label") or cls.get("name"), cls.get("name")],
                    title_font_size=11,
                ))
                max_width = max(max_width, x + M1_W)
                max_height = max(max_height, y + M1_H)

            current_y += band_h + level_gap_y

        # Orphans (whole_tree) — put them to the right of M2
        if orphans:
            orphan_x = m2_x + m2_box_w + 80
            orphan_y = m2_y
            cells.append(_title_cell(
                "orphan_band_title",
                "全树模板 (whole_tree)",
                orphan_x, orphan_y - 30, 300, 24,
                font_size=11, bold=False,
            ))
            for j, cls in enumerate(orphans):
                row_j, col_j = divmod(j, 3)
                x = orphan_x + col_j * (M1_W + 20)
                y = orphan_y + row_j * (M1_H + 10)
                m1_val = _m1_box_value(cls, "whole_tree")
                m1_id = f"m1_orphan_{j}"
                cells.append(_vertex_cell(
                    m1_id, m1_val,
                    (
                        "rounded=1;whiteSpace=wrap;html=1;"
                        f"fillColor={COLORS['m1_flat_fill']};strokeColor={COLORS['m1_flat_stroke']};"
                        "dashed=1;fontSize=11;"
                    ),
                    x, y, M1_W, M1_H,
                ))
                cells.append(_edge_cell(
                    f"e_orphan_{j}", m1_id, m2_id,
                    (
                        "edgeStyle=orthogonalEdgeStyle;rounded=0;endArrow=block;"
                        f"strokeColor={COLORS['inherit_stroke']};dashed=1;endFill=0;"
                    ),
                ))
                svg_nodes.append(_svg_box(
                    x, y, M1_W, M1_H,
                    COLORS["m1_flat_fill"], COLORS["m1_flat_stroke"],
                    [cls.get("label") or cls.get("name"), cls.get("name"), "[whole_tree]"],
                    title_font_size=11, dashed=True,
                ))
                max_width = max(max_width, x + M1_W)
                max_height = max(max_height, y + M1_H)
    else:
        # --- Flat: grid below M2 ---
        band_x = m2_x
        band_y = m2_y + m2_box_h + 60
        cols_count = 5
        for j, cls in enumerate(members):
            row_j, col_j = divmod(j, cols_count)
            x = band_x + col_j * (M1_W + 20)
            y = band_y + row_j * (M1_H + 20)
            m1_val = _m1_box_value(cls, None)
            m1_id = f"m1_flat_{j}"
            cells.append(_vertex_cell(
                m1_id, m1_val,
                (
                    "rounded=1;whiteSpace=wrap;html=1;"
                    f"fillColor={COLORS['m1_flat_fill']};strokeColor={COLORS['m1_flat_stroke']};"
                    "fontSize=11;"
                ),
                x, y, M1_W, M1_H,
            ))
            cells.append(_edge_cell(
                f"e_flat_{j}", m1_id, m2_id,
                (
                    "edgeStyle=orthogonalEdgeStyle;rounded=0;endArrow=block;"
                    f"strokeColor={COLORS['inherit_stroke']};dashed=1;endFill=0;"
                ),
            ))
            svg_nodes.append(_svg_box(
                x, y, M1_W, M1_H,
                COLORS["m1_flat_fill"], COLORS["m1_flat_stroke"],
                [cls.get("label") or cls.get("name"), cls.get("name")],
                title_font_size=11,
            ))
            max_width = max(max_width, x + M1_W)
            max_height = max(max_height, y + M1_H)

    # Stamp palette
    stamp_x = max_width + 60
    cells.extend(_review_stamps_palette(start_x_offset=stamp_x, start_y=m2_y))

    # SVG
    page_w = max(max_width + 240, 1000)
    page_h = max_height + 60
    svg = _wrap_svg(page_w, page_h, "\n".join(svg_nodes))

    # Page name: prefix with ordinal handled by caller; here just base name
    page_name = f"② {m2_label or m2_name}"

    return {
        "name": page_name,
        "title": f"{m2_label} · 主题归属图",
        "cells_xml": "\n".join(cells),
        "svg": svg,
    }


def _m1_box_value(cls: dict, level_label: str | None) -> str:
    cname = cls.get("name", "")
    clabel = cls.get("label", "")
    attrs_count = len([a for a in (cls.get("attributes") or []) if not a.get("is_inherited")])
    lvl_tag = ""
    if level_label and level_label != "whole_tree":
        lvl_tag = f"<br><span style='font-size:10px;color:#666'>[{xml_escape(level_label)}]</span>"
    elif level_label == "whole_tree":
        lvl_tag = "<br><span style='font-size:10px;color:#999'>[全树模板]</span>"
    return (
        f"<b>{xml_escape(clabel or cname)}</b>"
        f"<br><span style='font-size:10px;color:#666'>{xml_escape(cname)}</span>"
        f"{lvl_tag}"
        f"<br><span style='font-size:10px;color:#333'>{attrs_count} 自有属性</span>"
    )


# ==============================================================
# Draw.io XML helpers
# ==============================================================

def _assemble_drawio(pages: list) -> str:
    """Pack multiple pages into one .drawio file."""
    diagrams = []
    for i, p in enumerate(pages):
        # Page ids must be unique; page names used in the tab bar — both must be
        # XML-attribute-safe (no unescaped < > & " ').
        pid = f"page_{i}"
        pname = _xml_attr(p["name"])
        model = (
            '<mxGraphModel dx="1600" dy="1000" grid="1" gridSize="10" guides="1" '
            'tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" '
            'pageWidth="1400" pageHeight="1000" math="0" shadow="0">'
            '<root>'
            '<mxCell id="0"/>'
            '<mxCell id="1" parent="0"/>'
            f'{p["cells_xml"]}'
            '</root>'
            '</mxGraphModel>'
        )
        diagrams.append(f'<diagram id="{pid}" name="{pname}">{model}</diagram>')

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<mxfile host="app.diagrams.net" agent="MOFGenerator-ReviewExporter" '
        'version="20.0.0">'
        + "".join(diagrams)
        + '</mxfile>'
    )


def _xml_attr(s: str) -> str:
    """Escape a string for use inside an XML attribute value.

    Draw.io stores HTML markup inside the `value` attribute of <mxCell>. That HTML
    contains `<b>`, `<br>`, `<span>` etc., and those angle brackets MUST be XML-
    escaped (e.g. &lt;b&gt;) when placed in an XML attribute — otherwise the XML
    parser complains "Unescaped '<' not allowed in attributes values".

    We also escape " ' and & so the attribute value is safe regardless of content.
    """
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _vertex_cell(cid, value, style, x, y, w, h, parent="1") -> str:
    v = _xml_attr(value)
    s = _xml_attr(style)
    p = _xml_attr(parent)
    return (
        f'<mxCell id="{_xml_attr(cid)}" value="{v}" style="{s}" vertex="1" parent="{p}">'
        f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/>'
        f'</mxCell>'
    )


def _edge_cell(cid, src, tgt, style, value="", parent="1") -> str:
    v = _xml_attr(value)
    s = _xml_attr(style)
    return (
        f'<mxCell id="{_xml_attr(cid)}" value="{v}" style="{s}" edge="1" '
        f'source="{_xml_attr(src)}" target="{_xml_attr(tgt)}" parent="{_xml_attr(parent)}">'
        f'<mxGeometry relative="1" as="geometry"/>'
        f'</mxCell>'
    )


def _title_cell(cid, text, x, y, w, h, font_size=18, bold=True) -> str:
    # `text` is raw plain text; _vertex_cell will XML-escape it for the attribute.
    # We don't pre-escape — double-escaping would show literal "&lt;" in the diagram.
    bold_style = "fontStyle=1;" if bold else ""
    return _vertex_cell(
        cid, text,
        f"text;html=1;strokeColor=none;fillColor=none;align=left;verticalAlign=middle;"
        f"fontSize={font_size};{bold_style}",
        x, y, w, h,
    )


def _review_stamps_palette(start_x_offset, start_y) -> list:
    """Three reusable stamp cells that reviewers can clone."""
    stamps = []
    stamps.append(_title_cell(
        "stamp_title",
        "🖋 审查图章 (复制使用)",
        start_x_offset, start_y - 30, 200, 24,
        font_size=11, bold=True,
    ))

    # Pass stamp
    stamps.append(_vertex_cell(
        "stamp_pass",
        "✓ 通过",
        (
            "rounded=1;whiteSpace=wrap;html=1;"
            f"fillColor={COLORS['stamp_pass_fill']};strokeColor={COLORS['stamp_pass_stroke']};"
            "fontSize=14;fontStyle=1;rotation=-15;"
        ),
        start_x_offset, start_y, 100, 40,
    ))
    # Fail stamp
    stamps.append(_vertex_cell(
        "stamp_fail",
        "✗ 拒绝",
        (
            "rounded=1;whiteSpace=wrap;html=1;"
            f"fillColor={COLORS['stamp_fail_fill']};strokeColor={COLORS['stamp_fail_stroke']};"
            "fontSize=14;fontStyle=1;rotation=-15;"
        ),
        start_x_offset, start_y + 60, 100, 40,
    ))
    # Comment bubble
    stamps.append(_vertex_cell(
        "stamp_comment",
        "💬 待议",
        (
            "rounded=1;whiteSpace=wrap;html=1;"
            f"fillColor={COLORS['stamp_comment_fill']};strokeColor={COLORS['stamp_comment_stroke']};"
            "fontSize=13;"
        ),
        start_x_offset, start_y + 120, 100, 40,
    ))
    stamps.append(_title_cell(
        "stamp_hint",
        "复制图章拖到需要标注的元素旁边",
        start_x_offset, start_y + 170, 200, 40,
        font_size=9, bold=False,
    ))
    return stamps


# ==============================================================
# SVG helpers (plain SVG for Word/browser embedding)
# ==============================================================

def _font_stack() -> str:
    """Font-family value putting the reportlab-registered CJKSans first,
    then Microsoft YaHei / Segoe UI / sans-serif as browser/Word fallbacks.
    svglib takes only the first name, so CJKSans must be first if registered."""
    if _CJK_FONT_NAME:
        return f'{_CJK_FONT_NAME},"Microsoft YaHei","Segoe UI",sans-serif'
    return '"Microsoft YaHei","Segoe UI",sans-serif'


def _wrap_svg(width, height, inner) -> str:
    # Note: svglib has limited support for <style> blocks, so we additionally
    # emit font-family="..." as a direct attribute on every <text> element.
    font_stack = _font_stack()
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
        '<style>'
        f'text{{font-family:{font_stack}}}'
        '.title{font-weight:700;font-size:13px;fill:#222}'
        '.subtitle{font-size:10px;fill:#666}'
        '.meta{font-size:10px;fill:#333}'
        '</style>'
        f'{inner}'
        '</svg>'
    )


def _svg_title(text, x, y) -> str:
    fs = _font_stack()
    return (
        f'<text x="{x}" y="{y}" font-size="18" font-weight="700" fill="#222" '
        f'font-family=\'{fs}\'>{xml_escape(text)}</text>'
    )


def _svg_box(x, y, w, h, fill, stroke, text_lines, title_font_size=12, dashed=False) -> str:
    stroke_dash = 'stroke-dasharray="4 3"' if dashed else ""
    rect = (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" ry="6" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="1.5" {stroke_dash}/>'
    )
    # Text lines — title, then subtitle, then meta
    line_x = x + 10
    font_stack = _font_stack()
    texts = []
    current_y = y + title_font_size + 6
    for i, line in enumerate(text_lines):
        if i == 0:
            fs = title_font_size
            fill_color = "#222"
            font_weight = "700"
        elif i == 1:
            fs = 10
            fill_color = "#666"
            font_weight = "400"
        else:
            fs = 10
            fill_color = "#333"
            font_weight = "400"
        texts.append(
            f'<text x="{line_x}" y="{current_y}" font-size="{fs}" '
            f'font-weight="{font_weight}" fill="{fill_color}" '
            f'font-family=\'{font_stack}\'>{xml_escape(line or "")}</text>'
        )
        current_y += fs + 4
    return rect + "".join(texts)


def _svg_label_band(x, y, w, h, label, count, fill, stroke) -> str:
    font_stack = _font_stack()
    rect = (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="1" opacity="0.5"/>'
    )
    # svglib's <tspan> support is patchy; emit as two side-by-side <text> elements
    # so both parts render correctly in the PDF.
    label_text = (
        f'<text x="{x + 10}" y="{y + 18}" font-size="12" font-weight="700" fill="#333" '
        f'font-family=\'{font_stack}\'>{xml_escape(label)}</text>'
    )
    count_text = (
        f'<text x="{x + 10 + max(40, len(label) * 14)}" y="{y + 18}" '
        f'font-size="10" font-weight="400" fill="#666" '
        f'font-family=\'{font_stack}\'>({count} 个)</text>'
    )
    return rect + label_text + count_text


# ==============================================================
# Helpers for reading model data
# ==============================================================

def _count_children_by_m2(mappings: list) -> dict:
    out = {}
    for m in (mappings or []):
        p = m.get("m2_parent_name")
        if p:
            out[p] = out.get(p, 0) + 1
    return out


def _has_hierarchy_map(m2_pkg: dict) -> dict:
    """Returns {m2_name: bool} by checking if the M2 class has a 'level' enum attr."""
    out = {}
    enum_ids = {e.get("id") for e in (m2_pkg.get("enumerations") or [])}
    for c in (m2_pkg.get("classes") or []):
        has = False
        for a in (c.get("attributes") or []):
            if a.get("name") == "level" and a.get("enum_ref") in enum_ids:
                has = True
                break
        out[c.get("name", "")] = has
    return out


def _hierarchy_levels(m2_pkg: dict, m2_name: str) -> list:
    """Returns the level literals (ordered) for a given M2 class name, or []."""
    # Find the class's level enum_ref
    enum_id = None
    for c in (m2_pkg.get("classes") or []):
        if c.get("name") != m2_name:
            continue
        for a in (c.get("attributes") or []):
            if a.get("name") == "level":
                enum_id = a.get("enum_ref")
                break
        break
    if not enum_id:
        return []
    for e in (m2_pkg.get("enumerations") or []):
        if e.get("id") == enum_id:
            return [l.get("label") or l.get("name") for l in (e.get("literals") or [])]
    return []


def _svg_filename(idx: int, page_name: str) -> str:
    # Sanitize page_name to a filesystem-safe filename
    safe = "".join(c for c in page_name if c.isalnum() or c in "_-·①②③④⑤⑥⑦⑧⑨⑩")[:40]
    return f"{idx:02d}_{safe or 'page'}.svg"
