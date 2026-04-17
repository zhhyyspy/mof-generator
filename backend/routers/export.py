"""Export endpoints."""
from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from backend.models.api_schemas import ExportRequest
from backend.storage.file_store import store
from backend.services.model_exporter import exporter

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
