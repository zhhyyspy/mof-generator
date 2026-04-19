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
_pause_flags: dict[str, bool] = {}  # True = task should wait
_start_flags: dict[str, bool] = {}  # True = task authorized to begin AI calls
# Retry context: task_id → { "doc_texts": [...], "classes": [...], "enum_map": {...}, "failed_batches": [...] }
_retry_context: dict[str, dict] = {}


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


def _record_failed_batch(task_id: str, batch_info: dict):
    """Record a failed batch with enough info to retry it later."""
    if task_id not in _tasks:
        return
    batches = _tasks[task_id].setdefault("failed_batches", [])
    batch_info["id"] = f"fb_{len(batches)}_{int(datetime.now().timestamp())}"
    batch_info["retried"] = False
    batches.append(batch_info)


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
    auto_start = req.auto_start if hasattr(req, 'auto_start') else False
    initial_status = "running" if auto_start else "ready"
    initial_message = "正在解析文档..." if auto_start else "就绪，等待用户点击开始"

    _tasks[task_id] = {
        "task_id": task_id,
        "status": initial_status,
        "step": "ready" if not auto_start else "parsing_documents",
        "progress": 0.0,
        "message": initial_message,
        "result": None,
        "error": None,
        "parallel_tasks": [],
        "llm_conversations": [],
        "failed_batches": [],
        "documents": [
            {"id": m["id"], "filename": m["filename"], "status": "pending", "char_count": 0}
            for m in doc_metas
        ],
        "logs": [],
        "_start": datetime.now(),
    }
    _start_flags[task_id] = auto_start  # If not auto-starting, block until /start is called
    _pause_flags[task_id] = False

    async def _safe_run():
        try:
            # Wait for start signal if not auto-starting
            if not auto_start:
                _add_log(task_id, "info", "等待用户点击 [开始] 按钮...")
                started = await wait_for_start(task_id)
                if not started:
                    _update_task(task_id, status="cancelled", message="任务未启动已被取消")
                    return

            await _run_m1_extraction(
                task_id, req.document_ids, doc_metas,
                req.model_name, req.model_label,
            )
        except Exception as e:
            if _tasks.get(task_id, {}).get("status") != "cancelled":
                _update_task(task_id, status="failed", step="error",
                    message=f"提取失败: {str(e)[:200]}", error=str(e)[:200])
                _add_log(task_id, "error", f"错误: {str(e)[:200]}")

    asyncio.create_task(_safe_run())
    return {"task_id": task_id, "status": initial_status}


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
        current_phase = {"step": "extracting_entities", "detail": ""}

        # Major phases where we reset the parallel-tasks list in the UI
        # (old values kept for backward compat with older tasks mid-flight)
        _PHASE_BOUNDARIES = (
            "extracting_entities",        # new combined phase
            "extracting_attributes",      # deprecated (still accepted)
            "extracting_associations",
            "saving",
            "completed",
        )

        async def on_progress(step, progress, message):
            # Clear parallel tasks when entering a new major phase
            if step != current_phase["step"] and step in _PHASE_BOUNDARIES:
                _clear_parallel(task_id)
            current_phase["step"] = step
            current_phase["detail"] = message
            _update_task(task_id, step=step, progress=progress, message=message)
            _add_log(task_id, "step" if "识别" in message or "提取" in message or "分析" in message else "info", message)

        async def on_conversation(role, content, meta="", full=""):
            _add_conversation(task_id, role, content, meta, full)

        async def on_failed_batch(batch_info):
            _record_failed_batch(task_id, batch_info)

        async def on_partial_result(partial_package):
            """Called after each successful batch — streams results to UI in real-time."""
            if task_id not in _tasks:
                return
            # Update the result's package with latest partial state
            if _tasks[task_id].get("result") is None:
                _tasks[task_id]["result"] = {
                    "package": partial_package,
                    "classes_found": len(partial_package.get("classes", [])),
                    "attributes_found": sum(len(c.get("attributes", [])) for c in partial_package.get("classes", [])),
                    "associations_found": len(partial_package.get("associations", [])),
                    "enumerations_found": len(partial_package.get("enumerations", [])),
                    "confidence_notes": [],
                    "source_document_ids": doc_ids,
                    "total_documents": total_docs,
                    "total_chars": total_chars,
                    "partial": True,
                }
            else:
                r = _tasks[task_id]["result"]
                r["package"] = partial_package
                r["classes_found"] = len(partial_package.get("classes", []))
                r["attributes_found"] = sum(len(c.get("attributes", [])) for c in partial_package.get("classes", []))
                r["associations_found"] = len(partial_package.get("associations", []))
                r["enumerations_found"] = len(partial_package.get("enumerations", []))

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
            # NEW combined phase — AI extracts classes WITH their attributes in one pass
            "extracting_entities": [
                "AI正在单趟识别实体类型、属性和枚举...",
                "正在理解业务术语和字段含义...",
                "正在匹配设备名称、参数表结构、数据类型与单位...",
                "正在构建类定义: 名称 / 标签 / 属性 / 多重性...",
                "AI正在推理文档中的实体关系...",
            ],
            # Backward compat: older runs may still emit these step names
            "discovering_entities": [
                "AI正在分析文档内容，识别实体类型...",
                "正在理解业务术语和分类标准...",
            ],
            "extracting_attributes": [
                "正在逐类提取属性，匹配数据类型与单位...",
                "AI正在分析字段含义: String/Float/Integer/Date/Enum...",
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
                failed_batch_callback=on_failed_batch,
                partial_result_callback=on_partial_result,
                pause_waiter=lambda: wait_if_paused(task_id),
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

        # Save retry context so user can retry failed batches later
        _retry_context[task_id] = {
            "doc_texts": doc_texts,
            "enum_map": {},  # Will be populated below from result
            "current_package": result.get("package"),  # Current state for merging retry results
        }

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
                "package": result["package"],
                "classes_found": result["classes_found"],
                "attributes_found": result["attributes_found"],
                "associations_found": result["associations_found"],
                "enumerations_found": result["enumerations_found"],
                "confidence_notes": result["confidence_notes"],
                "source_document_ids": doc_ids,
                "total_documents": total_docs,
                "total_chars": total_chars,
                "partial": False,  # Full extraction complete
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
async def start_m2_derivation(model_id: str, data: dict = None):
    model = store.get_model(model_id)
    if model is None:
        raise HTTPException(404, f"Model {model_id} not found")
    if not model.versions:
        raise HTTPException(400, "Model has no versions")

    auto_start = (data or {}).get("auto_start", False)
    selected_class_ids = (data or {}).get("class_ids")  # None → use all classes
    task_id = str(uuid.uuid4())[:8]
    initial_status = "running" if auto_start else "ready"
    initial_message = "正在从M1推导M2元模型..." if auto_start else "就绪，等待用户点击开始"

    _tasks[task_id] = {
        "task_id": task_id,
        "status": initial_status,
        "step": "ready" if not auto_start else "starting",
        "progress": 0.0,
        "message": initial_message,
        "result": None,
        "error": None,
        "parallel_tasks": [],
        "llm_conversations": [],
        "failed_batches": [],
        "documents": [],
        "logs": [],
        "_start": datetime.now(),
        "pipeline_type": "m2_derivation",
    }
    _start_flags[task_id] = auto_start
    _pause_flags[task_id] = False

    m1_package_full = model.versions[-1].package.model_dump()

    # ---- Apply scope filter: keep only user-selected classes ----
    if selected_class_ids:
        selected_set = set(selected_class_ids)
        all_classes = m1_package_full.get("classes", [])
        filtered_classes = [c for c in all_classes if c.get("id") in selected_set]

        if len(filtered_classes) < 2:
            raise HTTPException(
                400,
                f"有效的M1类不足 2 个 (选中 {len(selected_class_ids)}, 匹配 {len(filtered_classes)})。M2推导需要至少 2 个类来抽象共性。"
            )

        # Associations: keep only those whose both ends reference a selected class
        kept_assocs = []
        for a in m1_package_full.get("associations", []):
            src_ref = (a.get("source") or {}).get("class_ref")
            tgt_ref = (a.get("target") or {}).get("class_ref")
            if src_ref in selected_set and tgt_ref in selected_set:
                kept_assocs.append(a)

        # Enumerations: keep those referenced by attributes of kept classes
        referenced_enum_refs = set()
        for c in filtered_classes:
            for attr in c.get("attributes", []):
                ref = attr.get("enum_ref")
                if ref:
                    referenced_enum_refs.add(ref)
        kept_enums = [
            e for e in m1_package_full.get("enumerations", [])
            if e.get("id") in referenced_enum_refs or e.get("name") in referenced_enum_refs
        ]

        m1_package = {
            **m1_package_full,
            "classes": filtered_classes,
            "associations": kept_assocs,
            "enumerations": kept_enums,
        }
        _add_log(
            task_id, "info",
            f"范围过滤: 选中 {len(filtered_classes)}/{len(all_classes)} 个类, "
            f"保留 {len(kept_assocs)} 个关联, {len(kept_enums)} 个枚举"
        )
    else:
        m1_package = m1_package_full

    async def _safe_run_m2():
        try:
            if not auto_start:
                _add_log(task_id, "info", "等待用户点击 [开始] 按钮...")
                started = await wait_for_start(task_id)
                if not started:
                    _update_task(task_id, status="cancelled", message="任务未启动已被取消")
                    return
            await _run_m2_derivation_workbench(task_id, model_id, m1_package)
        except Exception as e:
            if _tasks.get(task_id, {}).get("status") != "cancelled":
                _update_task(task_id, status="failed", step="error",
                    message=f"M2推导失败: {str(e)[:200]}")
                _add_log(task_id, "error", f"错误: {str(e)[:200]}")

    asyncio.create_task(_safe_run_m2())
    return {"task_id": task_id, "status": "running"}


async def _run_m2_derivation_workbench(task_id: str, m1_model_id: str, m1_package: dict):
    """Enhanced M2 derivation with workbench UX parity: partial results, failures, retry."""
    extractor = AIExtractor()
    current_phase = {"step": "clustering_m1", "detail": ""}

    # M2 derivation has 4 phases. Clear parallel-task list when entering each
    # phase so the UI shows only the current phase's subtasks.
    _M2_PHASE_BOUNDARIES = (
        "clustering_m1",
        "synthesizing_m2",
        "detecting_hierarchy",    # Phase 2.5
        "consolidating_m2",
        "saving",
        "completed",
        # Back-compat
        "deriving_m2",
    )

    async def on_progress(step, progress, message):
        if step != current_phase["step"] and step in _M2_PHASE_BOUNDARIES:
            _clear_parallel(task_id)
        current_phase["step"] = step
        _update_task(task_id, step=step, progress=progress, message=message)
        _add_log(task_id, "step", message)

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

    async def on_failed_batch(batch_info):
        _record_failed_batch(task_id, batch_info)

    async def on_partial_result(m2_package_dict):
        """Stream M2 package to UI as it's built."""
        if task_id not in _tasks:
            return
        result = _tasks[task_id].get("result")
        if result is None:
            _tasks[task_id]["result"] = {
                "package": m2_package_dict,
                "classes_found": len(m2_package_dict.get("classes", [])),
                "attributes_found": sum(len(c.get("attributes", [])) for c in m2_package_dict.get("classes", [])),
                "associations_found": len(m2_package_dict.get("associations", [])),
                "enumerations_found": len(m2_package_dict.get("enumerations", [])),
                "confidence_notes": [],
                "m1_class_mappings": [],
                "source_m1_id": m1_model_id,
                "partial": True,
                "is_m2": True,
            }
        else:
            r = _tasks[task_id]["result"]
            r["package"] = m2_package_dict
            r["classes_found"] = len(m2_package_dict.get("classes", []))
            r["attributes_found"] = sum(len(c.get("attributes", [])) for c in m2_package_dict.get("classes", []))
            r["associations_found"] = len(m2_package_dict.get("associations", []))
            r["enumerations_found"] = len(m2_package_dict.get("enumerations", []))

    # Phase 0: initial log
    m1_class_count = len(m1_package.get("classes", []))
    _add_log(task_id, "info", f"M1模型含 {m1_class_count} 个类")
    _update_task(task_id, progress=0.05, message="准备分析M1结构...")

    # Phase-aware heartbeat (mirrors 3-phase M2 derivation)
    heartbeat_running = True

    M2_PHASE_HEARTBEATS = {
        "clustering_m1": [
            "Phase 1/4: AI正在按业务观测维度对 M1 类分组...",
            "正在识别跨业务域的类簇 (如设备台账、会务资料、专题报告...)",
            "分组锚点是业务分析需求, 不是命名/属性相似度",
        ],
        "synthesizing_m2": [
            "Phase 2/4: 为每个业务组并行抽象 1 个 M2 基类...",
            "每组共享率 ≥ 50% 的属性才上升到 M2",
            "差异属性保留在 M1 层 (保持 M2 扁平单层)",
            "M2 自关联指向自身 (保持混合子类的业务树完整)",
        ],
        "detecting_hierarchy": [
            "Phase 2.5/4: 为每个 M2 基类探测是否有层级结构...",
            "典型层级: 设施→功能分组→设备→部件 / 工程项目→任务→子任务 / 单位→部门→岗位",
            "AI 会拒绝把专业类型 (机电/水工/闸门) 识别为层级",
            "有层级的 M2 基类会自动生成 level 枚举和 parent/children 自关联",
        ],
        "consolidating_m2": [
            "Phase 3/4: 检查 M2 基类是否有语义重复, 合并同义元类...",
            "合并后仍是扁平结构, 不做多级抽象",
        ],
    }
    FALLBACK_M2_HEARTBEAT = "AI仍在处理中, 请耐心等待..."

    async def heartbeat():
        idx = 0
        while heartbeat_running:
            await asyncio.sleep(10)
            if not heartbeat_running:
                break
            phase = current_phase.get("step", "")
            msgs = M2_PHASE_HEARTBEATS.get(phase, [FALLBACK_M2_HEARTBEAT])
            msg = msgs[idx % len(msgs)]
            elapsed = (datetime.now() - _tasks[task_id].get("_start", datetime.now())).total_seconds()
            if elapsed > 60:
                msg += f" (已运行 {int(elapsed)}s)"
            _add_log(task_id, "info", msg)
            idx += 1

    hb_task = asyncio.create_task(heartbeat())

    # Save retry context
    _retry_context[task_id] = {
        "m1_package": m1_package,
        "m1_model_id": m1_model_id,
        "is_m2": True,
    }

    try:
        result = await extractor.derive_m2(
            m1_package,
            progress_callback=on_progress,
            conversation_callback=on_conversation,
            check_cancelled=lambda: is_cancelled(task_id),
            partial_result_callback=on_partial_result,
            pause_waiter=lambda: wait_if_paused(task_id),
            parallel_callback=on_parallel,
        )
    finally:
        heartbeat_running = False
        try:
            hb_task.cancel()
            await hb_task
        except (asyncio.CancelledError, Exception):
            pass

    if is_cancelled(task_id):
        _add_log(task_id, "info", "M2推导已被用户中止")
        _update_task(task_id, status="cancelled", step="cancelled", message="M2推导已中止")
        _cancel_flags.pop(task_id, None)
        return

    # Completed — populate final result for review
    _add_log(task_id, "success",
        f"M2推导完成: {len(result['m2_package'].get('classes', []))} 个抽象类, "
        f"{len(result.get('m1_class_mappings', []))} 条M1继承映射")
    _add_log(task_id, "info", "请在成果区审查M2结构并确认保存")

    _update_task(task_id,
        status="completed", step="completed", progress=1.0,
        message="M2推导完成！等待审查确认",
        result={
            "package": result["m2_package"],
            "classes_found": len(result["m2_package"].get("classes", [])),
            "attributes_found": sum(len(c.get("attributes", [])) for c in result["m2_package"].get("classes", [])),
            "associations_found": len(result["m2_package"].get("associations", [])),
            "enumerations_found": len(result["m2_package"].get("enumerations", [])),
            "confidence_notes": result.get("confidence_notes", []),
            "m1_class_mappings": result.get("m1_class_mappings", []),
            "source_m1_id": m1_model_id,
            "partial": False,
            "is_m2": True,
        },
    )


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


@router.post("/retry-failed/{task_id}")
async def retry_failed_batches(task_id: str, data: dict = None):
    """Retry all failed batches for a given extraction task.
    Merges retried results into the task's current result.
    """
    task = _tasks.get(task_id)
    if task is None:
        raise HTTPException(404, f"Task {task_id} not found")

    retry_ctx = _retry_context.get(task_id)
    if retry_ctx is None:
        raise HTTPException(400, "重试上下文已过期，请重新提取")

    failed_batches = [fb for fb in task.get("failed_batches", []) if not fb.get("retried")]
    if not failed_batches:
        return {"status": "no_failures", "message": "没有需要重试的失败批次"}

    selected_ids = (data or {}).get("batch_ids")  # None = retry all

    # Filter to selected batches if specified
    to_retry = failed_batches
    if selected_ids:
        to_retry = [fb for fb in failed_batches if fb.get("id") in selected_ids]

    # Set task back to running for visibility
    _update_task(task_id, status="running", step="retrying", progress=0.5,
                 message=f"正在重试 {len(to_retry)} 个失败批次...")
    _add_log(task_id, "info", f"开始重试 {len(to_retry)} 个失败批次")

    async def _do_retry():
        from backend.services.ai_extractor import AIExtractor
        extractor = AIExtractor()
        current_pkg = retry_ctx.get("current_package") or {}
        doc_texts = retry_ctx.get("doc_texts", [])

        succeeded = 0
        failed_again = 0

        for fb in to_retry:
            if is_cancelled(task_id):
                break
            try:
                await _retry_one_batch(extractor, fb, current_pkg, doc_texts, task_id)
                fb["retried"] = True
                fb["retry_success"] = True
                succeeded += 1
                _add_log(task_id, "success", f"  ✓ 重试成功: {fb.get('label', fb['id'])}")
            except Exception as e:
                fb["retried"] = True
                fb["retry_success"] = False
                fb["retry_error"] = str(e)[:200]
                failed_again += 1
                _add_log(task_id, "error", f"  ✗ 重试仍失败: {fb.get('label')}: {str(e)[:100]}")

        # Update task result with merged package
        task["result"]["package"] = current_pkg
        task["result"]["classes_found"] = len(current_pkg.get("classes", []))
        task["result"]["attributes_found"] = sum(
            len(c.get("attributes", [])) for c in current_pkg.get("classes", [])
        )
        task["result"]["associations_found"] = len(current_pkg.get("associations", []))
        task["result"]["enumerations_found"] = len(current_pkg.get("enumerations", []))

        _update_task(task_id, status="completed", step="completed", progress=1.0,
                     message=f"重试完成: {succeeded} 成功, {failed_again} 仍失败")
        _add_log(task_id, "info", f"重试阶段结束: {succeeded}/{len(to_retry)} 批次成功")

    asyncio.create_task(_do_retry())
    return {"status": "retrying", "count": len(to_retry)}


async def _retry_one_batch(extractor, fb, current_pkg, doc_texts, task_id):
    """Retry a single failed batch by type and merge result into current_pkg."""
    from backend.services.ai_extractor import AIExtractor

    batch_type = fb.get("type")

    # Build enum map from current package
    enum_map = {e["name"]: e["id"] for e in current_pkg.get("enumerations", [])}

    # Reconstruct doc batches (same batching as original extraction)
    batches = []
    current_batch = []
    current_size = 0
    for filename, text in doc_texts:
        if current_size + len(text) > extractor.BATCH_MAX_CHARS and current_batch:
            batches.append(current_batch)
            current_batch = []
            current_size = 0
        if len(text) > extractor.BATCH_MAX_CHARS:
            # Chunk large files same way
            chunk_size = extractor.BATCH_MAX_CHARS - 500
            overlap = 500
            i = 0
            chunks = []
            while i < len(text):
                chunks.append(text[i:i + chunk_size])
                i += chunk_size - overlap
            for idx, ch in enumerate(chunks):
                cf = f"{filename} [第{idx+1}/{len(chunks)}部分]"
                if current_size + len(ch) > extractor.BATCH_MAX_CHARS and current_batch:
                    batches.append(current_batch)
                    current_batch = []
                    current_size = 0
                current_batch.append((cf, ch))
                current_size += len(ch)
        else:
            current_batch.append((filename, text))
            current_size += len(text)
    if current_batch:
        batches.append(current_batch)

    if batch_type == "combined_extraction":
        # New combined (entity + attributes) batch retry
        batch_idx = fb.get("batch_idx", 0)
        if batch_idx >= len(batches):
            raise RuntimeError(f"批{batch_idx}已不存在")

        # Build known_context from current_pkg (so AI doesn't redefine)
        known_ctx = {}
        for c in current_pkg.get("classes", []):
            known_ctx[c.get("name", "")] = {
                "label": c.get("label", ""),
                "attrs": [a.get("name", "") for a in c.get("attributes", [])],
            }

        doc_text = "\n\n---\n\n".join(f"[文档: {fn}]\n{txt}" for fn, txt in batches[batch_idx])
        result = await extractor._extract_entities_with_attrs_from_docs(doc_text, known_context=known_ctx)

        # Merge into current_pkg (same logic as pipeline merge)
        import uuid as _uuid
        enum_map = {e["name"]: e["id"] for e in current_pkg.get("enumerations", [])}

        # Enumerations first (so attribute.enum_ref can resolve)
        existing_enum_names = {e["name"] for e in current_pkg.get("enumerations", [])}
        for en in result.get("enumerations", []):
            ename = en.get("name", "")
            if not ename or ename in existing_enum_names:
                continue
            new_eid = str(_uuid.uuid4())
            enum_map[ename] = new_eid
            current_pkg.setdefault("enumerations", []).append({
                "id": new_eid, "name": ename, "label": en.get("label", ""),
                "description": en.get("description"),
                "literals": [
                    {"id": str(_uuid.uuid4()), "name": l.get("name", ""), "label": l.get("label", "")}
                    for l in en.get("literals", [])
                ],
            })
            existing_enum_names.add(ename)

        # Classes (with attributes)
        cls_by_name = {c["name"]: c for c in current_pkg.get("classes", [])}
        for cls in result.get("classes", []):
            cname = cls.get("name", "")
            if not cname:
                continue
            if cname in cls_by_name:
                target = cls_by_name[cname]
                existing_attr_names = {a.get("name") for a in target.get("attributes", [])}
                for ad in cls.get("attributes", []):
                    aname = ad.get("name", "")
                    if not aname or aname in existing_attr_names:
                        continue
                    dt = ad.get("data_type", "String")
                    if dt not in ("String", "Integer", "Float", "Boolean", "Date", "Enum", "Reference"):
                        dt = "String"
                    target.setdefault("attributes", []).append({
                        "id": str(_uuid.uuid4()),
                        "name": aname,
                        "label": ad.get("label", ""),
                        "description": ad.get("description"),
                        "data_type": dt,
                        "unit": ad.get("unit"),
                        "enum_ref": enum_map.get(ad.get("enum_name")) if dt == "Enum" else None,
                        "multiplicity": {
                            "lower": (ad.get("multiplicity") or {}).get("lower", 1),
                            "upper": (ad.get("multiplicity") or {}).get("upper", 1),
                        },
                        "is_inherited": False,
                    })
                    existing_attr_names.add(aname)
            else:
                # New class with attributes
                new_cls = {
                    "id": str(_uuid.uuid4()),
                    "name": cname,
                    "label": cls.get("label", ""),
                    "description": cls.get("description"),
                    "attributes": [],
                    "constraints": [],
                }
                for ad in cls.get("attributes", []):
                    aname = ad.get("name", "")
                    if not aname:
                        continue
                    dt = ad.get("data_type", "String")
                    if dt not in ("String", "Integer", "Float", "Boolean", "Date", "Enum", "Reference"):
                        dt = "String"
                    new_cls["attributes"].append({
                        "id": str(_uuid.uuid4()),
                        "name": aname,
                        "label": ad.get("label", ""),
                        "description": ad.get("description"),
                        "data_type": dt,
                        "unit": ad.get("unit"),
                        "enum_ref": enum_map.get(ad.get("enum_name")) if dt == "Enum" else None,
                        "multiplicity": {
                            "lower": (ad.get("multiplicity") or {}).get("lower", 1),
                            "upper": (ad.get("multiplicity") or {}).get("upper", 1),
                        },
                        "is_inherited": False,
                    })
                current_pkg.setdefault("classes", []).append(new_cls)
                cls_by_name[cname] = new_cls

    elif batch_type == "attribute_extraction":
        cls_batch_idx = fb.get("class_batch_idx", 0)
        doc_idx = fb.get("doc_batch_idx", 0)
        class_names = fb.get("class_names", [])

        if doc_idx >= len(batches):
            raise RuntimeError(f"文档批{doc_idx}已不存在")

        # Find the classes
        cls_batch = [
            {"name": c["name"], "label": c.get("label", ""), "description": c.get("description", "")}
            for c in current_pkg.get("classes", []) if c["name"] in class_names
        ]
        if not cls_batch:
            raise RuntimeError(f"类批{class_names}在当前包中找不到")

        doc_text = "\n\n---\n\n".join(f"[文档: {fn}]\n{txt}" for fn, txt in batches[doc_idx])

        # Known attrs context
        attr_context = {}
        for cls_name in class_names:
            cls = next((c for c in current_pkg.get("classes", []) if c["name"] == cls_name), None)
            if cls and cls.get("attributes"):
                attr_context[cls_name] = [a.get("name", "") for a in cls["attributes"]]

        result = await extractor._extract_attrs_from_docs(
            doc_text, cls_batch, enum_map, known_attrs_context=attr_context
        )

        # Merge: add new attributes to existing classes
        for cls_data in result:
            cname = cls_data.get("name", "")
            target_cls = next((c for c in current_pkg.get("classes", []) if c["name"] == cname), None)
            if not target_cls:
                continue
            existing_names = {a.get("name") for a in target_cls.get("attributes", [])}
            import uuid as _uuid
            for attr in cls_data.get("attributes", []):
                if attr.get("name") and attr.get("name") not in existing_names:
                    # Convert to full attribute with id
                    new_attr = {
                        "id": str(_uuid.uuid4()),
                        "name": attr.get("name"),
                        "label": attr.get("label", ""),
                        "description": attr.get("description"),
                        "data_type": attr.get("data_type", "String"),
                        "unit": attr.get("unit"),
                        "enum_ref": enum_map.get(attr.get("enum_name")) if attr.get("enum_name") else None,
                        "multiplicity": {
                            "lower": attr.get("multiplicity", {}).get("lower", 1),
                            "upper": attr.get("multiplicity", {}).get("upper", 1),
                        },
                        "is_inherited": False,
                    }
                    target_cls.setdefault("attributes", []).append(new_attr)
                    existing_names.add(attr.get("name"))

    elif batch_type == "entity_discovery":
        batch_idx = fb.get("batch_idx", 0)
        if batch_idx >= len(batches):
            raise RuntimeError(f"实体批{batch_idx}已不存在")

        known_names = [c["name"] for c in current_pkg.get("classes", [])]
        known_names += [e["name"] for e in current_pkg.get("enumerations", [])]
        doc_text = "\n\n---\n\n".join(f"[文档: {fn}]\n{txt}" for fn, txt in batches[batch_idx])

        entities = await extractor._discover_entities_from_docs(doc_text, known_context=known_names)

        import uuid as _uuid
        existing_cls_names = {c["name"] for c in current_pkg.get("classes", [])}
        for c in entities.get("classes", []):
            if c.get("name") and c["name"] not in existing_cls_names:
                current_pkg.setdefault("classes", []).append({
                    "id": str(_uuid.uuid4()),
                    "name": c.get("name"),
                    "label": c.get("label", ""),
                    "description": c.get("description"),
                    "attributes": [],
                    "constraints": [],
                })
        existing_enum_names = {e["name"] for e in current_pkg.get("enumerations", [])}
        for e in entities.get("enumerations", []):
            if e.get("name") and e["name"] not in existing_enum_names:
                current_pkg.setdefault("enumerations", []).append({
                    "id": str(_uuid.uuid4()),
                    "name": e["name"],
                    "label": e.get("label", ""),
                    "literals": [
                        {"id": str(_uuid.uuid4()), "name": l.get("name", ""), "label": l.get("label", "")}
                        for l in e.get("literals", [])
                    ],
                })

    elif batch_type == "association_extraction":
        doc_idx = fb.get("doc_batch_idx", 0)
        if doc_idx >= len(batches):
            raise RuntimeError(f"关联文档批{doc_idx}已不存在")

        known_assocs = [a["name"] for a in current_pkg.get("associations", [])]
        doc_text = "\n\n---\n\n".join(f"[文档: {fn}]\n{txt}" for fn, txt in batches[doc_idx])

        # Reconstruct MOFClass-like objects
        class MOFLite:
            def __init__(self, c):
                self.name = c["name"]; self.label = c.get("label", ""); self.description = c.get("description", ""); self.id = c["id"]
        classes_lite = [MOFLite(c) for c in current_pkg.get("classes", [])]

        assoc_data = await extractor._extract_assocs_from_docs(doc_text, classes_lite, known_assocs=known_assocs)

        import uuid as _uuid
        class_id_map = {c["name"]: c["id"] for c in current_pkg.get("classes", [])}
        for ad in assoc_data.get("associations", []):
            aname = ad.get("name", "")
            if aname and aname not in known_assocs:
                current_pkg.setdefault("associations", []).append({
                    "id": str(_uuid.uuid4()),
                    "name": aname,
                    "label": ad.get("label", ""),
                    "source": {
                        "class_ref": class_id_map.get(ad.get("source_class", ""), ""),
                        "class_name": ad.get("source_class", ""),
                        "role_name": ad.get("source_role"),
                        "multiplicity": ad.get("source_multiplicity", {"lower": 1, "upper": 1}),
                    },
                    "target": {
                        "class_ref": class_id_map.get(ad.get("target_class", ""), ""),
                        "class_name": ad.get("target_class", ""),
                        "role_name": ad.get("target_role"),
                        "multiplicity": ad.get("target_multiplicity", {"lower": 0, "upper": -1}),
                    },
                    "association_type": ad.get("association_type", "composition"),
                })
    else:
        raise RuntimeError(f"未知的失败类型: {batch_type}")


@router.post("/start/{task_id}")
async def start_extraction_task(task_id: str):
    """Authorize a ready task to actually begin AI calls."""
    task = _tasks.get(task_id)
    if task is None:
        raise HTTPException(404, f"Task {task_id} not found")
    if task.get("status") not in ("ready", "paused"):
        return {"status": task.get("status"), "message": "任务不在可启动状态"}
    _start_flags[task_id] = True
    _pause_flags[task_id] = False
    _update_task(task_id, status="running", message="开始执行...")
    _add_log(task_id, "step", "用户点击开始，正式启动提取")
    return {"status": "started"}


@router.post("/pause/{task_id}")
async def pause_extraction_task(task_id: str):
    """Pause a running task — AI calls will wait at checkpoints."""
    task = _tasks.get(task_id)
    if task is None:
        raise HTTPException(404, f"Task {task_id} not found")
    if task.get("status") != "running":
        return {"status": task.get("status"), "message": "任务不在运行中"}
    _pause_flags[task_id] = True
    _update_task(task_id, status="paused", message="已暂停，下个批次前会停下等待")
    _add_log(task_id, "info", "⏸ 用户请求暂停")
    return {"status": "paused"}


@router.post("/resume/{task_id}")
async def resume_extraction_task(task_id: str):
    """Resume a paused task."""
    task = _tasks.get(task_id)
    if task is None:
        raise HTTPException(404, f"Task {task_id} not found")
    if task.get("status") != "paused":
        return {"status": task.get("status"), "message": "任务不在暂停状态"}
    _pause_flags[task_id] = False
    _update_task(task_id, status="running", message="已恢复执行...")
    _add_log(task_id, "step", "▶ 用户继续执行")
    return {"status": "resumed"}


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


def is_paused(task_id: str) -> bool:
    return _pause_flags.get(task_id, False)


async def wait_if_paused(task_id: str):
    """Block until task is unpaused or cancelled."""
    while is_paused(task_id) and not is_cancelled(task_id):
        await asyncio.sleep(0.5)


async def wait_for_start(task_id: str, timeout_seconds: int = 600):
    """Block until task is authorized to begin. Returns False if timeout/cancelled."""
    waited = 0
    while not _start_flags.get(task_id, False):
        if is_cancelled(task_id):
            return False
        if waited >= timeout_seconds:
            return False
        await asyncio.sleep(0.5)
        waited += 0.5
    return True


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
