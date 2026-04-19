"""Export endpoints."""
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse, Response

from backend.models.api_schemas import ExportRequest
from backend.storage.file_store import store
from backend.services.model_exporter import exporter
from backend.services.review_exporter import export_review_package

router = APIRouter(prefix="/api/v1/export", tags=["export"])


@router.get("/formats")
async def list_formats():
    return {
        "formats": [
            {"id": "json", "name": "JSON", "description": "结构化JSON格式，适合系统集成"},
            {"id": "yaml", "name": "YAML", "description": "人类可读的YAML格式"},
            {"id": "mof_text", "name": "MOF Text", "description": "MOF文本表示法，类似伪代码"},
        ]
    }


@router.post("/")
async def export_model(req: ExportRequest):
    model = store.get_model(req.model_id)
    if model is None:
        raise HTTPException(404, f"Model {req.model_id} not found")

    try:
        content, filename = exporter.export(model, req.format, req.version)
    except ValueError as e:
        raise HTTPException(400, str(e))

    content_types = {
        "json": "application/json",
        "yaml": "text/yaml",
        "mof_text": "text/plain",
    }

    return PlainTextResponse(
        content=content,
        media_type=content_types.get(req.format, "text/plain"),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/review-package")
async def export_review_package_endpoint(data: dict):
    """Generate a comprehensive review package (.zip) for business reviewers.

    Request body: {"m1_id": str, "m2_id": str | null}
    Response: application/zip
    """
    m1_id = data.get("m1_id")
    m2_id = data.get("m2_id")

    if not m1_id:
        raise HTTPException(400, "缺少 m1_id")

    m1 = store.get_model(m1_id)
    if m1 is None:
        raise HTTPException(404, f"M1 模型 {m1_id} 不存在")

    m2 = None
    if m2_id:
        m2 = store.get_model(m2_id)
        if m2 is None:
            raise HTTPException(404, f"M2 模型 {m2_id} 不存在")

    try:
        zip_bytes, zip_name = export_review_package(
            m1.model_dump(),
            m2.model_dump() if m2 else None,
        )
    except Exception as e:
        raise HTTPException(500, f"审查包生成失败: {e}") from e

    # RFC 5987 UTF-8 filename. The plain `filename=` fallback must be ASCII
    # (HTTP headers are latin-1 by default, Chinese chars break that).
    # Modern browsers all honor `filename*=UTF-8''...`, so we only emit that.
    encoded_name = quote(zip_name)
    ascii_fallback = f"review_package.zip"
    disposition = (
        f"attachment; filename=\"{ascii_fallback}\"; "
        f"filename*=UTF-8''{encoded_name}"
    )

    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": disposition},
    )
