"""M2 template and M3 endpoints."""
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException

from backend.storage.file_store import store
from backend.config import settings

router = APIRouter(prefix="/api/v1/m2-templates", tags=["m2-templates"])


@router.get("/m3")
async def get_m3():
    """Get the fixed M3 meta-meta model definition."""
    p = settings.data_dir / "m3_fixed.json"
    if not p.exists():
        raise HTTPException(404, "M3 definition not found")
    return json.loads(p.read_text(encoding="utf-8"))


@router.get("/")
async def list_templates():
    """List available M2 templates."""
    return {"templates": store.list_m2_templates()}


@router.get("/{template_id}")
async def get_template(template_id: str):
    """Get full M2 template definition."""
    t = store.get_m2_template(template_id)
    if t is None:
        raise HTTPException(404, f"Template {template_id} not found")
    return t.model_dump()
