"""Document upload and management endpoints."""
from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, HTTPException
from pydantic import BaseModel

from backend.config import settings
from backend.storage.file_store import store
from backend.services.document_parser import parser

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])


# V3.1: document type taxonomy — drives M1 extraction prompt branching
VALID_DOC_TYPES = (
    "auto",      # unknown, classify later
    "spec",      # 📘 制度规范 (国标/行标/管理规定/设计规范) → aggressive class extraction
    "manual",    # 📗 技术说明书 (产品手册/设计规格) → dense attrs + relations
    "ledger",    # 📊 实例表单 (台账/清单/登记簿) → extract column structure only, rows = M0
    "process",   # 💬 业务过程 (会议纪要/邮件) → roles + stages only
)


@router.post("/")
async def upload_documents(files: list[UploadFile] = File(...)):
    """Upload one or more documents."""
    results = []
    for f in files:
        doc_id = str(uuid.uuid4())[:8]
        suffix = "." + f.filename.split(".")[-1] if "." in f.filename else ".txt"
        file_path = settings.documents_dir / f"{doc_id}{suffix}"

        # Save uploaded file
        content = await f.read()
        file_path.write_bytes(content)

        # Parse text
        try:
            text = parser.parse(file_path)
        except Exception as e:
            text = f"[Parse error: {str(e)}]"

        # Save extracted text
        store.save_document_text(doc_id, text)

        # Save metadata; doc_type starts as "auto" until user sets it or LLM classifies.
        meta = {
            "id": doc_id,
            "filename": f.filename,
            "original_path": str(file_path),
            "content_preview": text[:500],
            "char_count": len(text),
            "uploaded_at": datetime.now().isoformat(),
            "status": "ready",
            "doc_type": "auto",          # V3.1
            "doc_type_source": "default",  # "user" | "llm" | "default"
        }
        store.save_document_meta(doc_id, meta)
        results.append(meta)

    return {"documents": results}


class DocTypeUpdate(BaseModel):
    doc_type: str
    source: str = "user"     # "user" or "llm"


@router.patch("/{doc_id}/type")
async def set_document_type(doc_id: str, req: DocTypeUpdate):
    """V3.1: manually tag a document's type, used to branch M1 extraction prompt."""
    if req.doc_type not in VALID_DOC_TYPES:
        raise HTTPException(400, f"doc_type must be one of {VALID_DOC_TYPES}")
    meta = store.get_document_meta(doc_id)
    if meta is None:
        raise HTTPException(404, f"Document {doc_id} not found")
    meta["doc_type"] = req.doc_type
    meta["doc_type_source"] = req.source
    store.save_document_meta(doc_id, meta)
    return {"id": doc_id, "doc_type": req.doc_type, "doc_type_source": req.source}


# ============================================================================
#                V3.4: Excel/CSV structured table extraction
# ============================================================================

@router.get("/{doc_id}/excel-preview")
async def excel_preview(doc_id: str):
    """Return raw cell grid of an uploaded Excel/CSV.

    Frontend uses this to render the table and let user confirm structure.
    Capped at 50 rows × all columns for performance.
    """
    from backend.services.excel_reader import read_structured_file, is_structured_file
    meta = store.get_document_meta(doc_id)
    if meta is None:
        raise HTTPException(404, f"Document {doc_id} not found")
    if not is_structured_file(meta.get("filename", "")):
        raise HTTPException(400, "仅支持 .xlsx/.xls/.csv 文件")
    path = Path(meta["original_path"])
    if not path.exists():
        raise HTTPException(404, "原始文件已丢失, 无法预览")
    try:
        return read_structured_file(path, max_rows=50)
    except Exception as e:
        raise HTTPException(500, f"表格解析失败: {e}")


@router.post("/{doc_id}/excel-analyze")
async def excel_analyze(doc_id: str):
    """AI-analyze an Excel workbook: classify each sheet + analyze structure of data sheets.

    Returns:
      {
        "sheets_classified": [{sheet_name, is_data, confidence, reason}],
        "table_specs": {sheet_name: {...table_spec...}}
      }
    """
    from backend.services.excel_reader import read_structured_file, is_structured_file
    from backend.services.excel_to_m1 import analyze_workbook, analyze_table_structure

    meta = store.get_document_meta(doc_id)
    if meta is None:
        raise HTTPException(404, f"Document {doc_id} not found")
    if not is_structured_file(meta.get("filename", "")):
        raise HTTPException(400, "仅支持 .xlsx/.xls/.csv 文件")
    path = Path(meta["original_path"])
    if not path.exists():
        raise HTTPException(404, "原始文件已丢失")

    try:
        raw = read_structured_file(path, max_rows=50)
    except Exception as e:
        raise HTTPException(500, f"表格解析失败: {e}")

    # Phase 1: classify sheets
    classified = await analyze_workbook(raw)

    # Phase 2: for each data sheet, analyze structure — run in parallel (Semaphore limits concurrency)
    import asyncio
    data_sheets = [c["sheet_name"] for c in classified
                   if c.get("is_data") and c["sheet_name"] in raw["sheets"]]

    sem = asyncio.Semaphore(3)   # Max 3 concurrent LLM calls to avoid rate limits
    async def _analyze_one(sheet_name: str):
        async with sem:
            try:
                return sheet_name, await analyze_table_structure(
                    raw["sheets"][sheet_name], sheet_name
                )
            except Exception as e:
                return sheet_name, {
                    "sheet_name": sheet_name, "ai_error": str(e)[:200],
                    "confidence": "low",
                }

    results = await asyncio.gather(*[_analyze_one(sn) for sn in data_sheets])
    table_specs = {sn: spec for sn, spec in results}

    return {
        "sheets_classified": classified,
        "table_specs": table_specs,
    }


@router.post("/{doc_id}/analyze-stats-table")
async def analyze_stats_table(doc_id: str, body: dict):
    """V3.4 Phase 4: given an already-uploaded Excel that is a STATISTICS/SUMMARY
    table, identify its group-by vs aggregate columns.

    body: {"sheet_name": "Sheet1"}
    """
    from backend.services.excel_reader import read_structured_file, is_structured_file
    from backend.services.excel_to_m1 import analyze_statistics_table

    meta = store.get_document_meta(doc_id)
    if meta is None:
        raise HTTPException(404, f"Document {doc_id} not found")
    if not is_structured_file(meta.get("filename", "")):
        raise HTTPException(400, "仅支持 .xlsx/.xls/.csv 文件")
    path = Path(meta["original_path"])
    if not path.exists():
        raise HTTPException(404, "原始文件已丢失")

    sheet_name = body.get("sheet_name")
    if not sheet_name:
        raise HTTPException(400, "sheet_name required")

    try:
        raw = read_structured_file(path, max_rows=50)
    except Exception as e:
        raise HTTPException(500, f"表格解析失败: {e}")
    if sheet_name not in raw["sheets"]:
        raise HTTPException(404, f"Sheet '{sheet_name}' not found")

    try:
        return await analyze_statistics_table(raw["sheets"][sheet_name], sheet_name)
    except Exception as e:
        raise HTTPException(500, f"统计表分析失败: {e}")


@router.post("/{doc_id}/classify")
async def classify_document(doc_id: str):
    """V3.1: ask LLM to auto-classify the doc into one of VALID_DOC_TYPES.
    Uses first 1200 chars only so the call is fast/cheap (~1 sec)."""
    meta = store.get_document_meta(doc_id)
    if meta is None:
        raise HTTPException(404, f"Document {doc_id} not found")
    text = store.get_document_text(doc_id) or ""
    if not text.strip():
        raise HTTPException(400, "Document has no extracted text")

    from backend.services.ai_extractor import classify_document_type
    try:
        detected = await classify_document_type(text[:1200], filename=meta.get("filename", ""))
    except Exception as e:
        raise HTTPException(500, f"LLM classify failed: {e}")
    if detected not in VALID_DOC_TYPES:
        detected = "auto"
    meta["doc_type"] = detected
    meta["doc_type_source"] = "llm"
    store.save_document_meta(doc_id, meta)
    return {"id": doc_id, "doc_type": detected, "doc_type_source": "llm"}


@router.get("/")
async def list_documents():
    """List all uploaded documents."""
    return {"documents": store.list_documents()}


@router.get("/{doc_id}")
async def get_document(doc_id: str):
    """Get document metadata."""
    meta = store.get_document_meta(doc_id)
    if meta is None:
        raise HTTPException(404, f"Document {doc_id} not found")
    return meta


@router.get("/{doc_id}/text")
async def get_document_text(doc_id: str):
    """Get full extracted text."""
    text = store.get_document_text(doc_id)
    if text is None:
        raise HTTPException(404, f"Document {doc_id} not found")
    return {"id": doc_id, "text": text}


@router.delete("/{doc_id}")
async def delete_document(doc_id: str):
    if not store.delete_document(doc_id):
        raise HTTPException(404, f"Document {doc_id} not found")
    return {"status": "deleted", "id": doc_id}
