"""
Complete Model Package (.mofpkg.zip) — export / preview / import endpoints.

See backend/services/package_io.py for the package format definition.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from backend.services.package_io import (
    PackageExporter, PackageImporter,
    STRATEGY_RENAME, STRATEGY_SKIP, STRATEGY_OVERWRITE, VALID_STRATEGIES,
)


router = APIRouter(prefix="/api/v1/package", tags=["package"])


class ExportOptions(BaseModel):
    m1_id: str = Field(..., description="M1 model ID to export")
    include_m2: bool = True
    include_all_versions: bool = False
    include_documents: bool = False
    include_llm_providers: bool = False
    note: str = ""
    exported_by: str = ""


@router.post("/export")
async def export_package(options: ExportOptions):
    """Produce a .mofpkg.zip and return it as an attachment."""
    exporter = PackageExporter()
    try:
        zip_bytes, filename = exporter.export(
            options.m1_id,
            include_m2=options.include_m2,
            include_all_versions=options.include_all_versions,
            include_documents=options.include_documents,
            include_llm_providers=options.include_llm_providers,
            note=options.note,
            exported_by=options.exported_by,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    # RFC 5987 UTF-8 filename for CJK compatibility
    from urllib.parse import quote
    ascii_fb = "package.mofpkg.zip"
    utf8 = quote(filename, safe="")
    disp = f"attachment; filename=\"{ascii_fb}\"; filename*=UTF-8''{utf8}"
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": disp},
    )


@router.post("/preview")
async def preview_package(file: UploadFile = File(...)):
    """Dry-run: parse manifest + detect local conflicts. No store changes."""
    content = await file.read()
    if not content:
        raise HTTPException(400, "空文件")
    importer = PackageImporter()
    try:
        return importer.preview(content)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"预览失败: {e}")


@router.post("/import")
async def import_package(
    file: UploadFile = File(...),
    options: str = Form("{}"),
):
    """Apply import per options. Returns a summary of imported/skipped/failed entities."""
    try:
        opts = json.loads(options or "{}")
    except Exception:
        raise HTTPException(400, "options 参数 JSON 解析失败")

    strategy = (opts.get("strategy") or STRATEGY_RENAME).strip()
    if strategy not in VALID_STRATEGIES:
        raise HTTPException(400, f"非法 strategy: {strategy}")
    import_documents = bool(opts.get("import_documents", True))
    import_llm = bool(opts.get("import_llm", False))

    content = await file.read()
    if not content:
        raise HTTPException(400, "空文件")

    importer = PackageImporter()
    try:
        return importer.do_import(
            content,
            strategy=strategy,
            import_documents=import_documents,
            import_llm=import_llm,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"导入失败: {e}")
