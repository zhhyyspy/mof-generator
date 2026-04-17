"""AI extraction endpoints: docs→M1, then M1→M2. With per-document progress tracking."""
from __future__ import annotations

import uuid
import asyncio
from datetime import datetime

from fastapi import APIRouter, HTTPException

from backend.models.m3_schema import Package
from backend.models.m1_model import M1Model, M1ModelVersion
from backend.models.api_schemas import ExtractionRequest, RefineRequest
from backend.storage.file_store import store
from backend.services.ai_extractor import AIExtractor
from backend.services.document_parser import parser as doc_parser
from backend.config import settings

router = APIRouter(prefix="/api/v1/extraction", tags=["extraction"])

_tasks: dict[str, dict] = {}
_cancel_flags: dict[str, bool] = {}


def _update_task(task_id: str, **kwargs):
    if task_id in _tasks:
        _tasks[task_id].update(kwargs)


def _add_log(task_id: str, type: str, text: str):
    """Append a log entry to the task's log array."""
    if task_id in _tasks:
        _tasks[task_id].setdefault("logs", []).append({
            "type": type,
            "text": text,
            "time": (datetime.now() - _tasks[task_id].get("_start", datetime.now())).total_seconds(),
        })


def _update_doc(task_id: str, doc_id: str, status: str):
    """Update a single document's status in the task."""
    if task_id in _tasks:
        docs = _tasks[task_id].get("documents", [])
        for d in docs:
            if d["id"] == doc_id:
                d["status"] = status
                break


def _update_parallel(task_id: str, subtask_id: str, name: str, status: str):
    """Update a parallel subtask's status. Creates if not exists."""
    if task_id not in _tasks:
        return
    ptasks = _tasks[task_id].setdefault("parallel_tasks", [])
    now = datetime.now().isoformat()
    for pt in ptasks:
        if pt["id"] == subtask_id:
            pt["status"] = status
            pt["name"] = name
            if status in ("done", "error"):
                pt["finished_at"] = now
            return
    # Create new
    ptasks.append({
        "id": subtask_id,
        "name": name,
        "status": status,
        "started_at": now if status == "running" else None,
        "finished_at": now if status in ("done", "error") else None,
    })


def _clear_parallel(task_id: str):
    """Clear all parallel subtasks."""
    if task_id in _tasks:
        _tasks[task_id]["parallel_tasks"] = []


def _add_conversation(task_id: str, role: str, content: str, meta: str = "", full: str = ""):
    """Append a conversation entry (LLM request/response) to the task.
    content: short preview for the list view
    full: complete text for expand view
    """
    if task_id not in _tasks:
        return
    convs = _tasks[task_id].setdefault("llm_conversations", [])
    convs.append({
        "role": role,
        "content": content[:200],
        "full": full[:20000] if full else "",  # Full text for expand (up to 20K)
        "meta": meta,
        "time": (datetime.now() - _tasks[task_id].get("_start", datetime.now())).total_seconds(),
    })
    if len(convs) > 40:
        _tasks[task_id]["llm_conversations"] = convs[-40:]


# ---- Pipeline A: Documents → M1 ----

@router.post("/start-m1")
async def start_m1_extraction(req: ExtractionRequest):
    """Extract M1 model directly from uploaded documents."""
    # Validate all docs exist
    doc_metas = []
    for doc_id in req.document_ids:
        meta = store.get_document_meta(doc_id)
        if meta is None:
            raise HTTPException(404, f"Document {doc_id} not found")
        doc_metas.append(meta)

    task_id = str(uuid.uuid4())[:8]
    _tasks[task_id] = {
        "task_id": task_id,
        "status": "running",
        "step": "parsing_documents",
        "progress": 0.0,
        "message": "正在解析文档...",
        "result": None,
        "error": None,
        "parallel_tasks": [],
        "llm_conversations": [],  # Real-time LLM dialogue stream
        "documents": [
            {"id": m["id"], "filename": m["filename"], "status": "pending", "char_count": 0}
            for m in doc_metas
        ],
        "logs": [],
        "_start": datetime.now(),
    }

    async def _safe_run():
        """Wrapper that catches ALL exceptions — server never crashes from extraction."""
        try:
            await _run_m1_extraction(
                task_id, req.document_ids, doc_metas,
                req.model_name, req.model_label,
            )
        except Exception as e:
            # Only update if not already cancelled by the cancel endpoint
            if _tasks.get(task_id, {}).get("status") != "cancelled":
                _update_task(task_id, status="failed", step="error",
                    message=f"提取失败: {str(e)[:200]}", error=str(e)[:200])
                _add_log(task_id, "error", f"错误: {str(e)[:200]}")

    t = asyncio.create_task(_safe_run())
    # Task runs in background; cancel via _cancel_flags (no force-kill)
    return {"task_id": task_id, "status": "running"}


async def _run_m1_extraction(
    task_id: str, doc_ids: list[str], doc_metas: list[dict],
    model_name: str | None, model_label: str | None,
):
    try:
        total_docs = len(doc_ids)
        _add_log(task_id, "info", f"共 {total_docs} 份文档待处理")

        # ---- Phase 1: Load full text of each document ----
        doc_texts = []  # list of (filename, full_text)
        total_chars = 0
        for i, (doc_id, meta) in enumerate(zip(doc_ids, doc_metas)):
            filename = meta["filename"]
            _update_task(task_id,
                step="parsing_documents",
                progress=0.02 + 0.08 * (i / total_docs),
                message=f"正在加载文档 ({i+1}/{total_docs}): {filename}",
            )
            _update_doc(task_id, doc_id, "parsing")
            _add_log(task_id, "step", f"加载文档: {filename}")

            await asyncio.sleep(0.05)

            text = store.get_document_text(doc_id)
            if text:
                chars = len(text)
                total_chars += chars
                doc_texts.append((filename, text))
                _update_doc(task_id, doc_id, "done")
                _add_log(task_id, "success", f"  {filename} — {chars:,} 字符 (全量)")
            else:
                _update_doc(task_id, doc_id, "error")
                _add_log(task_id, "error", f"  {filename} — 无法读取")

        _add_log(task_id, "info", f"全部文档加载完成，共 {total_chars:,} 字符")
        _update_task(task_id, progress=0.10, message="文档加载完成，开始分批处理...")

        await asyncio.sleep(0.1)

        # ---- Phase 2: AI extraction with phase-aware heartbeat ----
        extractor = AIExtractor()
        current_phase = {"step": "discovering_entities", "detail": ""}

        async def on_progress(step, progress, message):
            # Clear parallel tasks when entering a new major phase
            if step != current_phase["step"] and step in ("extracting_attributes", "extracting_associations", "saving", "completed"):
                _clear_parallel(task_id)
            current_phase["step"] = step
            current_phase["detail"] = message
            _update_task(task_id, step=step, progress=progress, message=message)
            _add_log(task_id, "step" if "识别" in message or "提取" in message or "分析" in message else "info", message)

        async def on_conversation(role, content, meta="", full=""):
            _add_conversation(task_id, role, content, meta, full)

        async def on_parallel(subtask_id, name, status):
            _update_parallel(task_id, subtask_id, name, status)
            if status == "running":
                _add_log(task_id, "info", f"  ▶ {name}")
            elif status == "done":
                _add_log(task_id, "success", f"  ✓ {name}")
            elif status == "error":
                _add_log(task_id, "error", f"  ✗ {name}")

        # Phase-aware heartbeat: shows different messages depending on current step
        heartbeat_running = True

        PHASE_HEARTBEATS = {
            "discovering_entities": [
                "AI正在分析文档内容，识别实体类型...",
                "正在理解业务术语和分类标准...",
                "正在匹配设备名称、参数表结构...",
                "AI正在推理文档中的实体关系...",
            ],
            "extracting_attributes": [
                "正在逐类提取属性，匹配数据类型与单位...",
                "AI正在分析字段含义: String/Float/Integer/Date/Enum...",
                "正在识别度量单位: MW, kV, rpm, mm, MPa...",
                "正在比对文档中的参数表与类定义...",
            ],
            "extracting_associations": [
                "正在分析类之间的包含与引用关系...",
                "正在识别 composition / aggregation 模式...",
                "正在构建类间关联图...",
            ],
        }
        FALLBACK_HEARTBEAT = "AI仍在处理中，请耐心等待..."

        async def heartbeat():
            idx = 0
            while heartbeat_running:
                await asyncio.sleep(10)
                if not heartbeat_running:
                    break
                step = current_phase["step"]
                msgs = PHASE_HEARTBEATS.get(step, [FALLBACK_HEARTBEAT])
                msg = msgs[idx % len(msgs)]
                # Append elapsed time hint every 60s
                elapsed = (datetime.now() - _tasks[task_id].get("_start", datetime.now())).total_seconds()
                if elapsed > 120:
                    msg += f" (已运行 {int(elapsed)}s)"
                _add_log(task_id, "info", msg)
                idx += 1

        hb_task = asyncio.create_task(heartbeat())

        try:
            result = await extractor.extract_m1(
                doc_texts,
                progress_callback=on_progress,
                parallel_callback=on_parallel,
                check_cancelled=lambda: is_cancelled(task_id),
                conversation_callback=on_conversation,
            )
        finally:
            heartbeat_running = False
            try:
                hb_task.cancel()
                await hb_task
            except (asyncio.CancelledError, Exception):
                pass  # Heartbeat cleanup should never raise

        # Check if cancelled during extraction
        if is_cancelled(task_id):
            _add_log(task_id, "info", "提取已被用户中止")
            _update_task(task_id,
                status="cancelled", step="cancelled", progress=0,
                message="提取已中止",
            )
            _cancel_flags.pop(task_id, None)
            return

        # ---- Phase 3: Return for review (NOT auto-saved) ----
        _update_task(task_id, step="completed", progress=1.0, message="M1模型提取完成，等待审查确认...")
        _add_log(task_id, "success",
            f"提取完成: {result['classes_found']} 类, {result['attributes_found']} 属性, "
            f"{result['associations_found']} 关联, {result['enumerations_found']} 枚举")
        _add_log(task_id, "info", "请审查提取结果，勾选要导入的实体后确认")

        _update_task(task_id,
            status="completed", step="completed", progress=1.0,
            message="M1模型提取完成！",
            result={
                "package": result["package"],  # Raw Package for frontend review
                "classes_found": result["classes_found"],
                "attributes_found": result["attributes_found"],
                "associations_found": result["associations_found"],
                "enumerations_found": result["enumerations_found"],
                "confidence_notes": result["confidence_notes"],
                "source_document_ids": doc_ids,
                "total_documents": total_docs,
                "total_chars": total_chars,
            },
        )
    except Exception as e:
        err_msg = str(e)
        if "用户中止" in err_msg:
            # Cancelled via flag — cancel endpoint already set status
            _cancel_flags.pop(task_id, None)
        elif _tasks.get(task_id, {}).get("status") != "cancelled":
            _add_log(task_id, "error", f"提取失败: {err_msg[:200]}")
            _update_task(task_id,
                status="failed", step="error", progress=0,
                message=f"提取失败: {err_msg[:200]}", error=err_msg[:200],
            )


# ---- Pipeline B: M1 → M2 ----

@router.post("/derive-m2/{model_id}")
async def start_m2_derivation(model_id: str):
    model = store.get_model(model_id)
    if model is None:
        raise HTTPException(404, f"Model {model_id} not found")
    if not model.versions:
        raise HTTPException(400, "Model has no versions")

    task_id = str(uuid.uuid4())[:8]
    _tasks[task_id] = {
        "task_id": task_id,
        "status": "running",
        "step": "starting",
        "progress": 0.0,
        "message": "正在从M1推导M2元模型...",
        "result": None,
        "error": None,
        "documents": [],
        "logs": [],
        "_start": datetime.now(),
    }

    m1_package = model.versions[-1].package.model_dump()
    async def _safe_run_m2():
        try:
            await _run_m2_derivation(task_id, model_id, m1_package)
        except Exception as e:
            if _tasks.get(task_id, {}).get("status") != "cancelled":
                _update_task(task_id, status="failed", step="error",
                    message=f"M2推导失败: {str(e)[:200]}")

    t = asyncio.create_task(_safe_run_m2())
    # Task runs in background; cancel via _cancel_flags (no force-kill)
    return {"task_id": task_id, "status": "running"}


async def _run_m2_derivation(task_id: str, model_id: str, m1_package: dict):
    try:
        extractor = AIExtractor()

        async def on_progress(step, progress, message):
            _update_task(task_id, step=step, progress=progress, message=message)
            _add_log(task_id, "step", message)

        _add_log(task_id, "info", "加载M1模型数据...")
        result = await extractor.derive_m2(m1_package, progress_callback=on_progress)

        m2_id = f"m2_{model_id}"
        m2_pkg = Package.model_validate(result["m2_package"])
        m2_version = M1ModelVersion(
            version="1.0", created_at=datetime.now(),
            changelog="从M1模型自动推导的M2元模型", package=m2_pkg,
        )
        m2_model = M1Model(
            id=m2_id,
            name=m2_pkg.name or "M2MetaModel",
            label=m2_pkg.label or "M2元模型",
            description="从M1模型推导的M2元模型（通用抽象层）",
            m2_template_id="",
            current_version="1.0", versions=[m2_version], status="draft",
        )
        store.save_model(m2_model)

        # Update M1 parent references
        m1_model = store.get_model(model_id)
        if m1_model:
            m1_model.m2_template_id = m2_id
            mappings = {m["m1_class_name"]: m["m2_parent_name"]
                        for m in result.get("m1_class_mappings", [])}
            pkg = m1_model.versions[-1].package
            m2_class_ids = {c.name: c.id for c in m2_pkg.classes}
            for cls in pkg.classes:
                if cls.name in mappings:
                    parent_name = mappings[cls.name]
                    cls.parent_class_name = parent_name
                    cls.parent_class_ref = m2_class_ids.get(parent_name)
                    m2_cls = next((c for c in m2_pkg.classes if c.name == parent_name), None)
                    if m2_cls:
                        m2_attr_names = {a.name for a in m2_cls.attributes}
                        for attr in cls.attributes:
                            if attr.name in m2_attr_names:
                                attr.is_inherited = True
            store.save_model(m1_model)

        _add_log(task_id, "success", "M2元模型推导完成")
        _update_task(task_id,
            status="completed", step="completed", progress=1.0,
            message="M2元模型推导完成！",
            result={
                "m2_model_id": m2_id,
                "m1_class_mappings": result.get("m1_class_mappings", []),
                "confidence_notes": result.get("confidence_notes", []),
            },
        )
    except Exception as e:
        _add_log(task_id, "error", f"M2推导失败: {str(e)}")
        _update_task(task_id,
            status="failed", step="error", progress=0,
            message=f"M2推导失败: {str(e)}", error=str(e),
        )


# ---- Polling ----

@router.get("/status/{task_id}")
async def get_status(task_id: str):
    task = _tasks.get(task_id)
    if task is None:
        raise HTTPException(404, f"Task {task_id} not found")
    # Return everything except internal _start
    return {k: v for k, v in task.items() if not k.startswith("_")}


@router.post("/cancel/{task_id}")
async def cancel_task(task_id: str):
    """Cancel a running extraction task gracefully via flag (no force-kill).

    The current AI call is allowed to finish naturally, but no new calls will start.
    The frontend sees 'cancelled' immediately because we update the task status here.
    """
    task = _tasks.get(task_id)
    if task is None:
        return {"status": "cancelled", "message": "任务不存在或已结束"}
    if task.get("status") != "running":
        return {"status": task.get("status", "unknown"), "message": "任务已不在运行中"}

    # Set cancel flag — extraction loop checks this between batches
    _cancel_flags[task_id] = True

    # Immediately mark as cancelled so frontend polling sees it right away
    _update_task(task_id, status="cancelled", step="cancelled", message="提取已中止（等待当前调用结束）")
    _add_log(task_id, "info", "用户中止了任务")

    # NOTE: We do NOT call task.cancel() — that crashes HTTP clients mid-request.
    # The background task will check _cancel_flags and exit after current AI call finishes.

    return {"status": "cancelled", "message": "已中止"}


def is_cancelled(task_id: str) -> bool:
    return _cancel_flags.get(task_id, False)


# ---- Refinement ----

@router.post("/refine")
async def refine_extraction(req: RefineRequest):
    model = store.get_model(req.model_id)
    if model is None:
        raise HTTPException(404, f"Model {req.model_id} not found")
    pkg = model.versions[-1].package
    extractor = AIExtractor()
    layer = "M2" if req.model_id.startswith("m2_") else "M1"
    refined = await extractor.refine(pkg.model_dump(), req.user_message, layer)
    new_pkg = Package.model_validate(refined)
    model.versions[-1].package = new_pkg
    store.save_model(model)
    return {"status": "refined", "model_id": model.id}
