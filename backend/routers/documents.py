"""Document upload and management endpoints."""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, UploadFile, File, HTTPException

from backend.config import settings
from backend.storage.file_store import store
from backend.services.document_parser import parser

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])


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

        # Save metadata
        meta = {
            "id": doc_id,
            "filename": f.filename,
            "original_path": str(file_path),
            "content_preview": text[:500],
            "char_count": len(text),
            "uploaded_at": datetime.now().isoformat(),
            "status": "ready",
        }
        store.save_document_meta(doc_id, meta)
        results.append(meta)

    return {"documents": results}


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
