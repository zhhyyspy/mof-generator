"""
AI-powered entity extraction using configurable LLM provider.

Two independent pipelines:
  Pipeline A: Documents → M1 model (domain-specific classes, attributes, associations)
  Pipeline B: M1 model → M2 meta-model (generalized/abstract base types)
"""
from __future__ import annotations

import json
import re
import uuid
import asyncio
from typing import Callable, Optional

from backend.models.m3_schema import (
    Package, MOFClass, Attribute, Enumeration, EnumLiteral,
    Association, AssociationEnd, Multiplicity, Constraint,
    ComplexType, PrimitiveDataType, StructuralPattern,
)
from backend.services.llm_client import get_active_client, LLMClient


# ============================================================================
#                     V3.1 Document-type-aware strategy
# ============================================================================

_DOC_TYPE_PROMPTS = {
    "spec": """
📘 文档类型: 制度规范 (国标/行标/管理规定/设计规范)
抽取策略:
- 主动抽取文档里定义的**类型概念**作为 M1 Class
- 典型线索句式:
  • "...应具备以下属性..."
  • "...分为 A、B、C 三类"
  • "...由...组成/包括..."
  • "...的定义..."
- 属性粒度:规范里写的每个字段都抽
- 遇到具体名字 (如 "1 号机组", "惠州电站") 跳过,它们是 M0 实例""",
    "manual": """
📗 文档类型: 技术说明书 (产品手册/设计规格书/技术方案)
抽取策略:
- 抽取设备/组件/子系统作为 M1 Class
- 同时积极抽取它们的技术参数作为属性 (额定功率、尺寸、规格等)
- 注意区分"产品系列"(类,应抽) 和"具体订单型号" (实例,不抽)
- 关联重点:谁包含谁 (composition)、谁使用谁 (uses)""",
    "ledger": """
📊 文档类型: 实例表单 (台账/清单/登记簿)
⚠️ 重要约束:这类文档主要是 M0 数据,不是 M1 类型定义!
抽取策略:
- 不要把每一行 (每条记录) 抽成一个 Class — 它们是实例
- 只抽取:
  1. 表头/字段名 → 抽为某个类的属性 (如果能推断出所属的类名)
  2. 分组栏/分类栏 → 如果发现表格明确分组 (如"机电类/建筑类"),可以抽该分组词为枚举或类
- 如果整张表看起来就是"一大批同类实例",只抽取 1 个代表类 + 所有字段作属性""",
    "process": """
💬 文档类型: 业务过程 (会议纪要/邮件/工作汇报/讨论记录)
抽取策略:
- 抽取反复出现的**角色** (审批人/责任方/评审组) 为 Class
- 抽取反复出现的**阶段/活动** (立项/审查/批复) 为 Class
- 抽取**决策点/节点** 为 Class (如: 评审会、批复文、里程碑)
- 不要抽取具体人名、具体事件、具体日期 — 它们是 M0
- 关联重点: 谁负责谁 (responsible)、谁由谁产出 (produces)""",
    "auto": """
🔍 文档类型: 未标注 (按通用策略抽取)
抽取策略:
- 先判断文档主体是类型定义 (抽 Class) 还是实例记录 (保守抽)
- 含大量具体名称/编号 → 保守,只抽高频类型名
- 含"...由...组成"、"...分为..." 等规范化定义 → 积极抽类""",
}


def _doc_type_guidance(doc_type: str) -> str:
    """Return prompt snippet tailored to the given doc_type. Fallback to 'auto'."""
    return _DOC_TYPE_PROMPTS.get(doc_type) or _DOC_TYPE_PROMPTS["auto"]


# Module-level classify helper used by routers/documents.py /classify endpoint.
async def classify_document_type(text_excerpt: str, filename: str = "") -> str:
    """Ask LLM to classify a doc into one of spec/manual/ledger/process.
    Returns a string matching VALID_DOC_TYPES, or 'auto' on failure."""
    client = get_active_client()
    prompt = f"""请判断下面这份业务文档的类型,在以下 4 类中选 1 个:

- spec:     制度规范 (国标 / 行标 / 企业管理规定 / 设计规范 / 技术规范)
- manual:   技术说明书 (产品手册 / 设计规格书 / 施工方案 / 操作手册)
- ledger:   实例表单 (设备台账 / 人员清单 / 项目登记簿 / 具体 Excel 数据表)
- process:  业务过程 (会议纪要 / 邮件交流 / 工作汇报 / 讨论记录 / 签批流程)

文件名: {filename}

内容摘录 (前 1200 字):
---
{text_excerpt}
---

仅返回一个标签 (spec/manual/ledger/process),不要任何其他文字。"""
    try:
        resp = await client.chat(prompt, max_tokens=20)
        answer = (resp or "").strip().lower()
        for t in ("spec", "manual", "ledger", "process"):
            if t in answer:
                return t
    except Exception:
        pass
    return "auto"


# M0-instance heuristics: runs after extraction to flag likely instance names
# that slipped through as classes. `suspected_m0=true` in description is another
# signal the LLM itself may attach.
_M0_REGEXES = [
    # Numeric unit / item numbering: "1号机组", "2号电机", "#5泵", "A3段", etc.
    re.compile(r'\d+\s*(?:号|#|组|台|套|段|车|辆|站|房|号机|号泵|号炉|号电机)'),
    # Type codes / model numbers: "HYB-400", "WT-2023-08", "PLC-XY-02"
    re.compile(r'\b[A-Z]{2,}[-_]\d+'),
    # Chinese year markers: "2024年", "20年度"
    re.compile(r'\d{2,4}\s*年(?:度)?'),
    # Province/city name prefix (a sampling of common admin units)
    re.compile(r'(?:北京|上海|广州|深圳|惠州|东莞|佛山|中山|珠海|南京|杭州'
               r'|苏州|武汉|成都|重庆|西安|沈阳|大连|青岛|济南|天津|昆明'
               r'|长沙|合肥|郑州|福州|厦门|南昌|石家庄|太原|乌鲁木齐|兰州'
               r'|银川|西宁|拉萨|哈尔滨|长春|呼和浩特|海口|南宁|贵阳'
               r'|广东|江苏|浙江|山东|河北|河南|湖南|湖北|四川|安徽|福建'
               r'|江西|云南|贵州|陕西|山西|辽宁|吉林|黑龙江|内蒙古|新疆'
               r'|甘肃|宁夏|青海|西藏|海南|广西)'),
]


def detect_m0_instances(classes: list[dict]) -> list[str]:
    """Return the IDs of classes that look like M0 instances based on name heuristics.
    Used by the review panel to default-exclude them, with one-click restore."""
    flagged: list[str] = []
    for c in classes:
        nm = (c.get("name") or "") + " " + (c.get("label") or "")
        desc = (c.get("description") or "").lower()
        if "suspected_m0=true" in desc:
            flagged.append(c.get("id"))
            continue
        for rx in _M0_REGEXES:
            if rx.search(nm):
                flagged.append(c.get("id"))
                break
    return [cid for cid in flagged if cid]


def _build_attributes_from_raw(attr_dicts: list) -> list[Attribute]:
    """Convert a list of raw attribute dicts (from LLM output) into Attribute objects.

    Used by M2 materialization for both flat classes and per-level MetaClasses in a
    StructuralPattern. Normalizes data_type (falling back to String for unknown),
    multiplicity (handling missing fields), and carries through unit/description.
    """
    out: list[Attribute] = []
    for a in attr_dicts:
        if not isinstance(a, dict):
            continue
        name = (a.get("name") or "").strip()
        if not name:
            continue
        dt = a.get("data_type", "String")
        try:
            PrimitiveDataType(dt)
        except ValueError:
            dt = "String"
        mult = a.get("multiplicity", {}) or {}
        out.append(Attribute(
            id=str(uuid.uuid4()),
            name=name,
            label=a.get("label", "") or None,
            description=a.get("description") or None,
            data_type=dt,
            unit=a.get("unit") or None,
            enum_ref=a.get("enum_ref") or None,
            multiplicity=Multiplicity(
                lower=mult.get("lower", 1) if isinstance(mult, dict) else 1,
                upper=mult.get("upper", 1) if isinstance(mult, dict) else 1,
            ),
        ))
    return out


SYSTEM_PROMPT = """You are a data modeling expert specializing in MOF (Meta-Object Facility) methodology for industrial asset management, particularly in the Chinese energy sector (pumped storage, electrochemical storage, conventional hydropower).

You understand the MOF 4-layer architecture:
- M3 (meta-meta model): The modeling language itself — Class, Attribute, DataType, Association, Enumeration, Multiplicity, Constraint, Package
- M2 (meta model): Generic/abstract business object types (e.g., "Equipment" with basic universal attributes)
- M1 (model): Domain-specific templates that specialize M2 (e.g., "PumpedStorageUnit" with rated capacity, head, etc.)
- M0 (instances): Real business data

M3 allowed data types: String, Float, Integer, Date, Boolean, Enum

RULES:
1. Only produce constructs valid under the M3 schema
2. Data types MUST be one of: String, Float, Integer, Date, Boolean, Enum
3. Use English PascalCase for class names, camelCase for attribute names
4. Use Chinese for labels (display names)

CRITICAL OUTPUT FORMAT:
- Your response MUST start with `{` and end with `}`
- NO markdown code fences (no ```json or ```)
- NO explanation text before or after the JSON
- NO chain-of-thought reasoning in the output
- NO comments in the JSON
- The FIRST CHARACTER of your response must be `{`
- If you cannot produce valid JSON, output `{"classes": [], "enumerations": [], "confidence_notes": ["无法分析"]}`
"""


def _clean_json_text(text: str) -> str:
    """Clean common LLM JSON issues before parsing."""
    text = text.strip()
    # Remove markdown code fences (including those with language tags)
    text = re.sub(r"```(?:json|JSON)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*", "", text)
    # Remove <think>...</think> tags (Qwen/DeepSeek reasoning models)
    text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<thinking>[\s\S]*?</thinking>", "", text, flags=re.IGNORECASE)
    # Remove common prefixes like "Here is the JSON:" or "好的，分析如下:"
    text = text.strip()

    # Extract the outermost { ... } — aggressive scan
    first = text.find("{")
    if first < 0:
        return ""  # No JSON object at all
    if first > 0:
        text = text[first:]
    last = text.rfind("}")
    if last >= 0 and last < len(text) - 1:
        text = text[:last + 1]

    # Remove single-line comments
    text = re.sub(r'//[^\n]*', '', text)
    # Remove trailing commas before } or ]
    text = re.sub(r',\s*([}\]])', r'\1', text)
    # Insert missing commas between array elements: } { or } " or ] {
    text = re.sub(r'(\})\s*(\{)', r'\1,\2', text)
    text = re.sub(r'(\})\s*(")', r'\1,\2', text)
    text = re.sub(r'(\])\s*(\[)', r'\1,\2', text)
    # Insert missing comma after string value followed by key: "value" "key"
    text = re.sub(r'(")\s+(")', r'\1,\2', text)

    return text


def _fix_json_at_position(text: str, error_pos: int) -> str:
    """Try to fix JSON at the exact error position — missing comma or rogue quote."""
    if error_pos <= 0 or error_pos >= len(text):
        return text

    # Strategy 1: Check if it's a missing comma (} followed by { or ")
    before = text[max(0, error_pos-5):error_pos].rstrip()
    after = text[error_pos:error_pos+5].lstrip()
    if before.endswith('}') and (after.startswith('{') or after.startswith('"')):
        # Insert comma
        insert_at = text.rindex('}', 0, error_pos) + 1
        return text[:insert_at] + ',' + text[insert_at:]
    if before.endswith('"') and after.startswith('"'):
        return text[:error_pos] + ',' + text[error_pos:]

    # Strategy 2: Escape rogue unescaped quote
    search_start = max(0, error_pos - 200)
    region = text[search_start:error_pos + 200]

    quote_positions = []
    for i, ch in enumerate(region):
        if ch == '"' and (i == 0 or region[i-1] != '\\'):
            quote_positions.append(i)

    rel_pos = error_pos - search_start
    candidates = [p for p in quote_positions if p < rel_pos and p > 0]
    if candidates:
        fix_pos = candidates[-1]  # the rogue quote
        abs_pos = search_start + fix_pos
        text = text[:abs_pos] + '\\"' + text[abs_pos+1:]

    return text


def _extract_json(text: str) -> dict:
    """Extract JSON from LLM response with robust multi-strategy error recovery."""
    # Defensive: handle None/empty
    if not text or not text.strip():
        raise ValueError(f"AI返回了空响应 (原始: {repr(text)[:200]})")

    cleaned = _clean_json_text(text)

    # If cleaning stripped everything (no { found), the AI didn't return JSON at all
    if not cleaned or "{" not in cleaned:
        # Show what the AI actually said (helpful for debugging local LLMs)
        preview = text.strip()[:500]
        raise ValueError(
            f"AI返回的内容不包含JSON对象。\n"
            f"实际返回 (前500字符): {preview}\n"
            f"可能原因: 本地LLM未遵守JSON输出格式，建议换用更强的模型(如Claude/GPT-4/Qwen-Max)"
        )

    # Attempt 1: direct parse
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Attempt 2: iterative fix — try up to 10 rounds of escaping rogue quotes
    fixed = cleaned
    for _ in range(10):
        try:
            return json.loads(fixed)
        except json.JSONDecodeError as e:
            if "Expecting ',' delimiter" in str(e) or "Expecting ':' delimiter" in str(e):
                new_fixed = _fix_json_at_position(fixed, e.pos or 0)
                if new_fixed == fixed:
                    break  # no progress, stop
                fixed = new_fixed
            else:
                break

    # Attempt 3: fix unbalanced braces/brackets
    try:
        opens = fixed.count("{") - fixed.count("}")
        brackets = fixed.count("[") - fixed.count("]")
        tail = "}" * max(opens, 0) + "]" * max(brackets, 0)
        if tail:
            return json.loads(fixed + tail)
    except json.JSONDecodeError:
        pass

    # Attempt 4: regex extract
    match = re.search(r"\{[\s\S]*\}", fixed)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    # All local attempts failed — raise with diagnostic context
    try:
        json.loads(cleaned)
    except json.JSONDecodeError as e:
        pos = e.pos or 0
        start = max(0, pos - 60)
        end = min(len(cleaned), pos + 60)
        snippet = cleaned[start:end]
        raise ValueError(
            f"JSON解析失败 (位置 {pos}): {e.msg}\n"
            f"问题区域: ...{snippet}..."
        )


class AIExtractor:

    def __init__(self):
        self.llm = get_active_client()
        # Per-instance batch size, driven by the active LLM provider's config.
        # Lowering this reduces per-call prompt size WITHOUT breaking cross-batch context:
        # context is preserved via the known_context / known_attrs_context / known_assocs
        # hints passed between batches (see _discover_entities_from_docs,
        # _extract_attrs_from_docs, _extract_assocs_from_docs).
        configured = getattr(self.llm.config, "batch_max_chars", None) or 8000
        # Clamp to a usable range: at least 2000 chars (otherwise context hint itself
        # dominates the prompt), at most 40000 chars (reasoning models choke above this
        # regardless of tokenization).
        self.BATCH_MAX_CHARS = max(2000, min(int(configured), 40000))

    # Extraction calls get 5 minutes timeout (large prompts take time)
    EXTRACTION_TIMEOUT = 300
    # Reasoning models (Qwen3+, DeepSeek-R1) need a LOT more tokens for thinking
    DEFAULT_MAX_TOKENS = 16384

    async def _ask(self, prompt: str, max_tokens: int = None) -> dict:
        """Send prompt to the active LLM and parse JSON response."""
        effective_max_tokens = max_tokens or self.DEFAULT_MAX_TOKENS
        text = await self.llm.chat(
            system=SYSTEM_PROMPT,
            user=prompt,
            temperature=0,
            max_tokens=effective_max_tokens,
            timeout_override=self.EXTRACTION_TIMEOUT,
        )
        return _extract_json(text)

    # ==================================================================
    # Pipeline A: Documents → M1
    # ==================================================================
    # Note: BATCH_MAX_CHARS is now set per-instance in __init__ from the
    # active LLM config's batch_max_chars field. Cross-batch context
    # association is preserved by explicit context-hint injection between
    # batches — see known_context / known_attrs_context / known_assocs in
    # the per-batch prompt builders.

    async def extract_m1(
        self,
        document_texts: list[tuple[str, str]],  # list of (filename, full_text)
        document_types: Optional[list[str]] = None,   # V3.1: per-doc type tag
        progress_callback: Optional[Callable] = None,
        parallel_callback: Optional[Callable] = None,
        check_cancelled: Optional[Callable] = None,
        conversation_callback: Optional[Callable] = None,
        failed_batch_callback: Optional[Callable] = None,
        partial_result_callback: Optional[Callable] = None,
        pause_waiter: Optional[Callable] = None,  # async () — blocks while paused
    ) -> dict:
        """
        Extract M1 model from full-text documents using batch processing.

        Documents are split into batches by size. Each batch sends FULL text to AI.
        Later batches receive context from earlier batches (already-found entities)
        to avoid duplication and maintain cross-document relationships.
        """
        def _check():
            if check_cancelled and check_cancelled():
                raise RuntimeError("用户中止提取")

        _conv = conversation_callback  # shorthand

        # Monkey-patch self._ask to capture full LLM conversations
        _orig_ask = self._ask
        _call_count = [0]
        _cb = conversation_callback

        async def _ask_with_conv(prompt: str, max_tokens: int = None) -> dict:
            # Default to DEFAULT_MAX_TOKENS (16384) for reasoning models
            if max_tokens is None:
                max_tokens = self.DEFAULT_MAX_TOKENS
            _call_count[0] += 1
            n = _call_count[0]
            prompt_len = len(prompt)

            if _cb:
                # Show prompt preview + full content for expand
                first_line = prompt.strip().split('\n')[0][:120]
                full_prompt = f"[SYSTEM]\n{SYSTEM_PROMPT}\n\n[USER]\n{prompt}"
                await _cb("prompt", f"[#{n}] {first_line}...",
                           f"发送 {prompt_len:,} 字符",
                           full_prompt)
                await _cb("waiting", f"[#{n}] 等待AI响应中...", "")

            # Call LLM directly to capture raw text before JSON parsing
            raw_text = await self.llm.chat(
                system=SYSTEM_PROMPT, user=prompt,
                temperature=0, max_tokens=max_tokens,
                timeout_override=self.EXTRACTION_TIMEOUT,
            )

            if _cb:
                # Show response preview + full raw text for expand
                preview = raw_text[:150].replace('\n', ' ')
                await _cb("response", f"[#{n}] {preview}...",
                           f"收到 {len(raw_text):,} 字符",
                           raw_text)

            # Parse JSON (with robust local fixing)
            return _extract_json(raw_text)

        # Wrap _ask to also honor pause
        _ask_with_conv_orig = _ask_with_conv
        async def _ask_with_pause(p: str, max_tokens: int = None) -> dict:
            if pause_waiter:
                await pause_waiter()
            return await _ask_with_conv_orig(p, max_tokens)
        self._ask = _ask_with_pause

        def _check():
            if check_cancelled and check_cancelled():
                raise RuntimeError("用户中止提取")

        try:
            return await self._run_extract_m1_pipeline(
                document_texts, document_types or [], progress_callback, parallel_callback, _check,
                failed_batch_callback, partial_result_callback,
            )
        finally:
            self._ask = _orig_ask

    async def _run_extract_m1_pipeline(self, document_texts, document_types, progress_callback, parallel_callback, _check,
                                         failed_batch_callback=None, partial_result_callback=None):
        """The actual extraction pipeline (called with monkey-patched self._ask)."""
        _fbc = failed_batch_callback
        _prc = partial_result_callback

        # ---- Step 1: Split any oversized file into chunks ----
        # If a single file exceeds BATCH_MAX_CHARS, chunk it with filename annotations
        # (user's requirement: every file complete — but impossible if > batch limit, so chunk)
        # V3.1: each entry is (filename, text, doc_type) — doc_type inherited into chunks.
        preprocessed: list[tuple[str, str, str]] = []
        for i_doc, (filename, text) in enumerate(document_texts):
            dtype = document_types[i_doc] if i_doc < len(document_types) else "auto"
            if len(text) <= self.BATCH_MAX_CHARS:
                preprocessed.append((filename, text, dtype))
                continue
            # Split large file into chunks with 500-char overlap for context preservation
            chunk_size = self.BATCH_MAX_CHARS - 500  # leave room for filename header
            overlap = 500
            chunks = []
            i = 0
            while i < len(text):
                chunk = text[i:i + chunk_size]
                chunks.append(chunk)
                i += chunk_size - overlap
            total_chunks = len(chunks)
            for idx, chunk in enumerate(chunks):
                chunk_filename = f"{filename} [第{idx+1}/{total_chunks}部分]"
                preprocessed.append((chunk_filename, chunk, dtype))

        # ---- Step 2: Pack chunks into batches by size ----
        # Each batch is a list of (filename, text, doc_type) triples.
        batches = []
        current_batch = []
        current_size = 0
        for filename, text, dtype in preprocessed:
            if current_size + len(text) > self.BATCH_MAX_CHARS and current_batch:
                batches.append(current_batch)
                current_batch = []
                current_size = 0
            current_batch.append((filename, text, dtype))
            current_size += len(text)
        if current_batch:
            batches.append(current_batch)

        total_batches = len(batches)

        if progress_callback:
            if total_batches > 1:
                await progress_callback("extracting_entities", 0.10,
                    f"文档已分为 {total_batches} 批处理，单趟提取实体+属性...")
            else:
                await progress_callback("extracting_entities", 0.10,
                    "单趟提取实体类型、属性和枚举...")

        MAX_CONCURRENT = 3  # Max parallel AI calls to avoid rate limiting
        sem = asyncio.Semaphore(MAX_CONCURRENT)

        # Doc batch texts (used by both combined extraction and association extraction)
        doc_batch_texts = [
            "\n\n---\n\n".join(f"[文档: {fn}]\n{txt}" for fn, txt, _dt in batch)
            for batch in batches
        ]

        # Helper to emit partial results to UI after each milestone
        async def _emit_partial(mof_cls_list=None, assocs_list=None, enum_list=None):
            if not _prc:
                return
            try:
                partial_pkg = Package(
                    id=str(uuid.uuid4()),
                    name="M1Package_Partial", label="M1模型(提取中)",
                    classes=mof_cls_list or [],
                    enumerations=enum_list or [],
                    associations=assocs_list or [],
                )
                await _prc(partial_pkg.model_dump())
            except Exception as e:
                # Never let partial emit kill the extraction
                pass

        # ================================================================
        # COMBINED EXTRACTION (replaces the old two-phase discover + attrs)
        # ================================================================
        # Previously: Phase A = discover classes (N batches), Phase B = for
        # each class × each doc batch, ask "find attributes" (N_classes × N_docs
        # LLM calls — up to ~15,000 for large inputs).
        # Now: one LLM call per doc batch yields classes WITH their attributes.
        # Cross-batch context hint prevents redefinition and keeps names consistent.
        # Complexity: O(N_doc_batches) instead of O(N_classes × N_doc_batches).

        # Shared merged state across all batches (mutated under lock)
        merged_classes: dict = {}    # name -> dict with "attributes" list and "_attr_names" set
        merged_enums: dict = {}      # name -> dict with "literals" list and "_lit_names" set
        all_confidence_notes: list = []
        merge_lock = asyncio.Lock()
        completed_batches = 0

        def _build_context_snapshot() -> dict:
            """Snapshot merged_classes for passing as known_context to later batches."""
            ctx = {}
            for cname, cls in merged_classes.items():
                ctx[cname] = {
                    "label": cls.get("label", ""),
                    "attrs": list(cls.get("_attr_names", [])),
                }
            return ctx

        def _merge_batch_result(result: dict):
            """Merge a batch's result into the shared accumulated state."""
            for cls in result.get("classes", []):
                cname = cls.get("name", "").strip()
                if not cname:
                    continue
                if cname not in merged_classes:
                    merged_classes[cname] = {
                        "name": cname,
                        "label": cls.get("label", ""),
                        "description": cls.get("description", "") or "",
                        "attributes": [],
                        "_attr_names": set(),
                        "hierarchy_hint": None,
                    }
                existing = merged_classes[cname]
                # Fill in better label/description if the new one is richer
                if cls.get("label") and not existing["label"]:
                    existing["label"] = cls["label"]
                new_desc = cls.get("description") or ""
                if new_desc and len(new_desc) > len(existing["description"]):
                    existing["description"] = new_desc
                # Union attributes by name (first occurrence wins — honors known_context)
                for a in cls.get("attributes", []):
                    aname = (a.get("name") or "").strip()
                    if not aname or aname in existing["_attr_names"]:
                        continue
                    existing["attributes"].append(a)
                    existing["_attr_names"].add(aname)
                # Preserve hierarchy_hint: keep first non-empty one found across batches
                hint = cls.get("hierarchy_hint")
                if hint and isinstance(hint, dict) and not existing.get("hierarchy_hint"):
                    # Only keep hints with at least one useful field
                    if hint.get("theme_hint") or hint.get("level_hint") or hint.get("parent_name_hint"):
                        existing["hierarchy_hint"] = {
                            "theme_hint": hint.get("theme_hint", ""),
                            "level_hint": hint.get("level_hint", ""),
                            "parent_name_hint": hint.get("parent_name_hint", ""),
                        }

            for en in result.get("enumerations", []):
                ename = en.get("name", "").strip()
                if not ename:
                    continue
                if ename not in merged_enums:
                    merged_enums[ename] = {
                        "name": ename,
                        "label": en.get("label", ""),
                        "description": en.get("description", ""),
                        "literals": [],
                        "_lit_names": set(),
                    }
                existing = merged_enums[ename]
                if en.get("label") and not existing["label"]:
                    existing["label"] = en["label"]
                for lit in en.get("literals", []):
                    lname = (lit.get("name") or "").strip()
                    if not lname or lname in existing["_lit_names"]:
                        continue
                    existing["literals"].append(lit)
                    existing["_lit_names"].add(lname)

            all_confidence_notes.extend(result.get("confidence_notes", []))

        async def process_combined_batch(batch_idx, batch, initial_seed=None):
            """Extract classes (with attributes) + enumerations from one doc batch."""
            _check()
            nonlocal completed_batches
            # Dominant doc_type for this batch: use most frequent among its items
            _dtype_counts: dict[str, int] = {}
            for _fn, _txt, _dt in batch:
                _dtype_counts[_dt] = _dtype_counts.get(_dt, 0) + 1
            batch_doc_type = max(_dtype_counts, key=_dtype_counts.get) if _dtype_counts else "auto"
            subtask_id = f"extract_batch_{batch_idx}"
            batch_filenames = [fn for fn, _txt, _dt in batch]
            subtask_name = (
                f"批{batch_idx+1}: {', '.join(batch_filenames[:2])}"
                + ('...' if len(batch_filenames) > 2 else '')
            )

            if parallel_callback:
                await parallel_callback(subtask_id, subtask_name, "queued")

            async with sem:
                if parallel_callback:
                    await parallel_callback(subtask_id, subtask_name, "running")
                if progress_callback:
                    await progress_callback("extracting_entities",
                        0.10 + 0.55 * (completed_batches / max(total_batches, 1)),
                        f"提取 [{completed_batches}/{total_batches}]: {subtask_name}")

                batch_text = "\n\n---\n\n".join(
                    f"[文档: {fn}]\n{txt}" for fn, txt, _dt in batch
                )

                # Build context snapshot (union of current merged state + first-batch seed)
                async with merge_lock:
                    ctx = _build_context_snapshot()
                if initial_seed:
                    for k, v in initial_seed.items():
                        ctx.setdefault(k, v)

                try:
                    result = await self._extract_entities_with_attrs_from_docs(
                        batch_text, known_context=ctx, doc_type=batch_doc_type,
                    )
                except Exception as e:
                    if parallel_callback:
                        await parallel_callback(subtask_id, subtask_name, "error")
                    all_confidence_notes.append(f"批{batch_idx+1}失败: {str(e)[:200]}")
                    if _fbc:
                        await _fbc({
                            "type": "combined_extraction",
                            "label": f"提取 批{batch_idx+1}",
                            "batch_idx": batch_idx,
                            "filenames": batch_filenames,
                            "error": str(e)[:500],
                        })
                    return

                # Merge under lock so parallel batches don't race
                async with merge_lock:
                    _merge_batch_result(result)
                    completed_batches += 1
                    cls_cnt = len(merged_classes)
                    enum_cnt = len(merged_enums)
                    attr_cnt = sum(len(c["attributes"]) for c in merged_classes.values())

                if parallel_callback:
                    await parallel_callback(subtask_id, subtask_name, "done")
                if progress_callback:
                    await progress_callback("extracting_entities",
                        0.10 + 0.55 * (completed_batches / max(total_batches, 1)),
                        f"提取 [{completed_batches}/{total_batches}]: "
                        f"累计 {cls_cnt} 类 / {attr_cnt} 属性 / {enum_cnt} 枚举")

                # Live partial emission so UI updates progressively
                try:
                    partial_cls, partial_enums = _materialize_partial()
                    await _emit_partial(mof_cls_list=partial_cls, enum_list=partial_enums)
                except Exception:
                    pass

        def _materialize_partial():
            """Build MOFClass / Enumeration objects from current merged state for partial emit."""
            # Build enumerations first for name->id lookup
            enum_map_tmp = {}
            enum_objs_tmp = []
            for ename, en in merged_enums.items():
                eid = str(uuid.uuid4())
                enum_map_tmp[ename] = eid
                enum_objs_tmp.append(Enumeration(
                    id=eid, name=ename, label=en.get("label", ""),
                    description=en.get("description") or None,
                    literals=[
                        EnumLiteral(id=str(uuid.uuid4()),
                                    name=lit.get("name", ""),
                                    label=lit.get("label", ""),
                                    value=lit.get("value"))
                        for lit in en.get("literals", [])
                    ],
                ))
            cls_objs = []
            for cname, cls in merged_classes.items():
                attrs = []
                for ad in cls.get("attributes", []):
                    dt = ad.get("data_type", "String")
                    try:
                        PrimitiveDataType(dt)
                    except ValueError:
                        dt = "String"
                    eref = None
                    if dt == "Enum":
                        eref = enum_map_tmp.get(ad.get("enum_name", ""))
                    m = ad.get("multiplicity", {}) or {}
                    attrs.append(Attribute(
                        id=str(uuid.uuid4()), name=ad.get("name", ""),
                        label=ad.get("label", ""),
                        description=ad.get("description"),
                        data_type=dt, enum_ref=eref, unit=ad.get("unit"),
                        multiplicity=Multiplicity(
                            lower=m.get("lower", 1), upper=m.get("upper", 1),
                        ),
                    ))
                cls_objs.append(MOFClass(
                    id=str(uuid.uuid4()), name=cname,
                    label=cls.get("label", ""),
                    description=cls.get("description") or None,
                    attributes=attrs,
                ))
            return cls_objs, enum_objs_tmp

        # --- Execute: first batch serial (seeds context), rest in parallel ---
        if total_batches == 1:
            await process_combined_batch(0, batches[0])
        else:
            await process_combined_batch(0, batches[0])
            # Build seed context from first-batch merged state
            async with merge_lock:
                seed_ctx = _build_context_snapshot()
            if progress_callback:
                await progress_callback("extracting_entities", 0.15,
                    f"首批完成，剩余 {total_batches-1} 批并行处理中...")
            tasks = [
                process_combined_batch(i, batches[i], initial_seed=seed_ctx)
                for i in range(1, total_batches)
            ]
            await asyncio.gather(*tasks, return_exceptions=True)

        _check()

        # Clear parallel subtasks (phase changing)
        # (routers/extraction.py handles this via phase-change detection)

        # ---- Finalize: build Enumeration and MOFClass objects from merged state ----
        enumerations = []
        enum_name_to_id = {}
        for ename, en in merged_enums.items():
            enum_id = str(uuid.uuid4())
            enum_name_to_id[ename] = enum_id
            literals = [
                EnumLiteral(
                    id=str(uuid.uuid4()),
                    name=lit.get("name", ""),
                    label=lit.get("label", ""),
                    value=lit.get("value"),
                )
                for lit in en.get("literals", [])
            ]
            enumerations.append(Enumeration(
                id=enum_id, name=ename, label=en.get("label", ""),
                description=en.get("description") or None,
                literals=literals,
            ))

        classes_raw = [  # shape expected by downstream code (retry etc.)
            {
                "name": cls["name"],
                "label": cls["label"],
                "description": cls["description"],
                "attributes": cls["attributes"],
                "hierarchy_hint": cls.get("hierarchy_hint"),
            }
            for cls in merged_classes.values()
        ]
        confidence_notes = list(all_confidence_notes)

        mof_classes = []
        total_attrs = 0
        for cls_data in classes_raw:
            cls_id = str(uuid.uuid4())
            attrs = []
            for ad in cls_data.get("attributes", []):
                dt = ad.get("data_type", "String")
                try:
                    PrimitiveDataType(dt)
                except ValueError:
                    dt = "String"
                enum_ref = None
                if dt == "Enum":
                    enum_ref = enum_name_to_id.get(ad.get("enum_name", ""))
                mult = ad.get("multiplicity", {}) or {}
                attrs.append(Attribute(
                    id=str(uuid.uuid4()), name=ad.get("name", ""),
                    label=ad.get("label", ""), description=ad.get("description"),
                    data_type=dt, enum_ref=enum_ref, unit=ad.get("unit"),
                    multiplicity=Multiplicity(
                        lower=mult.get("lower", 1), upper=mult.get("upper", 1),
                    ),
                ))
                total_attrs += 1
            mof_classes.append(MOFClass(
                id=cls_id, name=cls_data.get("name", ""),
                label=cls_data.get("label", ""),
                description=cls_data.get("description") or None,
                attributes=attrs,
                hierarchy_hint=cls_data.get("hierarchy_hint"),
            ))

        if progress_callback:
            await progress_callback("extracting_entities", 0.65,
                f"实体+属性提取完成: {len(mof_classes)} 类, {total_attrs} 属性, {len(enumerations)} 枚举")

        # Emit partial: classes with attributes + enumerations (no associations yet)
        await _emit_partial(mof_cls_list=mof_classes, enum_list=enumerations)

        _check()

        # Step 3: Association Extraction — parallelized, first batch serial for context seed
        class_id_map = {c.name: c.id for c in mof_classes}
        all_associations_raw = []
        known_assoc_names: list = []
        assoc_lock = asyncio.Lock()
        assoc_completed = 0
        n_assoc_batches = len(doc_batch_texts)

        async def process_assoc_batch(doc_idx, doc_text, initial_seed=None):
            nonlocal assoc_completed
            _check()
            subtask_id = f"assoc_batch_{doc_idx}"
            subtask_name = f"关联批{doc_idx+1}"

            if parallel_callback:
                await parallel_callback(subtask_id, subtask_name, "queued")

            async with sem:
                if parallel_callback:
                    await parallel_callback(subtask_id, subtask_name, "running")

                # Snapshot known assoc names for context hint
                async with assoc_lock:
                    known_snapshot = list(known_assoc_names)
                if initial_seed:
                    for n in initial_seed:
                        if n not in known_snapshot:
                            known_snapshot.append(n)

                try:
                    assoc_data = await self._extract_assocs_from_docs(
                        doc_text, mof_classes, known_assocs=known_snapshot,
                    )
                except Exception as e:
                    if parallel_callback:
                        await parallel_callback(subtask_id, subtask_name, "error")
                    async with assoc_lock:
                        confidence_notes.append(f"关联批{doc_idx+1} 失败: {str(e)[:100]}")
                        assoc_completed += 1
                    if _fbc:
                        await _fbc({
                            "type": "association_extraction",
                            "label": f"关联提取 文档批{doc_idx+1}",
                            "doc_batch_idx": doc_idx,
                            "error": str(e)[:500],
                        })
                    return

                # Merge under lock to avoid duplicate appends
                async with assoc_lock:
                    for ad in assoc_data.get("associations", []):
                        aname = ad.get("name", "")
                        if aname and aname not in known_assoc_names:
                            all_associations_raw.append(ad)
                            known_assoc_names.append(aname)
                    confidence_notes.extend(assoc_data.get("confidence_notes", []))
                    assoc_completed += 1
                    progress_pct = assoc_completed / max(n_assoc_batches, 1)

                if parallel_callback:
                    await parallel_callback(subtask_id, subtask_name, "done")
                if progress_callback:
                    await progress_callback("extracting_associations",
                        0.65 + 0.25 * progress_pct,
                        f"关联提取 [{assoc_completed}/{n_assoc_batches}]: 累计 {len(all_associations_raw)} 条")

        # Execute: first batch serial (seeds context), rest in parallel
        if n_assoc_batches == 1:
            await process_assoc_batch(0, doc_batch_texts[0])
        elif n_assoc_batches > 1:
            await process_assoc_batch(0, doc_batch_texts[0])
            async with assoc_lock:
                seed = list(known_assoc_names)
            tasks = [
                process_assoc_batch(i, doc_batch_texts[i], initial_seed=seed)
                for i in range(1, n_assoc_batches)
            ]
            await asyncio.gather(*tasks, return_exceptions=True)

        associations = []
        for ad in all_associations_raw:
            src_name = ad.get("source_class", "")
            tgt_name = ad.get("target_class", "")
            src_mult = ad.get("source_multiplicity", {})
            tgt_mult = ad.get("target_multiplicity", {})
            associations.append(Association(
                id=str(uuid.uuid4()), name=ad.get("name", ""),
                label=ad.get("label", ""), description=ad.get("description"),
                source=AssociationEnd(
                    class_ref=class_id_map.get(src_name, src_name),
                    class_name=src_name, role_name=ad.get("source_role"),
                    multiplicity=Multiplicity(lower=src_mult.get("lower", 1), upper=src_mult.get("upper", 1)),
                ),
                target=AssociationEnd(
                    class_ref=class_id_map.get(tgt_name, tgt_name),
                    class_name=tgt_name, role_name=ad.get("target_role"),
                    multiplicity=Multiplicity(lower=tgt_mult.get("lower", 0), upper=tgt_mult.get("upper", -1)),
                ),
                association_type=ad.get("association_type", "composition"),
            ))

        # ====================================================================
        # Phase 1.5 (V3.1): dedicated composition 补边
        # ====================================================================
        # Existing Phase 2 assoc extraction often misses cross-batch composition
        # (it only sees per-batch text). A consolidation call over FULL class
        # inventory catches explicit containment the per-batch prompts missed.
        # Runs as one extra call at end of pipeline — cheap relative to phase 1.
        if progress_callback:
            await progress_callback("extracting_associations", 0.87,
                f"Phase 1.5 补边: 扫描 {len(mof_classes)} 类的组合关系...")
        try:
            supplement = await self._extract_compositions_supplement(
                mof_classes, associations, doc_batch_texts,
            )
            for comp in supplement:
                # Skip duplicates (by source+target+type)
                src_name = comp.get("source_class", "")
                tgt_name = comp.get("target_class", "")
                if not src_name or not tgt_name:
                    continue
                dup = any(
                    (a.source.class_name == src_name and a.target.class_name == tgt_name
                     and a.association_type in ("composition", "aggregation"))
                    for a in associations
                )
                if dup:
                    continue
                src_mult = comp.get("source_multiplicity", {})
                tgt_mult = comp.get("target_multiplicity", {})
                associations.append(Association(
                    id=str(uuid.uuid4()),
                    name=comp.get("name") or f"{src_name}_contains_{tgt_name}",
                    label=comp.get("label", ""),
                    description=(comp.get("description") or "") + " [Phase1.5补边]",
                    source=AssociationEnd(
                        class_ref=class_id_map.get(src_name, src_name),
                        class_name=src_name,
                        role_name=comp.get("source_role"),
                        multiplicity=Multiplicity(lower=src_mult.get("lower", 1), upper=src_mult.get("upper", 1)),
                    ),
                    target=AssociationEnd(
                        class_ref=class_id_map.get(tgt_name, tgt_name),
                        class_name=tgt_name,
                        role_name=comp.get("target_role"),
                        multiplicity=Multiplicity(lower=tgt_mult.get("lower", 0), upper=tgt_mult.get("upper", -1)),
                    ),
                    association_type=comp.get("association_type", "composition"),
                ))
        except Exception as e:
            confidence_notes.append(f"Phase 1.5 补边失败 (已忽略): {str(e)[:150]}")

        # Emit partial: complete classes + enumerations + associations
        await _emit_partial(mof_cls_list=mof_classes, enum_list=enumerations, assocs_list=associations)

        package = Package(
            id=str(uuid.uuid4()), name="M1Package", label="M1模型",
            classes=mof_classes, enumerations=enumerations, associations=associations,
        )

        # V3.1: flag classes that look like M0 instances (name patterns, suspected_m0
        # tag from LLM self-assessment). Review panel will default-exclude them.
        pkg_dump = package.model_dump()
        suspected_m0_ids = detect_m0_instances(pkg_dump.get("classes", []))

        if progress_callback:
            await progress_callback("completed", 1.0, "M1模型提取完成！")

        return {
            "package": pkg_dump,
            "classes_found": len(mof_classes),
            "attributes_found": total_attrs,
            "associations_found": len(associations),
            "enumerations_found": len(enumerations),
            "confidence_notes": confidence_notes,
            "suspected_m0_class_ids": suspected_m0_ids,    # V3.1: for review UI
        }

    # ==================================================================
    # Pipeline B: M1 → M2
    # ==================================================================

    async def derive_m2(
        self,
        m1_package: dict,
        progress_callback: Optional[Callable] = None,
        conversation_callback: Optional[Callable] = None,
        check_cancelled: Optional[Callable] = None,
        partial_result_callback: Optional[Callable] = None,
        pause_waiter: Optional[Callable] = None,
        parallel_callback: Optional[Callable] = None,
    ) -> dict:
        """
        Derive a generalized M2 meta-model from an M1 model using a 3-phase approach:
          Phase 1 — Business-observation clustering: group M1 classes by what a business
                   analyst would query together (NOT by name/attribute similarity).
          Phase 2 — Per-group synthesis: for each group, produce EXACTLY ONE M2 class
                   (flat, single-layer). Shared attributes bubble up; differences stay in M1.
          Phase 3 — Cross-group consolidation: merge semantically-duplicate M2 classes
                   (still flat, no hierarchy).

        The old single-call approach collapsed to Entity/Object because a single LLM call
        cannot simultaneously cluster + name + structure 365 classes. This decomposes the
        problem so each LLM call sees a bounded, coherent context.
        """
        # ---- Monkey-patch _ask to capture conversations + honor pause ----
        _orig_ask = self._ask
        _call_count = [0]

        async def _ask_with_conv(p: str, max_tokens: int = None) -> dict:
            if pause_waiter:
                await pause_waiter()
            if max_tokens is None:
                max_tokens = self.DEFAULT_MAX_TOKENS
            _call_count[0] += 1
            n = _call_count[0]
            if conversation_callback:
                first_line = p.strip().split('\n')[0][:120]
                full_prompt = f"[SYSTEM]\n{SYSTEM_PROMPT}\n\n[USER]\n{p}"
                await conversation_callback("prompt", f"[#{n}] {first_line}...",
                                              f"发送 {len(p):,} 字符", full_prompt)
                await conversation_callback("waiting", f"[#{n}] 等待AI响应中...", "")
            raw_text = await self.llm.chat(
                system=SYSTEM_PROMPT, user=p,
                temperature=0, max_tokens=max_tokens,
                timeout_override=self.EXTRACTION_TIMEOUT,
            )
            if conversation_callback:
                preview = raw_text[:150].replace('\n', ' ')
                await conversation_callback("response", f"[#{n}] {preview}...",
                                              f"收到 {len(raw_text):,} 字符", raw_text)
            return _extract_json(raw_text)

        self._ask = _ask_with_conv

        def _check():
            if check_cancelled and check_cancelled():
                raise RuntimeError("用户中止推导")

        try:
            m1_classes = m1_package.get("classes", []) or []
            if not m1_classes:
                raise RuntimeError("M1 模型没有类, 无法推导 M2")

            # ================================================================
            # Phase 1 — Business-observation clustering
            # ================================================================
            _check()
            if progress_callback:
                await progress_callback("clustering_m1", 0.05,
                    f"Phase 1/3: 按业务观测维度对 {len(m1_classes)} 个 M1 类分组...")

            groups = await self._cluster_m1_for_m2(m1_package, parallel_callback)

            if not groups:
                raise RuntimeError("Phase 1 未能生成任何分组")

            if progress_callback:
                await progress_callback("clustering_m1", 0.18,
                    f"Phase 1 完成: {len(groups)} 个业务组")

            # ================================================================
            # Phase 2 — Per-group synthesis (parallel, 3 concurrent)
            # ================================================================
            _check()
            if progress_callback:
                await progress_callback("synthesizing_m2", 0.20,
                    f"Phase 2/3: 为 {len(groups)} 个业务组抽象 M2 基类 (并行)...")

            m2_classes_raw, m1_mappings_raw, group_notes = await self._synthesize_m2_groups(
                groups, m1_package,
                progress_callback, parallel_callback, pause_waiter, _check,
                partial_result_callback,
            )

            if not m2_classes_raw:
                raise RuntimeError("Phase 2 未能生成任何 M2 基类")

            if progress_callback:
                await progress_callback("synthesizing_m2", 0.68,
                    f"Phase 2 完成: 初步合成 {len(m2_classes_raw)} 个 M2 基类")

            # ================================================================
            # Phase 2.5 — Hierarchy detection per M2 base class (parallel)
            # ================================================================
            _check()
            if progress_callback:
                await progress_callback("detecting_hierarchy", 0.70,
                    f"Phase 2.5/4: 探测 {len(m2_classes_raw)} 个 M2 基类的层级结构...")

            m2_classes_raw, hierarchy_notes = await self._detect_hierarchy_for_m2_classes(
                m2_classes_raw, m1_mappings_raw, m1_package,
                progress_callback, parallel_callback, pause_waiter, _check,
            )

            _hierarchy_count = sum(
                1 for c in m2_classes_raw
                if c.get("_hierarchy", {}).get("has_hierarchy")
            )
            if progress_callback:
                await progress_callback("detecting_hierarchy", 0.82,
                    f"Phase 2.5 完成: {_hierarchy_count} 个 M2 基类具有层级结构")

            # ================================================================
            # Phase 3 — Cross-group consolidation (merge duplicates)
            # ================================================================
            _check()
            if progress_callback:
                await progress_callback("consolidating_m2", 0.84,
                    "Phase 3/4: 检查 M2 基类是否有语义重复...")

            m2_classes_raw, m1_mappings_raw, consolidation_notes = await self._consolidate_m2(
                m2_classes_raw, m1_mappings_raw,
            )

            if progress_callback:
                await progress_callback("consolidating_m2", 0.92,
                    f"Phase 3 完成: 最终 {len(m2_classes_raw)} 个 M2 基类")

        finally:
            self._ask = _orig_ask

        # ================================================================
        # Build final M2 Package (V3.0: 元类 vs 元结构 dual representation)
        # ================================================================
        # Per methodology V3.0, M2 now has two output shapes per theme:
        #
        #   (A) 元类 FlatClass (hierarchy NOT detected):
        #       → Keep one flat MOFClass with the shared attributes
        #
        #   (B) 元结构 StructuralClass (hierarchy detected):
        #       → Split into N MetaClasses (one per level), each with its own
        #         per-level attribute subset (carved from the shared attrs)
        #       → Create N-1 ordered hierarchy Associations (source=上层, target=下层)
        #       → Register a StructuralPattern on the Package metadata with
        #         root_class_id + participating_class_ids + constraints
        #
        # m1_class_mappings is extended to carry the specific target MetaClass
        # name for each M1 (based on level assignment). save-m2 uses this to
        # backfill each M1 class's parent_class_name correctly.

        m2_classes: list[MOFClass] = []
        m2_assocs: list[Association] = []
        m2_enumerations: list[Enumeration] = []
        m2_structural_patterns: list[StructuralPattern] = []

        # For m1_class_mappings redirection: original theme name → target M2 class name per M1
        # (for flat: all M1 point to the single M2 class. for structural: each M1 → its level's class)
        theme_to_target_by_m1: dict[tuple[str, str], str] = {}   # (theme_name, m1_name) → target_m2_class_name

        for c_raw in m2_classes_raw:
            theme_name = c_raw.get("name", "")
            theme_label = c_raw.get("label", theme_name)
            theme_desc = c_raw.get("description", "") or ""
            h = c_raw.get("_hierarchy") or {}

            # ---------- Flat (元类) — unchanged from pre-V3.0 ----------
            if not h.get("has_hierarchy"):
                attrs = _build_attributes_from_raw(c_raw.get("attributes", []) or [])
                flat_cls = MOFClass(
                    id=str(uuid.uuid4()),
                    name=theme_name,
                    label=theme_label,
                    description=theme_desc or None,
                    is_abstract=True,
                    attributes=attrs,
                )
                m2_classes.append(flat_cls)
                # All M1s of this theme → this flat class (unchanged mapping)
                for m in m1_mappings_raw:
                    if m.get("m2_parent_name") == theme_name:
                        theme_to_target_by_m1[(theme_name, m.get("m1_class_name", ""))] = theme_name
                continue

            # ---------- Structural (元结构) — V3.0 multi-class pattern ----------
            levels_def = h.get("levels") or []
            if len(levels_def) < 2:
                # Defensive: if hierarchy was declared but levels missing, degrade to flat
                attrs = _build_attributes_from_raw(c_raw.get("attributes", []) or [])
                fallback_cls = MOFClass(
                    id=str(uuid.uuid4()),
                    name=theme_name, label=theme_label,
                    description=theme_desc or None, is_abstract=True, attributes=attrs,
                )
                m2_classes.append(fallback_cls)
                for m in m1_mappings_raw:
                    if m.get("m2_parent_name") == theme_name:
                        theme_to_target_by_m1[(theme_name, m.get("m1_class_name", ""))] = theme_name
                continue

            # Allocate new StructuralPattern id
            pattern_id = str(uuid.uuid4())

            # Build one MOFClass per level, with that level's carved attribute subset
            level_class_ids: list[str] = []   # ordered root → leaf
            level_name_to_class_id: dict[str, str] = {}
            level_name_to_class_name: dict[str, str] = {}
            for idx, lvl_def in enumerate(levels_def):
                lvl_name = lvl_def.get("level_name", "")
                mc_name = lvl_def.get("class_name", "")
                mc_label = lvl_def.get("class_label", lvl_name)
                mc_desc = lvl_def.get("description", "") or None
                role = "root" if idx == 0 else ("leaf" if idx == len(levels_def) - 1 else "intermediate")

                # Build per-level attributes — strip server-side to valid data types
                attrs = _build_attributes_from_raw(lvl_def.get("attributes", []) or [])

                mc_id = str(uuid.uuid4())
                level_class_ids.append(mc_id)
                level_name_to_class_id[lvl_name] = mc_id
                level_name_to_class_name[lvl_name] = mc_name

                m2_classes.append(MOFClass(
                    id=mc_id,
                    name=mc_name,
                    label=mc_label,
                    description=mc_desc,
                    is_abstract=True,
                    attributes=attrs,
                    meta_structure_id=pattern_id,
                    meta_structure_role=role,
                    meta_structure_level=idx + 1,  # 1-indexed
                ))

            # Build the N-1 ordered hierarchy Associations
            hierarchy_assoc_ids: list[str] = []
            for i, assoc_def in enumerate(h.get("hierarchy_associations") or []):
                src_lvl = assoc_def.get("source_level", "")
                tgt_lvl = assoc_def.get("target_level", "")
                src_id = level_name_to_class_id.get(src_lvl)
                tgt_id = level_name_to_class_id.get(tgt_lvl)
                src_name = level_name_to_class_name.get(src_lvl, src_lvl)
                tgt_name = level_name_to_class_name.get(tgt_lvl, tgt_lvl)
                if not src_id or not tgt_id:
                    continue
                tgt_mult = assoc_def.get("target_multiplicity") or {"lower": 0, "upper": -1}
                a_type = assoc_def.get("association_type") or "aggregation"
                if a_type not in ("association", "aggregation", "composition"):
                    a_type = "aggregation"
                assoc_id = str(uuid.uuid4())
                hierarchy_assoc_ids.append(assoc_id)
                m2_assocs.append(Association(
                    id=assoc_id,
                    name=assoc_def.get("name", f"contains{tgt_name}"),
                    label=assoc_def.get("label", f"包含{tgt_lvl}"),
                    description=assoc_def.get("description") or None,
                    source=AssociationEnd(
                        class_ref=src_id, class_name=src_name,
                        multiplicity=Multiplicity(lower=1, upper=1),
                    ),
                    target=AssociationEnd(
                        class_ref=tgt_id, class_name=tgt_name,
                        multiplicity=Multiplicity(
                            lower=tgt_mult.get("lower", 0),
                            upper=tgt_mult.get("upper", -1),
                        ),
                    ),
                    association_type=a_type,
                    is_hierarchy=True,
                    hierarchy_order=i + 1,
                ))

            # Register the StructuralPattern metadata
            root_lvl_name = h.get("root_level_name") or levels_def[0].get("level_name", "")
            root_class_id = level_name_to_class_id.get(root_lvl_name) or level_class_ids[0]
            m2_structural_patterns.append(StructuralPattern(
                id=pattern_id,
                name=theme_name,
                label=theme_label,
                description=theme_desc or None,
                participating_class_ids=level_class_ids,
                hierarchy_association_ids=hierarchy_assoc_ids,
                root_class_id=root_class_id,
                level_names=[L.get("level_name", "") for L in levels_def],
                constraints=["no_cycle", "no_cross_level", "no_reverse", "root_fixed"],
                recommended_assoc_type="aggregation",
            ))

            # Redirect each M1 (originally mapped to theme_name) to its specific level's class
            for assignment in (h.get("m1_level_assignments") or []):
                m1n = assignment.get("m1_class_name", "")
                lvl = assignment.get("level", "")
                target_cname = level_name_to_class_name.get(lvl)
                if m1n and target_cname:
                    theme_to_target_by_m1[(theme_name, m1n)] = target_cname

        # ---- Redirect m1_class_mappings so each M1 points to its specific target class ----
        m1_mappings_final: list[dict] = []
        for m in m1_mappings_raw:
            theme = m.get("m2_parent_name", "")
            m1n = m.get("m1_class_name", "")
            target = theme_to_target_by_m1.get((theme, m1n), theme)
            entry = {
                "m1_class_name": m1n,
                "m2_parent_name": target,
                "m2_theme_name": theme,  # remember which structural theme for back-navigation
            }
            m1_mappings_final.append(entry)

        m2_package = Package(
            id=str(uuid.uuid4()),
            name="M2MetaModelPackage",
            label="M2元模型",
            classes=m2_classes,
            enumerations=m2_enumerations,
            associations=m2_assocs,
            structural_patterns=m2_structural_patterns,
            publish_status="draft",  # can transition to published via explicit API
        )

        # Emit partial for UI
        if partial_result_callback:
            try:
                await partial_result_callback(m2_package.model_dump())
            except Exception:
                pass

        if progress_callback:
            hierarchy_n = sum(
                1 for c in m2_classes_raw if c.get("_hierarchy", {}).get("has_hierarchy")
            )
            await progress_callback("completed", 1.0,
                f"M2推导完成: {len(m2_classes)} 个基类 ({hierarchy_n} 个带层级), "
                f"{len(m1_mappings_final)} 条继承映射")

        return {
            "m2_package": m2_package.model_dump(),
            "m1_class_mappings": m1_mappings_final,
            "confidence_notes": group_notes + hierarchy_notes + consolidation_notes,
        }

    # ==================================================================
    # M2 derivation — Phase 1: Business-observation clustering
    # ==================================================================

    async def _cluster_m1_for_m2(
        self,
        m1_package: dict,
        parallel_callback: Optional[Callable] = None,
    ) -> list[dict]:
        """Group M1 classes by business observation dimension.

        The anchor is "what business question would make an analyst look at these
        together for aggregation/analysis" — NOT name similarity or attribute overlap.
        Each M1 class ends up in exactly one group. Isolated classes get their own group.
        """
        if parallel_callback:
            await parallel_callback("m2_phase1", "Phase 1: 业务聚类", "running")

        # Build SKELETON context (no attribute details) so 365 classes fit in one call
        skeleton = []
        for c in m1_package.get("classes", []) or []:
            desc = (c.get("description") or "").strip()
            if len(desc) > 120:
                desc = desc[:117] + "..."
            skeleton.append({
                "name": c.get("name", ""),
                "label": c.get("label", ""),
                "description": desc,
                "attrs": [a.get("name", "") for a in (c.get("attributes") or [])],
            })
        skeleton_json = json.dumps(skeleton, ensure_ascii=False)

        prompt = f"""你是 MOF 元建模专家。把下面的 M1 领域类按【业务观测维度】分组, 为后续 M2 元模型抽象做准备。

⚠️ 分组锚点 (核心!):
不是按"命名相似"或"属性重叠"分组, 而是锚在「业务分析会不会把这些对象放一起查看/汇总/决策」这个问题上。

正面例子:
- PumpedStorageEquipmentLedger (抽水蓄能设备台账) +
  ElectrochemicalStorageEquipmentLedger (电化学储能设备台账) +
  ConventionalHydroEquipmentLedger (常规水电设备台账)
  → 组 "设备台账"
  理由: 电站资产分析需要跨类型汇总全部设备台账

- ReviewMeetingMaterial (审查会会务资料) +
  ReportMeetingMaterial (专题报告会议资料) +
  ApprovalMeetingMaterial (核准会会务资料)
  → 组 "会务资料"
  理由: 会务管理需要跨类型查全部会议产出

反例 (不要这样做):
❌ 把 "设备台账" 和 "项目计划" 合在一起 (都有编号/名称/状态), 业务上不会把它们一起查
❌ 把 "水轮机台账" 和 "大坝台账" 分到不同组 (两者都是设备, 跨类型设备分析是同一个业务需求)

硬约束:
1. 每个 M1 类必须且仅能进一个组
2. 孤立类独立成组 (1 个成员也合法)
3. 禁用 Entity/Object/Item/Thing/Element/Data 做组主题名
4. 组主题名必须反映业务场景 (台账/资料/报告/计划/意见/审批...)
5. 为了降低组数而强行合并无关业务类, 是不允许的
6. 不要在本阶段做任何属性抽象, 只负责分组

返回严格的 JSON (不要有其他文字/markdown):
{{
  "groups": [
    {{
      "theme": "设备台账",
      "rationale": "跨电站类型汇总设备资产参数",
      "m1_classes": ["PumpedStorageEquipmentLedger", "ElectrochemicalStorageEquipmentLedger"]
    }},
    {{
      "theme": "会务资料",
      "rationale": "跨会议类型统一查询和归档",
      "m1_classes": ["ReviewMeetingMaterial", "ReportMeetingMaterial"]
    }}
  ]
}}

M1 类列表 (共 {len(skeleton)} 个, 仅骨架信息):
{skeleton_json}"""

        result = await self._ask(prompt, max_tokens=self.DEFAULT_MAX_TOKENS)
        groups = result.get("groups") or []

        # --- Validate / repair groups ---
        all_m1_names = {c.get("name", "") for c in m1_package.get("classes", []) or []}
        assigned: set[str] = set()
        valid_groups: list[dict] = []

        for g in groups:
            theme = (g.get("theme") or "").strip()
            members = [n for n in (g.get("m1_classes") or []) if n in all_m1_names]
            # Reject groups with forbidden theme names or empty members
            if not members or not theme:
                continue
            if theme.lower() in ("entity", "object", "item", "thing", "element", "data"):
                # Still accept but append a marker; we'll let orphan handling clean up
                theme = f"{theme} (原组)"
            # Dedupe members, skip duplicates across groups (first assignment wins)
            unique_members = []
            for n in members:
                if n in assigned:
                    continue
                unique_members.append(n)
                assigned.add(n)
            if unique_members:
                valid_groups.append({
                    "theme": theme,
                    "rationale": (g.get("rationale") or "").strip(),
                    "m1_classes": unique_members,
                })

        # --- Orphan M1 classes → each becomes its own group ---
        cls_by_name = {c.get("name", ""): c for c in m1_package.get("classes", []) or []}
        unassigned = all_m1_names - assigned
        for cn in sorted(unassigned):
            c = cls_by_name.get(cn)
            theme = (c.get("label") if c else "") or cn
            valid_groups.append({
                "theme": theme,
                "rationale": "孤立类, 独立成 M2 基类",
                "m1_classes": [cn],
                "_is_orphan": True,
            })

        if parallel_callback:
            await parallel_callback("m2_phase1", f"Phase 1: 业务聚类 ({len(valid_groups)} 组)", "done")

        return valid_groups

    # ==================================================================
    # M2 derivation — Phase 2: Per-group synthesis (parallel)
    # ==================================================================

    async def _synthesize_m2_groups(
        self,
        groups: list[dict],
        m1_package: dict,
        progress_callback: Optional[Callable],
        parallel_callback: Optional[Callable],
        pause_waiter: Optional[Callable],
        _check,
        partial_result_callback: Optional[Callable] = None,
    ) -> tuple[list[dict], list[dict], list[str]]:
        """For each group, synthesize exactly 1 M2 class. Run up to 3 groups in parallel.

        Returns: (m2_classes_raw, m1_mappings, notes)
        """
        # Index M1 by name for fast lookup
        m1_by_name = {c.get("name", ""): c for c in m1_package.get("classes", []) or []}
        m1_assocs = m1_package.get("associations", []) or []

        sem = asyncio.Semaphore(3)
        lock = asyncio.Lock()
        all_m2_classes: list[dict] = []
        all_mappings: list[dict] = []
        all_notes: list[str] = []
        completed = 0
        total = len(groups)

        async def run_one(idx, g):
            nonlocal completed
            _check()
            subtask_id = f"m2_group_{idx}"
            theme = g.get("theme", f"组{idx+1}")
            subtask_name = f"组{idx+1}: {theme}"

            if parallel_callback:
                await parallel_callback(subtask_id, subtask_name, "queued")

            async with sem:
                if pause_waiter:
                    await pause_waiter()
                if parallel_callback:
                    await parallel_callback(subtask_id, subtask_name, "running")

                try:
                    m2_cls, mappings, notes = await self._synthesize_one_m2_group(
                        g, m1_by_name, m1_assocs,
                    )
                except Exception as e:
                    if parallel_callback:
                        await parallel_callback(subtask_id, subtask_name, "error")
                    async with lock:
                        all_notes.append(f"组 '{theme}' 抽象失败: {str(e)[:200]}")
                        completed += 1
                        local_done = completed
                    if progress_callback:
                        await progress_callback("synthesizing_m2",
                            0.20 + 0.60 * (local_done / max(total, 1)),
                            f"Phase 2: {local_done}/{total} 组完成 (部分失败)")
                    return

                async with lock:
                    if m2_cls:
                        all_m2_classes.append(m2_cls)
                    all_mappings.extend(mappings or [])
                    all_notes.extend(notes or [])
                    completed += 1
                    local_done = completed
                    local_m2_count = len(all_m2_classes)

                if parallel_callback:
                    await parallel_callback(subtask_id, subtask_name, "done")
                if progress_callback:
                    await progress_callback("synthesizing_m2",
                        0.20 + 0.60 * (local_done / max(total, 1)),
                        f"Phase 2: {local_done}/{total} 组完成, 累计 {local_m2_count} 个 M2 基类")

                # Live partial emission — build lightweight package for UI stream
                if partial_result_callback:
                    try:
                        partial_cls = []
                        for c_raw in all_m2_classes:
                            attrs = []
                            for a in (c_raw.get("attributes") or []):
                                dt = a.get("data_type", "String")
                                try:
                                    PrimitiveDataType(dt)
                                except ValueError:
                                    dt = "String"
                                mult = a.get("multiplicity", {}) or {}
                                attrs.append(Attribute(
                                    id=str(uuid.uuid4()),
                                    name=a.get("name", ""),
                                    label=a.get("label", ""),
                                    data_type=dt,
                                    unit=a.get("unit"),
                                    multiplicity=Multiplicity(
                                        lower=mult.get("lower", 1),
                                        upper=mult.get("upper", 1),
                                    ),
                                ))
                            partial_cls.append(MOFClass(
                                id=str(uuid.uuid4()),
                                name=c_raw.get("name", ""),
                                label=c_raw.get("label", ""),
                                description=c_raw.get("description"),
                                is_abstract=True,
                                attributes=attrs,
                            ))
                        partial_pkg = Package(
                            id=str(uuid.uuid4()),
                            name="M2MetaModelPackage_Partial",
                            label="M2元模型(合成中)",
                            classes=partial_cls,
                            enumerations=[],
                            associations=[],
                        )
                        await partial_result_callback(partial_pkg.model_dump())
                    except Exception:
                        pass

        await asyncio.gather(
            *[run_one(i, g) for i, g in enumerate(groups)],
            return_exceptions=True,
        )

        return all_m2_classes, all_mappings, all_notes

    async def _synthesize_one_m2_group(
        self,
        group: dict,
        m1_by_name: dict,
        m1_assocs: list,
    ) -> tuple[Optional[dict], list[dict], list[str]]:
        """Call LLM for ONE business group → 1 M2 class + mappings."""
        theme = group.get("theme", "")
        rationale = group.get("rationale", "")
        member_names = group.get("m1_classes", []) or []
        is_orphan = bool(group.get("_is_orphan"))

        # Gather full details of group members
        group_classes = [m1_by_name[n] for n in member_names if n in m1_by_name]
        if not group_classes:
            return None, [], [f"组 '{theme}' 无有效成员"]

        # Gather M1 associations touching any member (helps AI infer M2 self-associations)
        member_set = set(member_names)
        related_assocs = []
        for a in m1_assocs:
            src = (a.get("source") or {}).get("class_name", "")
            tgt = (a.get("target") or {}).get("class_name", "")
            if src in member_set and tgt in member_set:
                related_assocs.append({
                    "name": a.get("name", ""),
                    "label": a.get("label", ""),
                    "source": src,
                    "target": tgt,
                    "association_type": a.get("association_type", "association"),
                    "target_multiplicity": (a.get("target") or {}).get("multiplicity"),
                })

        # For single-member (orphan) groups, the M2 is basically a copy with bubble-up attributes
        # but we still let AI name/describe it properly.
        group_detail = {
            "classes": [
                {
                    "name": c.get("name", ""),
                    "label": c.get("label", ""),
                    "description": c.get("description", "") or "",
                    "attributes": [
                        {
                            "name": a.get("name", ""),
                            "label": a.get("label", ""),
                            "data_type": a.get("data_type", "String"),
                            "unit": a.get("unit"),
                        }
                        for a in (c.get("attributes") or []) if not a.get("is_inherited")
                    ],
                }
                for c in group_classes
            ],
            "intra_group_associations": related_assocs,
        }
        group_detail_json = json.dumps(group_detail, ensure_ascii=False)

        orphan_hint = ""
        if is_orphan:
            orphan_hint = (
                "\n注意: 本组只有 1 个 M1 成员 (孤立类)。直接将其核心属性提升为 M2 基类属性; "
                "M2 的名字应该体现其业务语义, 不是简单照抄 M1 类名。\n"
            )

        prompt = f"""你是 MOF 元建模专家。为下面这组"{theme}"业务组抽象出恰好 1 个 M2 元类。

⚠️ 硬性规则:
1. 恰好产出 1 个 M2 元类 (不多不少, 不允许为本组生成多个基类或分层结构)
2. 只保留业务分析关心的共性 (见下面"抽象思路" 第 1-2 点)
3. 差异属性必须留在 M1 中, 不要强行提升到 M2
4. M2 的自关联必须指向自身 (M2 元类), 不能指向 M1 子类 — 这样才能保持混合子类的业务树 (如"引水系统"下可同时挂机电设备和建筑设备)
5. 元类名必须反映业务主题, 禁用 Entity/Object/Item/Thing/Element/Data 等空泛词
6. 所有 {len(member_names)} 个组内 M1 类都必须映射为这个 M2 的子类 (parent_class_name = M2.name)
7. 一个 M2 元类至少应该有 3 个属性 (id + name + 至少 1 个业务属性); 实在找不到共性时, 至少保留 id/name/code/description/status 这类通用骨架
{orphan_hint}
抽象思路:
1. **按语义共性而非字面同名**: M1 类的属性命名可能分歧 (如 ratedVoltage / nominalVoltage / rated_u 其实都是"额定电压"), 识别出语义相同的一簇, 用最清晰的名字放入 M2 (label 用中文标签以保语义)
2. **阈值放宽**: 通常需要 ≥ 50% 组内 M1 类语义上拥有 (不是字面同名); 对于基础字段 (id/code/name/description/status/createTime/updateTime 等), 只要 ≥ 30% 就可上升
3. **数据类型泛化**: 同一属性在不同 M1 里如果类型不同 (Float/Integer/String), M2 向更宽类型靠 (Float 覆盖 Integer; String 最宽)
4. **单位处理**: 同一属性单位不一致 (MW/kW) 选最常用的, 允许 M1 覆盖
5. **识别 M1 之间的自关联**: 如 "包含子设备/引用/组合" 等模式, 提升为 M2 自关联 (source 和 target 都是 M2 自身, 不是子类)
6. M2 的 description 必须说明【这组 M1 共同服务的业务观测场景】(如"跨类型汇总设备资产")

⚠️ 如果组内 M1 类属性完全不相交, 说明 Phase 1 聚类有误 —— 这种情况下仍然产出 1 个 M2,
   description 里注明"属性共性弱, 主要作为业务观测维度的分类契约", attributes 至少给出
   通用骨架 (id / name / code / description / status)。不要返回空属性列表。

返回严格的 JSON (不要 markdown 或其他文字):
{{
  "m2_class": {{
    "name": "EquipmentLedger",
    "label": "设备台账",
    "description": "所有类型电站设备的静态参数台账, 支持跨类型资产分析",
    "is_abstract": true,
    "attributes": [
      {{"name": "ledgerId", "label": "台账编号", "data_type": "String", "multiplicity": {{"lower": 1, "upper": 1}}}}
    ],
    "self_associations": [
      {{"name": "containsSubEquipment", "label": "包含子设备",
        "source_multiplicity": {{"lower": 1, "upper": 1}},
        "target_multiplicity": {{"lower": 0, "upper": -1}},
        "association_type": "composition"}}
    ]
  }},
  "m1_mappings": [
    {{"m1_class_name": "PumpedStorageEquipmentLedger", "m2_parent_name": "EquipmentLedger"}}
  ],
  "notes": []
}}

本组信息:
- 主题 (theme): {theme}
- 分组理由 (rationale): {rationale}
- M1 成员数量: {len(member_names)}

本组 M1 类详情 (含属性名/类型/单位、组内关联):
{group_detail_json}"""

        try:
            result = await self._ask(prompt, max_tokens=self.DEFAULT_MAX_TOKENS)
        except Exception as e:
            return None, [], [f"组 '{theme}' LLM 调用失败: {str(e)[:200]}"]

        m2_cls = result.get("m2_class")
        mappings = result.get("m1_mappings") or []
        notes = result.get("notes") or []

        # --- Validate / normalize the returned M2 class ---
        if not m2_cls or not m2_cls.get("name"):
            return None, [], [f"组 '{theme}' 未返回有效的 M2 类"]

        name = m2_cls["name"].strip()
        # Reject forbidden names; fall back to theme-based name
        if name.lower() in ("entity", "object", "item", "thing", "element", "data"):
            # Convert theme to PascalCase
            fallback = "".join(w.capitalize() for w in theme.replace("_", " ").split() if w)
            if not fallback:
                fallback = f"Group{hash(theme) & 0xFFFF:04X}"
            m2_cls["name"] = fallback + "MetaClass"
            notes.append(f"组 '{theme}' 名称被改为 '{m2_cls['name']}' (原名为禁用词)")

        m2_cls.setdefault("label", theme)
        m2_cls.setdefault("description", rationale or f"{theme}的 M2 元类")
        m2_cls["is_abstract"] = True

        # Safety net: if AI returned empty/missing attributes list, inject a universal
        # skeleton so the M2 class isn't completely hollow. The prompt asks for this,
        # but some models still return [] for heterogeneous clusters.
        attrs = m2_cls.get("attributes") or []
        if not attrs:
            attrs = [
                {"name": "id", "label": "编号",
                 "data_type": "String", "multiplicity": {"lower": 1, "upper": 1},
                 "description": "唯一标识 (自动兜底属性 — 原聚类共性较弱)"},
                {"name": "name", "label": "名称",
                 "data_type": "String", "multiplicity": {"lower": 1, "upper": 1},
                 "description": "业务名称"},
                {"name": "description", "label": "描述",
                 "data_type": "String", "multiplicity": {"lower": 0, "upper": 1}},
            ]
            m2_cls["attributes"] = attrs
            notes.append(
                f"组 '{theme}': AI 返回空属性列表, 已注入通用骨架 (id/name/description)"
                " — 建议人工审查该聚类是否过于松散"
            )

        # Ensure all group members are mapped (AI sometimes forgets)
        mapped_names = {m.get("m1_class_name") for m in mappings if m.get("m1_class_name")}
        for n in member_names:
            if n not in mapped_names:
                mappings.append({
                    "m1_class_name": n,
                    "m2_parent_name": m2_cls["name"],
                })

        # Normalize mapping parent names: any mapping whose parent is missing, empty,
        # or points to a name *different* from the single M2 class we produced → force
        # it back to this class's name (AI sometimes hallucinates parent names).
        for m in mappings:
            p = m.get("m2_parent_name")
            if not p or p != m2_cls["name"]:
                m["m2_parent_name"] = m2_cls["name"]

        return m2_cls, mappings, notes

    # ==================================================================
    # M2 derivation — Phase 2.5: Hierarchy detection per base class
    # ==================================================================
    # For each M2 base class, determine if its business theme has a natural
    # multi-level containment hierarchy (like 设施→功能分组→设备→部件).
    # If yes: produces ordered level names + per-M1-class level assignment.
    # Outputs are consumed by the final materialization (Part 3 of derive_m2)
    # to auto-generate a level Enum, a level attribute on the M2 base, and
    # a self-referential parent/children association.

    async def _detect_hierarchy_for_m2_classes(
        self,
        m2_classes_raw: list[dict],
        m1_mappings: list[dict],
        m1_package: dict,
        progress_callback: Optional[Callable],
        parallel_callback: Optional[Callable],
        pause_waiter: Optional[Callable],
        _check,
    ) -> tuple[list[dict], list[str]]:
        """Run hierarchy detection for each M2 base class in parallel.
        Mutates m2_classes_raw in place, adding a `_hierarchy` dict to classes
        whose theme has a hierarchy. Returns (classes, notes).
        """
        notes: list[str] = []
        if not m2_classes_raw:
            return m2_classes_raw, notes

        # Index: m2_class_name -> list of member M1 class dicts
        m1_by_name = {c.get("name", ""): c for c in m1_package.get("classes", []) or []}
        members_by_m2: dict[str, list[dict]] = {}
        for m in m1_mappings:
            parent = m.get("m2_parent_name", "")
            child_name = m.get("m1_class_name", "")
            if not parent or not child_name:
                continue
            members_by_m2.setdefault(parent, []).append(
                m1_by_name.get(child_name, {"name": child_name})
            )

        sem = asyncio.Semaphore(3)
        lock = asyncio.Lock()
        completed = 0
        total = len(m2_classes_raw)

        async def run_one(idx, m2_cls):
            nonlocal completed
            _check()
            cls_name = m2_cls.get("name", "")
            cls_label = m2_cls.get("label", cls_name)
            subtask_id = f"m2_hierarchy_{idx}"
            subtask_name = f"层级探测 {idx+1}/{total}: {cls_label}"

            if parallel_callback:
                await parallel_callback(subtask_id, subtask_name, "queued")

            async with sem:
                if pause_waiter:
                    await pause_waiter()
                if parallel_callback:
                    await parallel_callback(subtask_id, subtask_name, "running")

                members = members_by_m2.get(cls_name, [])
                try:
                    hierarchy = await self._detect_hierarchy_for_one(m2_cls, members)
                except Exception as e:
                    if parallel_callback:
                        await parallel_callback(subtask_id, subtask_name, "error")
                    async with lock:
                        notes.append(f"'{cls_label}' 层级探测失败: {str(e)[:200]}")
                        completed += 1
                        local_done = completed
                    if progress_callback:
                        await progress_callback("detecting_hierarchy",
                            0.70 + 0.10 * (local_done / max(total, 1)),
                            f"Phase 2.5: {local_done}/{total} (部分失败)")
                    return

                async with lock:
                    if hierarchy and hierarchy.get("has_hierarchy"):
                        m2_cls["_hierarchy"] = hierarchy
                    completed += 1
                    local_done = completed
                    discovered = sum(
                        1 for c in m2_classes_raw
                        if c.get("_hierarchy", {}).get("has_hierarchy")
                    )

                if parallel_callback:
                    msg = (
                        f"层级 [{len(hierarchy.get('levels', []))} 层]"
                        if hierarchy and hierarchy.get("has_hierarchy")
                        else "无层级"
                    )
                    await parallel_callback(subtask_id, f"{subtask_name} — {msg}", "done")
                if progress_callback:
                    await progress_callback("detecting_hierarchy",
                        0.70 + 0.10 * (local_done / max(total, 1)),
                        f"Phase 2.5: {local_done}/{total}, 发现 {discovered} 个带层级主题")

        await asyncio.gather(
            *[run_one(i, c) for i, c in enumerate(m2_classes_raw)],
            return_exceptions=True,
        )

        return m2_classes_raw, notes

    async def _detect_hierarchy_for_one(
        self,
        m2_cls: dict,
        member_m1_classes: list[dict],
    ) -> dict:
        """Single LLM call — design the metastructure (V3.0) for this M2 theme.

        Per methodology V3.0, a StructuralClass is NOT "one class with self-assoc"
        but a PATTERN of multiple MetaClasses + ordered hierarchy Associations.
        This method asks the LLM to either:
          a) Declare it has no hierarchy (it's a FlatClass / 元类)  —  return has_hierarchy=false
          b) Design a full metastructure: N MetaClasses (each with its OWN attribute
             subset carved from the original shared attrs) + N-1 ordered hierarchy
             Associations + root class + per-M1 level assignment.

        Returns a dict with either:
          {has_hierarchy: False, rationale: str}
          or
          {
            has_hierarchy: True,
            levels: [{level_name, class_name, class_label, description, attributes: [...]}, ...],
            hierarchy_associations: [{source_level, target_level, name, label, mult}, ...],
            m1_level_assignments: [{m1_class_name, level}, ...],
            root_level_name: str,
            rationale: str,
          }
        """
        cls_name = m2_cls.get("name", "")
        cls_label = m2_cls.get("label", cls_name)
        cls_desc = m2_cls.get("description", "") or ""

        # All shared attributes discovered in Phase 2 — these need to be
        # redistributed across the N level MetaClasses in a structural design.
        shared_attrs = m2_cls.get("attributes", []) or []
        shared_attrs_compact = [
            {
                "name": a.get("name", ""),
                "label": a.get("label", ""),
                "data_type": a.get("data_type", "String"),
                "unit": a.get("unit", ""),
            }
            for a in shared_attrs
        ]

        # Build compact member info (including hierarchy_hints from M1 extraction)
        member_info = []
        hint_samples = []
        for c in member_m1_classes:
            cname = c.get("name", "")
            info = {
                "name": cname,
                "label": c.get("label", "") or "",
                "description": (c.get("description") or "")[:80],
                "attrs": [a.get("name", "") for a in (c.get("attributes") or [])][:15],
            }
            h = c.get("hierarchy_hint")
            if h and isinstance(h, dict):
                if h.get("level_hint") or h.get("theme_hint"):
                    hint_samples.append({
                        "m1_class": cname,
                        "theme": h.get("theme_hint", ""),
                        "level": h.get("level_hint", ""),
                        "parent": h.get("parent_name_hint", ""),
                    })
            member_info.append(info)

        members_json = json.dumps(member_info, ensure_ascii=False)
        hints_json = json.dumps(hint_samples, ensure_ascii=False) if hint_samples else "[]"
        shared_attrs_json = json.dumps(shared_attrs_compact, ensure_ascii=False)

        prompt = f"""你是 MOF 元建模专家。方法论 V3.0 要求：对有层级结构的业务主题, M2 应表达为【元结构 StructuralClass】, 即多个独立的 MetaClass + 有序层级关联, 而不是一个含自关联的单类。

任务: 为 M2 主题 "{cls_label}" ({cls_name}) 做结构化设计。

业务背景:
- 主题名: {cls_name}  (label: {cls_label})
- 描述: {cls_desc[:200]}
- 包含 {len(member_m1_classes)} 个 M1 成员类
- Phase 2 暂汇总的共性属性集: {len(shared_attrs)} 个 (需在 N 个 MetaClass 间重新分配)

⚠️ 决策规则:

(1) 首先决定该主题是【元类】还是【元结构】:

  ✅ 元结构 (StructuralClass) 的判据 — 需要**纵向包含关系**:
     - [设施, 功能分组, 设备, 部件]  ← 上层实际包含下层
     - [工程项目, 任务, 子任务]      ← 上层可分解出下层
     - [法人, 部门, 岗位, 人员]      ← 组织的上下级

  ❌ 不是元结构:
     - [机电, 水工, 闸门]           ← 横向专业分类, 非包含关系
     - [抽水蓄能, 电化学, 常规水电]   ← 电站类型, 非层级
     - 会务资料 / 专家意见 / 标准报告   ← 扁平业务对象

  判据看四个线索: M1 成员名的层级语义 / M1 属性里的"上级""所属"暗示 / 行业常识 / hierarchy_hint

(2) 若判为元类 → 返回 has_hierarchy=false, rationale 说明为什么

(3) 若判为元结构 → **设计完整元结构**:
    a. 确定层级序列 (2-6 层, 从根到叶), 每层一个 MetaClass
    b. 给每个 MetaClass 取 PascalCase 英文名 + 中文 label (例: 设施=Facility)
    c. **把 Phase 2 的共性属性集按层级重新分配** — 每个属性只属于一层 MetaClass:
       - "地理坐标"属于设施级, 不属于部件级
       - "投运日期""运行状态"属于设备级
       - "规格参数""材质"属于部件级
       - 如果某属性所有层都应有 (如 id/name/code), 放到【根层级】
    d. 生成 N-1 条有序层级关联 (source=上层, target=下层, multiplicity=0..*)
    e. 标注根节点 (根层级的 class_name)
    f. 给每个 M1 成员类分配一个具体 level (不再有 whole_tree 概念, 每个 M1 必须落在具体层)

(4) **绝对禁止**:
    - 把专业类型 (机电/水工/闸门) 识别为层级
    - 超过 6 层的过度分解
    - 用 Entity/Object/Item/Thing/Element/Data 做层级名
    - 允许 M1 不归任何一层 (全树模板的场景在 V3.0 中应改为绑定到根层级)

返回严格 JSON (无 markdown):

▼ 若是元结构:
{{
  "has_hierarchy": true,
  "levels": [
    {{
      "level_name": "设施",
      "class_name": "Facility",
      "class_label": "设施",
      "description": "业务资产管理的顶层节点",
      "attributes": [
        {{"name": "facilityCode", "label": "设施编码", "data_type": "String", "multiplicity": {{"lower": 1, "upper": 1}}}},
        {{"name": "facilityName", "label": "设施名称", "data_type": "String"}},
        {{"name": "geoCoordinate", "label": "地理坐标", "data_type": "String"}}
      ]
    }},
    {{
      "level_name": "功能分组",
      "class_name": "FunctionalGroup",
      "class_label": "功能分组",
      "description": "同一功能的设备分组",
      "attributes": [
        {{"name": "groupCode", "label": "分组编码", "data_type": "String"}},
        {{"name": "groupType", "label": "分组类型", "data_type": "Enum"}}
      ]
    }},
    {{
      "level_name": "设备",
      "class_name": "Equipment",
      "class_label": "设备",
      "description": "可独立运维的设备实体",
      "attributes": [
        {{"name": "equipmentCode", "label": "设备编码", "data_type": "String"}},
        {{"name": "commissionDate", "label": "投运日期", "data_type": "Date"}},
        {{"name": "operatingStatus", "label": "运行状态", "data_type": "Enum"}}
      ]
    }},
    {{
      "level_name": "部件",
      "class_name": "Component",
      "class_label": "部件",
      "description": "设备的组成部分",
      "attributes": [
        {{"name": "componentCode", "label": "部件编码", "data_type": "String"}},
        {{"name": "specification", "label": "规格参数", "data_type": "String"}}
      ]
    }}
  ],
  "hierarchy_associations": [
    {{"source_level": "设施", "target_level": "功能分组", "name": "containsFunctionalGroup", "label": "包含功能分组", "target_multiplicity": {{"lower": 0, "upper": -1}}, "association_type": "aggregation"}},
    {{"source_level": "功能分组", "target_level": "设备", "name": "containsEquipment", "label": "包含设备", "target_multiplicity": {{"lower": 0, "upper": -1}}, "association_type": "aggregation"}},
    {{"source_level": "设备", "target_level": "部件", "name": "containsComponent", "label": "包含部件", "target_multiplicity": {{"lower": 0, "upper": -1}}, "association_type": "aggregation"}}
  ],
  "root_level_name": "设施",
  "m1_level_assignments": [
    {{"m1_class_name": "PumpedStorageStation", "level": "设施"}},
    {{"m1_class_name": "GeneratorUnitSystem", "level": "功能分组"}},
    {{"m1_class_name": "HydroTurbine", "level": "设备"}},
    {{"m1_class_name": "TurbineBlade", "level": "部件"}}
  ],
  "rationale": "该主题在业务上有标准的 电站→系统→设备→部件 分层, 属于元结构"
}}

▼ 若不是元结构 (元类):
{{
  "has_hierarchy": false,
  "rationale": "该主题是扁平业务对象, 无纵向包含关系"
}}

---

Phase 2 已汇总的共性属性集 (需要在设计中重新分配到各层级):
{shared_attrs_json}

该 M2 主题的 M1 成员 ({len(member_info)} 个, 含属性名列表):
{members_json}

M1 提取时的层级线索 (仅供参考):
{hints_json}"""

        result = await self._ask(prompt, max_tokens=self.DEFAULT_MAX_TOKENS)

        # ----- Validate / sanitize -----
        if not isinstance(result, dict):
            return {"has_hierarchy": False, "rationale": "AI 返回格式无效"}

        has_h = bool(result.get("has_hierarchy"))
        if not has_h:
            return {"has_hierarchy": False, "rationale": result.get("rationale", "")}

        raw_levels = result.get("levels") or []
        if not isinstance(raw_levels, list) or len(raw_levels) < 2:
            return {"has_hierarchy": False, "rationale": "探测到的层级不足 2 层, 视为元类"}

        # --- Clean levels: unique level_name, valid class_name, strip forbidden names ---
        forbidden = {"entity", "object", "item", "thing", "element", "data"}
        cleaned_levels: list[dict] = []
        seen_level_names: set[str] = set()
        seen_class_names: set[str] = set()
        for lvl in raw_levels[:8]:  # cap 8
            if not isinstance(lvl, dict):
                continue
            ln = (lvl.get("level_name") or "").strip()
            cn = (lvl.get("class_name") or "").strip()
            cl = (lvl.get("class_label") or ln or "").strip()
            if not ln or not cn or ln in seen_level_names or cn in seen_class_names:
                continue
            if ln.lower() in forbidden or cn.lower() in forbidden:
                continue
            # Sanitize attributes
            attrs_list = []
            for a in (lvl.get("attributes") or []):
                if not isinstance(a, dict):
                    continue
                an = (a.get("name") or "").strip()
                if not an:
                    continue
                mult = a.get("multiplicity") or {}
                attrs_list.append({
                    "name": an,
                    "label": a.get("label") or an,
                    "data_type": a.get("data_type") or "String",
                    "unit": a.get("unit") or None,
                    "enum_name": a.get("enum_name") or None,
                    "multiplicity": {
                        "lower": mult.get("lower", 1) if isinstance(mult, dict) else 1,
                        "upper": mult.get("upper", 1) if isinstance(mult, dict) else 1,
                    },
                })
            cleaned_levels.append({
                "level_name": ln,
                "class_name": cn,
                "class_label": cl,
                "description": (lvl.get("description") or "").strip(),
                "attributes": attrs_list,
            })
            seen_level_names.add(ln)
            seen_class_names.add(cn)

        if len(cleaned_levels) < 2:
            return {"has_hierarchy": False, "rationale": "清洗后层级不足 2 层, 视为元类"}

        # --- Clean hierarchy_associations: must form ordered chain (N-1 edges) ---
        raw_assocs = result.get("hierarchy_associations") or []
        level_name_order = [L["level_name"] for L in cleaned_levels]
        cleaned_assocs: list[dict] = []
        for i in range(len(level_name_order) - 1):
            src_name = level_name_order[i]
            tgt_name = level_name_order[i + 1]
            # Find matching from AI output, or synthesize
            found = None
            for a in raw_assocs:
                if not isinstance(a, dict):
                    continue
                if (a.get("source_level") == src_name) and (a.get("target_level") == tgt_name):
                    found = a
                    break
            if found:
                tgt_mult = found.get("target_multiplicity") or {"lower": 0, "upper": -1}
                cleaned_assocs.append({
                    "source_level": src_name,
                    "target_level": tgt_name,
                    "name": (found.get("name") or f"contains{tgt_name}").strip(),
                    "label": (found.get("label") or f"包含{tgt_name}").strip(),
                    "description": (found.get("description") or "").strip(),
                    "target_multiplicity": {
                        "lower": tgt_mult.get("lower", 0),
                        "upper": tgt_mult.get("upper", -1),
                    },
                    "association_type": (found.get("association_type") or "aggregation"),
                })
            else:
                # Synthesize — ensure the chain is complete even if AI missed an edge
                cleaned_assocs.append({
                    "source_level": src_name,
                    "target_level": tgt_name,
                    "name": f"contains{cleaned_levels[i+1]['class_name']}",
                    "label": f"包含{cleaned_levels[i+1]['class_label']}",
                    "description": "",
                    "target_multiplicity": {"lower": 0, "upper": -1},
                    "association_type": "aggregation",
                })

        # --- Root level: prefer AI's choice, fall back to first level ---
        root_name = (result.get("root_level_name") or "").strip()
        if root_name not in seen_level_names:
            root_name = cleaned_levels[0]["level_name"]

        # --- M1 level assignments: every M1 must map to a valid level ---
        member_names = {c.get("name", "") for c in member_m1_classes}
        raw_assignments = result.get("m1_level_assignments") or []
        assigned_lookup: dict[str, str] = {}
        for a in raw_assignments:
            if not isinstance(a, dict):
                continue
            n = (a.get("m1_class_name") or "").strip()
            L = (a.get("level") or "").strip()
            if n not in member_names:
                continue
            # Accept either legacy "whole_tree" mapped to root, or exact level name
            if L == "whole_tree":
                assigned_lookup[n] = root_name
            elif L in seen_level_names:
                assigned_lookup[n] = L
        # Any unassigned M1 → root (safest default, user can fix post-hoc)
        for n in member_names:
            if n not in assigned_lookup:
                assigned_lookup[n] = root_name

        clean_assignments = [
            {"m1_class_name": n, "level": lvl}
            for n, lvl in assigned_lookup.items()
        ]

        return {
            "has_hierarchy": True,
            "levels": cleaned_levels,
            "hierarchy_associations": cleaned_assocs,
            "root_level_name": root_name,
            "m1_level_assignments": clean_assignments,
            "rationale": (result.get("rationale") or "").strip(),
        }

    # ==================================================================
    # M2 derivation — Phase 3: Cross-group consolidation
    # ==================================================================

    async def _consolidate_m2(
        self,
        m2_classes: list[dict],
        m1_mappings: list[dict],
    ) -> tuple[list[dict], list[dict], list[str]]:
        """Check if any M2 classes are semantic duplicates and merge them. Flat output only."""
        notes: list[str] = []

        if len(m2_classes) < 2:
            return m2_classes, m1_mappings, notes

        skeleton = []
        for c in m2_classes:
            desc = (c.get("description") or "").strip()
            if len(desc) > 180:
                desc = desc[:177] + "..."
            skeleton.append({
                "name": c.get("name", ""),
                "label": c.get("label", ""),
                "description": desc,
                "attrs": [a.get("name", "") for a in (c.get("attributes") or [])],
            })
        skeleton_json = json.dumps(skeleton, ensure_ascii=False)

        prompt = f"""你是 MOF 元建模专家。下面是 {len(m2_classes)} 个已初步合成的 M2 元类。检查是否有【业务含义实质相同、只是命名略异】的, 应该合并。

⚠️ 合并原则:
1. 仅合并业务含义实质相同的 (如 "会议材料" ≈ "会务资料", "设计意见" ≈ "专家意见")
2. 绝对不做多级抽象 — 合并后仍是扁平单层
3. 业务场景不同的元类必须保持分离 (如 "会务资料" ≠ "审批文档" ≠ "设备台账")
4. 每个合并建议必须有具体业务理由
5. 如果所有 M2 元类都没有明显重复, 返回空 merges 列表 — 这是合法结果
6. 不允许跨大类合并 (例如"设备台账"和"专题报告"不能合并成"资产相关文档")

返回严格的 JSON (不要 markdown):
{{
  "merges": [
    {{
      "target_name": "MeetingMaterial",
      "target_label": "会务资料",
      "sources": ["ConferenceDoc", "MeetingDocument"],
      "rationale": "两者都指会议产生的材料, 业务上应统一查询"
    }}
  ]
}}

若无可合并, 返回: {{"merges": []}}

现有 M2 元类 (共 {len(m2_classes)} 个):
{skeleton_json}"""

        try:
            result = await self._ask(prompt, max_tokens=self.DEFAULT_MAX_TOKENS)
        except Exception as e:
            notes.append(f"Phase 3 合并检查失败 (保持原状): {str(e)[:200]}")
            return m2_classes, m1_mappings, notes

        merges = result.get("merges") or []
        if not merges:
            return m2_classes, m1_mappings, notes

        # ---- Build COMPLETE rename map (every source → target, including cases where
        # the target name is brand-new / not already one of the M2 classes).
        # IMPORTANT: do NOT pop any entries — m1_mappings remap needs all renames visible.
        rename_map: dict[str, str] = {}                # source_name → final target_name
        target_meta: dict[str, dict] = {}              # target_name → {label, rationale}
        existing_names = {c.get("name", "") for c in m2_classes}

        for m in merges:
            target_name = (m.get("target_name") or "").strip()
            sources = [s for s in (m.get("sources") or []) if s]
            if not target_name or not sources:
                continue
            target_meta[target_name] = {
                "label": m.get("target_label") or target_name,
                "rationale": m.get("rationale") or "",
            }
            for src in sources:
                if src != target_name:
                    rename_map[src] = target_name

        # ---- Group original classes by their FINAL target name ----
        final_groups: dict[str, list[dict]] = {}
        for c in m2_classes:
            orig_name = c.get("name", "")
            final_name = rename_map.get(orig_name, orig_name)
            final_groups.setdefault(final_name, []).append(c)

        # ---- Build consolidated class list, preserving input order via first appearance ----
        new_classes: list[dict] = []
        seen_final: set[str] = set()

        for c in m2_classes:
            orig_name = c.get("name", "")
            final_name = rename_map.get(orig_name, orig_name)
            if final_name in seen_final:
                continue
            seen_final.add(final_name)

            group = final_groups[final_name]

            # Case 1: single untouched class (no rename, no merge) — pass through
            if len(group) == 1 and orig_name == final_name and final_name not in target_meta:
                new_classes.append(c)
                continue

            # Case 2: merge/rename — use group[0] as base, union attrs+self_associations from rest
            base = dict(group[0])  # shallow copy; we'll replace lists with fresh copies
            base["name"] = final_name
            if final_name in target_meta:
                base["label"] = target_meta[final_name]["label"]
            base["attributes"] = list(group[0].get("attributes") or [])
            base["self_associations"] = list(group[0].get("self_associations") or [])

            existing_attr_names = {a.get("name") for a in base["attributes"]}
            existing_assoc_names = {sa.get("name") for sa in base["self_associations"]}

            for other in group[1:]:
                for a in (other.get("attributes") or []):
                    if a.get("name") not in existing_attr_names:
                        base["attributes"].append(a)
                        existing_attr_names.add(a.get("name"))
                for sa in (other.get("self_associations") or []):
                    if sa.get("name") not in existing_assoc_names:
                        base["self_associations"].append(sa)
                        existing_assoc_names.add(sa.get("name"))

            if len(group) > 1:
                src_list = [x.get("name", "") for x in group]
                rationale = target_meta.get(final_name, {}).get("rationale", "")
                notes.append(
                    f"合并 {src_list} → {final_name}"
                    + (f" ({rationale[:80]})" if rationale else "")
                )
            elif orig_name != final_name:
                notes.append(f"重命名 {orig_name} → {final_name}")

            new_classes.append(base)

        # ---- Remap m1_mappings using the COMPLETE rename_map ----
        # (This is the critical fix: previously `rename_map.pop(base_src, None)`
        # removed the renamed base's entry, orphaning any m1_mapping still pointing
        # to its original name.)
        remapped_count = 0
        for m in m1_mappings:
            parent = m.get("m2_parent_name", "")
            if parent in rename_map:
                m["m2_parent_name"] = rename_map[parent]
                remapped_count += 1
        if remapped_count:
            notes.append(f"修正了 {remapped_count} 条 M1→M2 映射的父类引用")

        return new_classes, m1_mappings, notes

    # ==================================================================
    # Refinement
    # ==================================================================

    async def refine(self, current_package: dict, user_message: str, layer: str = "M1") -> dict:
        """Refine M1 or M2 model based on user feedback."""
        prompt = f"""The user wants to refine the {layer} model. Current model state:
{json.dumps(current_package, ensure_ascii=False, indent=2)}

User's request: "{user_message}"

Apply the user's requested changes. Return the complete updated model as valid JSON with the same structure.
Only modify what the user asked for. Keep everything else unchanged."""

        return await self._ask(prompt, max_tokens=16384)

    # ==================================================================
    # Internal prompt methods — Pipeline A
    # ==================================================================

    async def _extract_entities_with_attrs_from_docs(
        self,
        doc_text: str,
        known_context: dict = None,
        doc_type: str = "auto",
    ) -> dict:
        """Combined single-pass extraction: classes (with attributes) + enumerations.

        Replaces the old two-phase (discover classes → then attributes) approach,
        which required N_classes × N_doc_batches LLM calls. This does it in one
        call per doc batch — reducing total calls by ~100×.

        `known_context` schema (from previously-processed batches):
            {
              class_name: {
                "label": "中文名",
                "attrs": ["already", "known", "attribute", "names"]
              }, ...
            }
        Returns the same shape as the LLM response:
            {
              "classes": [{"name", "label", "description",
                           "attributes": [{"name","label","data_type","unit","enum_name","multiplicity"}]}],
              "enumerations": [{"name","label","literals":[{"name","label"}]}],
              "confidence_notes": [...]
            }
        """
        context_hint = ""
        if known_context:
            lines = []
            for cname, info in list(known_context.items())[:80]:  # cap context size
                attrs = info.get("attrs", []) if isinstance(info, dict) else []
                label = info.get("label", "") if isinstance(info, dict) else ""
                attr_summary = ", ".join(attrs[:12]) + ("..." if len(attrs) > 12 else "")
                lines.append(f"  - {cname}" + (f" ({label})" if label else "") +
                             (f": has attrs [{attr_summary}]" if attr_summary else ""))
            more = "" if len(known_context) <= 80 else f"\n  (+{len(known_context)-80} more classes not shown)"
            context_hint = f"""
IMPORTANT: The following classes (and some of their attributes) were ALREADY identified
from previous document batches. For THIS document batch:
- If you find one of these EXISTING classes here, include it in your response but ONLY
  with attributes NOT already listed below. We'll merge automatically.
- If you find a NEW class not in this list, include it FULLY with all its attributes.
- Use EXACTLY the same class names as shown — don't create synonyms.

Already found:
{chr(10).join(lines)}{more}
"""

        # V3.1: doc-type-aware strategy injection
        type_guidance = _doc_type_guidance(doc_type)

        prompt = f"""Analyze these business documents and extract:
1. CLASSES (entity types) WITH their attributes — in ONE pass.
2. ENUMERATIONS (closed value sets such as status, type, mode).

This is M1-level extraction — identify CONCRETE, DOMAIN-SPECIFIC types exactly as
the documents describe. Do NOT generalize.

=== 文档类型 ({doc_type}) 特定策略 ===
{type_guidance}

=== M0 实例 vs M1 类型的严格区分 ===
绝对不要把下列对象作为 Class 抽取 (它们是 M0 实例):
- 含编号/型号代码的具体设备 (如 "HYB-400 水轮机", "机组 1#", "PLC-002" )
- 含具体地名/单位名的实体 (如 "惠州抽水蓄能电站", "广州局 A 厂房")
- 含年份/日期的项目或记录 (如 "2024 年度检修计划", "2023-06 月报")
- 具体人名/组织名 (如 "张三", "设计一所")

应抽取为 Class 的是**类型/概念**本身 (如 "抽水蓄能电站" 是类, "惠州抽水蓄能电站" 是实例)。
如某名称看起来像一个具体对象而非类型,在 "description" 里备注 "suspected_m0=true"。
{context_hint}
For each Class:
- "name": English PascalCase, e.g. "PumpedStorageUnit"
- "label": Chinese display name
- "description": one-sentence Chinese description
- "attributes": list of this class's attributes found in this doc:
  - "name": English camelCase
  - "label": Chinese display name
  - "data_type": one of String | Integer | Float | Boolean | Date | Enum
  - "unit": measurement unit if applicable (MW, m, rpm, kV, A, mm, MPa, etc.)
  - "enum_name": if data_type=Enum, the name of the enumeration it refers to
  - "multiplicity": {{"lower": N, "upper": M}}, use -1 for unlimited (default {{1, 1}})
  - "description": brief Chinese description (optional)
- "hierarchy_hint" (OPTIONAL, only include if the document CLEARLY suggests this):
  - "theme_hint": the broader business family this class belongs to (e.g. 设备台账, 项目计划, 组织机构, 文档资料)
  - "level_hint": this class's role in a containment hierarchy
    (e.g. 设施, 功能分组, 设备, 部件  /  工程项目, 任务, 子任务  /  单位, 部门, 岗位)
  - "parent_name_hint": if the document explicitly says this class belongs under another class
  If uncertain, OMIT this field entirely — don't guess.

For each Enumeration:
- "name": English PascalCase
- "label": Chinese name
- "literals": array of {{"name": "english", "label": "中文"}}

Return valid JSON ONLY (no markdown, no commentary):
{{
  "classes": [
    {{
      "name": "PumpedStorageUnit",
      "label": "抽水蓄能机组",
      "description": "可发电也可抽水蓄能的水电机组",
      "attributes": [
        {{"name": "ratedCapacity", "label": "额定容量", "data_type": "Float", "unit": "MW", "multiplicity": {{"lower": 1, "upper": 1}}}},
        {{"name": "operatingMode", "label": "运行模式", "data_type": "Enum", "enum_name": "OperatingMode", "multiplicity": {{"lower": 1, "upper": 1}}}}
      ],
      "hierarchy_hint": {{"theme_hint": "设备台账", "level_hint": "设备"}}
    }}
  ],
  "enumerations": [
    {{"name": "OperatingMode", "label": "运行模式", "literals": [
      {{"name": "generation", "label": "发电"}},
      {{"name": "pumping", "label": "抽水"}}
    ]}}
  ],
  "confidence_notes": ["list any uncertain extractions"]
}}

DOCUMENTS:
---
{doc_text}
---"""

        # Combined output can be larger than pure discovery output. Use a generous
        # cap; the model will stop early when done.
        return await self._ask(prompt, max_tokens=self.DEFAULT_MAX_TOKENS)

    async def _discover_entities_from_docs(self, doc_text: str, known_context: list[str] = None) -> dict:
        context_hint = ""
        if known_context:
            context_hint = f"""
IMPORTANT: The following entities have ALREADY been identified from previous documents.
Do NOT repeat them. Only identify NEW entities not in this list:
Already found: {', '.join(known_context)}

If you find entities that RELATE TO the above existing ones (e.g. a new class that
references an existing class, or an enumeration used by an existing class), still
include them and note the relationship.
"""

        prompt = f"""Analyze the following business documents and identify ALL entity types (Classes) and Enumerations.

This is an M1-level extraction: identify CONCRETE, DOMAIN-SPECIFIC types directly from the documents.
Do NOT generalize — extract exactly what the documents describe.
{context_hint}
For each Class:
1. "name": English PascalCase (e.g. "PumpedStorageUnit", "WaterTurbine")
2. "label": Chinese display name
3. "description": one-sentence Chinese description

For each Enumeration (status types, category types, mode types, etc.):
1. "name": English PascalCase
2. "label": Chinese name
3. "literals": array of {{"name": "english", "label": "中文"}}

Return valid JSON:
{{
  "classes": [
    {{"name": "PumpedStorageUnit", "label": "抽水蓄能机组", "description": "..."}}
  ],
  "enumerations": [
    {{"name": "OperatingMode", "label": "运行模式", "literals": [{{"name": "generation", "label": "发电"}}]}}
  ],
  "confidence_notes": ["list any uncertain extractions"]
}}

DOCUMENTS:
---
{doc_text}
---"""

        return await self._ask(prompt, max_tokens=16384)

    async def _extract_attrs_from_docs(
        self, doc_text: str, classes_batch: list[dict], enum_name_to_id: dict,
        known_attrs_context: dict = None
    ) -> list[dict]:
        class_desc = "\n".join(
            f"- {c['name']} ({c.get('label', '')}): {c.get('description', '')}"
            for c in classes_batch
        )
        enum_names = ", ".join(enum_name_to_id.keys()) if enum_name_to_id else "(none)"

        context_hint = ""
        if known_attrs_context:
            lines = []
            for cname, attr_names in known_attrs_context.items():
                if attr_names:
                    lines.append(f"  - {cname}: already has {', '.join(attr_names[:15])}{'...' if len(attr_names) > 15 else ''}")
            if lines:
                context_hint = f"""
IMPORTANT: The following attributes have ALREADY been found from previous documents for these classes.
Do NOT repeat them. Only identify NEW attributes found in THIS document:
{chr(10).join(lines)}

If you see a known attribute mentioned here with additional details (e.g., a unit, enum value, or clearer description), you may include it with the refined info.
"""

        prompt = f"""For the following classes, extract ALL attributes from the documents.

Classes:
{class_desc}

Available Enumerations: {enum_names}
{context_hint}
For each attribute:
- "name": English camelCase
- "label": Chinese display name
- "data_type": one of String, Float, Integer, Date, Boolean, Enum
- "enum_name": if Enum type, which enumeration
- "unit": measurement unit if applicable (MW, m, rpm, kV, A, mm, MPa, etc.)
- "multiplicity": {{"lower": N, "upper": M}} where -1 = unlimited
- "description": brief Chinese description

Return valid JSON:
{{
  "classes": [
    {{
      "name": "ClassName", "label": "中文名", "description": "...",
      "attributes": [
        {{"name": "ratedCapacity", "label": "额定容量", "data_type": "Float", "unit": "MW", "multiplicity": {{"lower": 1, "upper": 1}}}}
      ]
    }}
  ]
}}

DOCUMENTS:
---
{doc_text}
---"""

        result = await self._ask(prompt, max_tokens=16384)
        return result.get("classes", [])

    async def _extract_assocs_from_docs(self, doc_text: str, classes: list[MOFClass],
                                          known_assocs: list = None) -> dict:
        class_list = "\n".join(f"- {c.name} ({c.label}): {c.description or ''}" for c in classes)

        context_hint = ""
        if known_assocs:
            context_hint = f"""
IMPORTANT: These associations have ALREADY been identified from previous documents.
Do NOT repeat them. Only identify NEW associations found in THIS document:
Already found: {', '.join(known_assocs[:30])}{'...' if len(known_assocs) > 30 else ''}
"""

        prompt = f"""Given these M1 classes, identify all associations (relationships) between them from the documents.

Classes:
{class_list}
{context_hint}

For each association:
- "name": English descriptive name
- "label": Chinese label
- "source_class": source class name
- "source_role": role name
- "source_multiplicity": {{"lower": N, "upper": M}}
- "target_class": target class name
- "target_role": role name
- "target_multiplicity": {{"lower": N, "upper": M}} (-1 = unlimited)
- "association_type": "composition" (part-of), "aggregation" (has-a), or "association" (reference)

Return valid JSON:
{{
  "associations": [...],
  "constraints": [],
  "confidence_notes": []
}}

DOCUMENTS:
---
{doc_text}
---"""

        return await self._ask(prompt, max_tokens=16384)

    async def _extract_compositions_supplement(
        self,
        classes: list[MOFClass],
        existing_assocs: list,
        doc_batch_texts: list[str],
    ) -> list[dict]:
        """V3.1 Phase 1.5: a dedicated pass to补边 M1-to-M1 composition.

        Per-batch association extraction (Phase 2) often misses composition edges
        that span batches — e.g. "电站 contains 机组区域" where 电站 is mentioned
        in one doc and 机组区域 in another. This call looks at the FULL class
        inventory + a concatenated digest of all doc batches, and asks the LLM
        to infer containment relationships it can justify from the text.

        Returns a list of composition/aggregation edges (same shape as
        _extract_assocs_from_docs output items).
        """
        if len(classes) < 2:
            return []
        class_list = "\n".join(
            f"- {c.name} ({c.label}): {(c.description or '')[:80]}" for c in classes
        )
        # Summary of existing edges so LLM doesn't duplicate
        existing_pairs = set()
        for a in existing_assocs:
            src = getattr(a.source, "class_name", None)
            tgt = getattr(a.target, "class_name", None)
            if src and tgt:
                existing_pairs.add(f"{src}→{tgt}")
        existing_hint = ""
        if existing_pairs:
            sample = ", ".join(list(existing_pairs)[:50])
            existing_hint = f"""
The following composition/aggregation/association edges are ALREADY captured;
do NOT repeat them (in any direction):
  {sample}
"""

        # Concatenate doc batches (cap total length to keep prompt bounded)
        MAX_TEXT = 20000
        combined = "\n\n---BATCH---\n\n".join(doc_batch_texts)
        if len(combined) > MAX_TEXT:
            combined = combined[:MAX_TEXT] + "\n\n[... truncated]"

        prompt = f"""You are doing a SUPPLEMENTARY pass to补边 M1 class composition.
从上面已抽取的 {len(classes)} 个类中找出**类间组合/归属关系** (composition / aggregation),
重点是那些跨文档批次可能被漏掉的层级包含关系。

判定要点:
- composition: B is a physical part of A (A contains B permanently)
  · 典型文字线索:"...由...组成"、"...包括"、"X 属于 Y"、"Y 包含 X"
- aggregation: A aggregates B but B is independent (A has B)
  · 典型:"...下辖"、"...负责管理..."、"...下属"
- 仅抽取**明显在文档中体现**的组合关系;不要臆断
- 如果一个类是 root (无父),不要强行给它加父类

输出约束:
- 仅输出 composition 或 aggregation (不输出普通 association/reference)
- source_class 是"容器",target_class 是"被包含者"
- target_multiplicity 默认 {{0, -1}} (0 或多个),composition 则 source 是 {{1,1}}

已知 M1 类:
{class_list}
{existing_hint}
合并后的文档摘录 (可能被截断):
---
{combined}
---

Return valid JSON ONLY:
{{
  "compositions": [
    {{
      "name": "plantHasGenUnit",
      "label": "电站包含机组",
      "source_class": "PumpedStoragePlant",
      "source_role": "plant",
      "source_multiplicity": {{"lower": 1, "upper": 1}},
      "target_class": "PumpedStorageUnit",
      "target_role": "units",
      "target_multiplicity": {{"lower": 0, "upper": -1}},
      "association_type": "composition",
      "confidence": "high"
    }}
  ]
}}

若没发现新的组合关系,返回 {{"compositions": []}}。"""

        try:
            result = await self._ask(prompt, max_tokens=4000)
            return result.get("compositions", []) or []
        except Exception:
            return []
