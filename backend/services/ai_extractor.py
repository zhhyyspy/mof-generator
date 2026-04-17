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
    ComplexType, PrimitiveDataType,
)
from backend.services.llm_client import get_active_client, LLMClient


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
5. Return ONLY valid JSON, no markdown code fences, no extra text
"""


def _clean_json_text(text: str) -> str:
    """Clean common LLM JSON issues before parsing."""
    text = text.strip()
    # Remove markdown code fences
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    text = text.strip()

    # Extract the outermost { ... }
    first = text.find("{")
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
    cleaned = _clean_json_text(text)

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

    # Extraction calls get 5 minutes timeout (large prompts take time)
    EXTRACTION_TIMEOUT = 300

    async def _ask(self, prompt: str, max_tokens: int = 4096) -> dict:
        """Send prompt to the active LLM and parse JSON response."""
        text = await self.llm.chat(
            system=SYSTEM_PROMPT,
            user=prompt,
            temperature=0,
            max_tokens=max_tokens,
            timeout_override=self.EXTRACTION_TIMEOUT,
        )
        # _extract_json already has robust local fixing (quotes, commas, braces)
        # No need for expensive LLM retry — local fix handles 95%+ of cases
        return _extract_json(text)

    # ==================================================================
    # Pipeline A: Documents → M1
    # ==================================================================

    # Max chars per batch (~12K tokens for Chinese, targets 15-30s per call)
    BATCH_MAX_CHARS = 25000

    async def extract_m1(
        self,
        document_texts: list[tuple[str, str]],  # list of (filename, full_text)
        progress_callback: Optional[Callable] = None,
        parallel_callback: Optional[Callable] = None,
        check_cancelled: Optional[Callable] = None,
        conversation_callback: Optional[Callable] = None,  # (role, content, meta)
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

        async def _ask_with_conv(prompt: str, max_tokens: int = 4096) -> dict:
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

        self._ask = _ask_with_conv

        def _check():
            if check_cancelled and check_cancelled():
                raise RuntimeError("用户中止提取")

        try:
            return await self._run_extract_m1_pipeline(
                document_texts, progress_callback, parallel_callback, _check
            )
        finally:
            self._ask = _orig_ask

    async def _run_extract_m1_pipeline(self, document_texts, progress_callback, parallel_callback, _check):
        """The actual extraction pipeline (called with monkey-patched self._ask)."""

        # ---- Split documents into batches by size ----
        batches = []
        current_batch = []
        current_size = 0
        for filename, text in document_texts:
            if current_size + len(text) > self.BATCH_MAX_CHARS and current_batch:
                batches.append(current_batch)
                current_batch = []
                current_size = 0
            current_batch.append((filename, text))
            current_size += len(text)
        if current_batch:
            batches.append(current_batch)

        total_batches = len(batches)

        if progress_callback:
            if total_batches > 1:
                await progress_callback("discovering_entities", 0.10,
                    f"文档已分为 {total_batches} 批处理（全量数据）...")
            else:
                await progress_callback("discovering_entities", 0.10,
                    "正在从文档中识别实体类型和枚举...")

        # ---- Process batches: first batch serial (gets initial context), rest parallel ----
        MAX_CONCURRENT = 3  # Max parallel AI calls to avoid rate limiting
        sem = asyncio.Semaphore(MAX_CONCURRENT)

        all_classes_raw = []
        all_enums_raw = []
        all_confidence_notes = []
        known_entity_names = []
        completed_batches = 0

        async def process_entity_batch(batch_idx, batch, context):
            _check()  # Check cancellation before each batch
            nonlocal completed_batches
            subtask_id = f"entity_batch_{batch_idx}"
            batch_filenames = [fn for fn, _ in batch]
            subtask_name = f"实体批{batch_idx+1}: {', '.join(batch_filenames[:2])}{'...' if len(batch_filenames) > 2 else ''}"

            if parallel_callback:
                await parallel_callback(subtask_id, subtask_name, "queued")

            async with sem:
                batch_text = "\n\n---\n\n".join(
                    f"[文档: {fn}]\n{txt}" for fn, txt in batch
                )

                if parallel_callback:
                    await parallel_callback(subtask_id, subtask_name, "running")
                if progress_callback:
                    await progress_callback("discovering_entities",
                        0.10 + 0.25 * (batch_idx / total_batches),
                        f"第 {batch_idx+1}/{total_batches} 批{'(并行)' if batch_idx > 0 else ''}: 识别实体")

                try:
                    entities = await self._discover_entities_from_docs(batch_text, known_context=context)
                    completed_batches += 1

                    if parallel_callback:
                        await parallel_callback(subtask_id, subtask_name, "done")
                    if progress_callback:
                        await progress_callback("discovering_entities",
                            0.10 + 0.25 * (completed_batches / total_batches),
                            f"实体发现: {completed_batches}/{total_batches} 批完成")
                    return entities
                except Exception as e:
                    if parallel_callback:
                        await parallel_callback(subtask_id, subtask_name, "error")
                    raise

        if total_batches == 1:
            # Single batch — just run it
            entities = await process_entity_batch(0, batches[0], [])
            all_classes_raw.extend(entities.get("classes", []))
            all_enums_raw.extend(entities.get("enumerations", []))
            all_confidence_notes.extend(entities.get("confidence_notes", []))
        else:
            # First batch serial to get initial entity context
            first_entities = await process_entity_batch(0, batches[0], [])
            first_classes = first_entities.get("classes", [])
            first_enums = first_entities.get("enumerations", [])
            all_classes_raw.extend(first_classes)
            all_enums_raw.extend(first_enums)
            all_confidence_notes.extend(first_entities.get("confidence_notes", []))

            # Build context from first batch for remaining batches
            known_entity_names = [c.get("name", "") for c in first_classes]
            known_entity_names += [e.get("name", "") for e in first_enums]

            # Remaining batches run in parallel (all share the same initial context)
            if len(batches) > 1:
                if progress_callback:
                    await progress_callback("discovering_entities", 0.15,
                        f"剩余 {len(batches)-1} 批并行处理中...")

                tasks = [
                    process_entity_batch(i, batches[i], list(known_entity_names))
                    for i in range(1, len(batches))
                ]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                for r in results:
                    if isinstance(r, Exception):
                        all_confidence_notes.append(f"一个批次处理失败: {str(r)}")
                        continue
                    all_classes_raw.extend(r.get("classes", []))
                    all_enums_raw.extend(r.get("enumerations", []))
                    all_confidence_notes.extend(r.get("confidence_notes", []))

        # Deduplicate by name
        seen_cls = set()
        deduped_classes = []
        for c in all_classes_raw:
            if c.get("name") not in seen_cls:
                seen_cls.add(c.get("name"))
                deduped_classes.append(c)
        seen_enum = set()
        deduped_enums = []
        for e in all_enums_raw:
            if e.get("name") not in seen_enum:
                seen_enum.add(e.get("name"))
                deduped_enums.append(e)

        classes_raw = deduped_classes
        enums_raw = deduped_enums

        _check()  # Check cancellation before attribute extraction

        # ---- Prepare document BATCHES for attribute/association extraction ----
        # Each doc batch contains COMPLETE documents (no truncation).
        # We'll iterate doc batches per class, carrying forward discovered attributes as context.
        # Reuse the same doc-batching logic as entity discovery (the `batches` list).
        doc_batch_texts = [
            "\n\n---\n\n".join(f"[文档: {fn}]\n{txt}" for fn, txt in batch)
            for batch in batches
        ]
        # For association extraction later, use the first/largest batch as context sample
        combined = doc_batch_texts[0] if doc_batch_texts else ""

        confidence_notes = all_confidence_notes

        # Build Enumeration objects
        enumerations = []
        for e in enums_raw:
            enum_id = str(uuid.uuid4())
            literals = [
                EnumLiteral(
                    id=str(uuid.uuid4()),
                    name=lit.get("name", ""),
                    label=lit.get("label", ""),
                    value=lit.get("value"),
                )
                for lit in e.get("literals", [])
            ]
            enumerations.append(Enumeration(
                id=enum_id, name=e.get("name", ""), label=e.get("label", ""),
                description=e.get("description"), literals=literals,
            ))

        enum_name_to_id = {e.name: e.id for e in enumerations}

        # Step 2: Attribute Extraction (parallel, 3 classes per call, max 3 concurrent)
        class_batches = [classes_raw[i:i+3] for i in range(0, len(classes_raw), 3)]
        total_class_batches = len(class_batches)

        if progress_callback:
            await progress_callback("extracting_attributes", 0.35,
                f"开始并行提取属性: {len(classes_raw)} 个类, 分 {total_class_batches} 批, 最多 {MAX_CONCURRENT} 路并发...")

        completed_attr_batches = 0
        total_work_units = len(class_batches) * len(doc_batch_texts)

        async def extract_attr_batch(cls_batch_idx, cls_batch):
            """For one class batch: iterate ALL doc batches serially, accumulating attributes.
            Each doc batch sees complete docs + attributes already found for these classes,
            so new docs can add/refine attributes while knowing what's already known.
            """
            _check()
            subtask_id = f"attr_batch_{cls_batch_idx}"
            batch_names = ", ".join(c.get("label", c.get("name", "")) for c in cls_batch)
            subtask_name = f"属性批{cls_batch_idx+1}: {batch_names}"

            if parallel_callback:
                await parallel_callback(subtask_id, subtask_name, "queued")

            async with sem:
                if parallel_callback:
                    await parallel_callback(subtask_id, subtask_name, "running")

                # Accumulate attributes per class across doc batches
                accumulated = {c["name"]: {"name": c["name"], "label": c.get("label", ""),
                                             "description": c.get("description", ""),
                                             "attributes": []} for c in cls_batch}

                for doc_idx, doc_text in enumerate(doc_batch_texts):
                    _check()
                    nonlocal completed_attr_batches
                    completed_attr_batches += 1

                    if progress_callback:
                        await progress_callback("extracting_attributes",
                            0.35 + 0.30 * (completed_attr_batches / max(total_work_units, 1)),
                            f"属性提取 [类批{cls_batch_idx+1}/{len(class_batches)} × 文档批{doc_idx+1}/{len(doc_batch_texts)}]: {batch_names}")

                    # Build context: attributes already found for this class batch
                    attr_context = {}
                    for name, info in accumulated.items():
                        if info["attributes"]:
                            attr_context[name] = [a.get("name", "") for a in info["attributes"]]

                    try:
                        result = await self._extract_attrs_from_docs(
                            doc_text, cls_batch, enum_name_to_id,
                            known_attrs_context=attr_context,
                        )
                        # Merge new attributes (dedupe by name within class)
                        for cls_data in result:
                            cname = cls_data.get("name", "")
                            if cname not in accumulated:
                                continue
                            existing_names = {a.get("name") for a in accumulated[cname]["attributes"]}
                            for attr in cls_data.get("attributes", []):
                                if attr.get("name") and attr.get("name") not in existing_names:
                                    accumulated[cname]["attributes"].append(attr)
                                    existing_names.add(attr.get("name"))
                    except Exception as e:
                        confidence_notes.append(f"类批{cls_batch_idx+1} × 文档批{doc_idx+1} 失败: {str(e)[:100]}")

                if parallel_callback:
                    await parallel_callback(subtask_id, subtask_name, "done")

                return list(accumulated.values())

        attr_tasks = [extract_attr_batch(i, b) for i, b in enumerate(class_batches)]
        attr_results = await asyncio.gather(*attr_tasks, return_exceptions=True)

        mof_classes = []
        total_attrs = 0
        for batch_result in attr_results:
            if isinstance(batch_result, Exception):
                confidence_notes.append(f"一个属性批次失败: {str(batch_result)}")
                continue
            for cls_data in batch_result:
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
                    mult = ad.get("multiplicity", {})
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
                    description=cls_data.get("description"),
                    attributes=attrs,
                ))

        if progress_callback:
            await progress_callback("extracting_attributes", 0.65,
                f"全部属性提取完成: {len(mof_classes)} 个类, {total_attrs} 个属性")

        _check()  # Check cancellation before association extraction

        # Step 3: Association Extraction — iterate all doc batches with context
        class_id_map = {c.name: c.id for c in mof_classes}
        all_associations_raw = []
        known_assoc_names = []

        for doc_idx, doc_text in enumerate(doc_batch_texts):
            _check()
            if progress_callback:
                await progress_callback("extracting_associations",
                    0.65 + 0.30 * (doc_idx / max(len(doc_batch_texts), 1)),
                    f"关联提取 [文档批{doc_idx+1}/{len(doc_batch_texts)}]")

            try:
                assoc_data = await self._extract_assocs_from_docs(
                    doc_text, mof_classes, known_assocs=known_assoc_names
                )
                for ad in assoc_data.get("associations", []):
                    aname = ad.get("name", "")
                    if aname and aname not in known_assoc_names:
                        all_associations_raw.append(ad)
                        known_assoc_names.append(aname)
                confidence_notes.extend(assoc_data.get("confidence_notes", []))
            except Exception as e:
                confidence_notes.append(f"关联批{doc_idx+1} 失败: {str(e)[:100]}")

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

        package = Package(
            id=str(uuid.uuid4()), name="M1Package", label="M1模型",
            classes=mof_classes, enumerations=enumerations, associations=associations,
        )

        if progress_callback:
            await progress_callback("completed", 1.0, "M1模型提取完成！")

        return {
            "package": package.model_dump(),
            "classes_found": len(mof_classes),
            "attributes_found": total_attrs,
            "associations_found": len(associations),
            "enumerations_found": len(enumerations),
            "confidence_notes": confidence_notes,
        }

    # ==================================================================
    # Pipeline B: M1 → M2
    # ==================================================================

    async def derive_m2(
        self,
        m1_package: dict,
        progress_callback: Optional[Callable] = None,
    ) -> dict:
        """
        Given an M1 model, derive a generalized M2 meta-model by:
        - Identifying common/shared attributes across M1 classes → M2 base class attributes
        - Abstracting domain-specific classes into generic types
        - Extracting universal enumerations
        - Defining generic association patterns
        """
        if progress_callback:
            await progress_callback("deriving_m2", 0.2, "正在从M1模型抽象出M2元模型...")

        m1_json = json.dumps(m1_package, ensure_ascii=False, indent=2)

        prompt = f"""Given the following M1 (domain-specific) model, derive an M2 (meta-model) by generalizing it.

The M2 meta-model should:
1. Extract COMMON attributes shared by multiple M1 classes into abstract M2 base classes
   (e.g., if multiple M1 classes all have "code", "name", "status" → create an M2 "Equipment" base class)
2. Define generic/abstract types that the M1 classes specialize
3. Keep only universal attributes in M2; domain-specific attributes belong in M1
4. Identify common enumeration types that are shared across classes
5. Define abstract association patterns (e.g., "containsSubEquipment" self-association)

For each M2 class:
- "name": English PascalCase (generic name, e.g. "Equipment" not "PumpedStorageUnit")
- "label": Chinese generic label
- "description": what this abstract type represents
- "attributes": only COMMON/UNIVERSAL attributes (shared by multiple M1 classes)

For each M1 class, indicate which M2 class it specializes via "parent_class_name".

Return valid JSON:
{{
  "m2_package": {{
    "name": "M2MetaModelPackage",
    "label": "M2元模型",
    "classes": [
      {{
        "name": "Equipment",
        "label": "设备",
        "description": "所有设备类型的通用基类",
        "attributes": [
          {{"name": "equipmentCode", "label": "设备编码", "data_type": "String", "multiplicity": {{"lower": 1, "upper": 1}}}},
          ...
        ]
      }}
    ],
    "enumerations": [...],
    "associations": [...]
  }},
  "m1_class_mappings": [
    {{"m1_class_name": "PumpedStorageUnit", "m2_parent_name": "Equipment"}}
  ],
  "confidence_notes": []
}}

M1 Model:
{m1_json}"""

        result = await self._ask(prompt, max_tokens=8192)

        # Build M2 Package
        m2_raw = result.get("m2_package", {})
        m2_classes = []
        for c in m2_raw.get("classes", []):
            attrs = []
            for a in c.get("attributes", []):
                dt = a.get("data_type", "String")
                mult = a.get("multiplicity", {})
                attrs.append(Attribute(
                    id=str(uuid.uuid4()), name=a.get("name", ""),
                    label=a.get("label", ""), description=a.get("description"),
                    data_type=dt, unit=a.get("unit"),
                    multiplicity=Multiplicity(lower=mult.get("lower", 1), upper=mult.get("upper", 1)),
                ))
            m2_classes.append(MOFClass(
                id=str(uuid.uuid4()), name=c.get("name", ""),
                label=c.get("label", ""), description=c.get("description"),
                is_abstract=True, attributes=attrs,
            ))

        m2_enums = []
        for e in m2_raw.get("enumerations", []):
            lits = [EnumLiteral(id=str(uuid.uuid4()), name=l.get("name", ""), label=l.get("label", ""))
                    for l in e.get("literals", [])]
            m2_enums.append(Enumeration(
                id=str(uuid.uuid4()), name=e.get("name", ""),
                label=e.get("label", ""), literals=lits,
            ))

        m2_assocs = []
        for a in m2_raw.get("associations", []):
            src = a.get("source", {})
            tgt = a.get("target", {})
            m2_class_id_map = {c.name: c.id for c in m2_classes}
            m2_assocs.append(Association(
                id=str(uuid.uuid4()), name=a.get("name", ""),
                label=a.get("label", ""), description=a.get("description"),
                source=AssociationEnd(
                    class_ref=m2_class_id_map.get(src.get("class_name", ""), ""),
                    class_name=src.get("class_name", ""),
                    multiplicity=Multiplicity(
                        lower=src.get("multiplicity", {}).get("lower", 0),
                        upper=src.get("multiplicity", {}).get("upper", 1)),
                ),
                target=AssociationEnd(
                    class_ref=m2_class_id_map.get(tgt.get("class_name", ""), ""),
                    class_name=tgt.get("class_name", ""),
                    multiplicity=Multiplicity(
                        lower=tgt.get("multiplicity", {}).get("lower", 0),
                        upper=tgt.get("multiplicity", {}).get("upper", -1)),
                ),
                association_type=a.get("association_type", "association"),
            ))

        m2_package = Package(
            id=str(uuid.uuid4()),
            name=m2_raw.get("name", "M2MetaModelPackage"),
            label=m2_raw.get("label", "M2元模型"),
            classes=m2_classes, enumerations=m2_enums, associations=m2_assocs,
        )

        if progress_callback:
            await progress_callback("completed", 1.0, "M2元模型推导完成！")

        return {
            "m2_package": m2_package.model_dump(),
            "m1_class_mappings": result.get("m1_class_mappings", []),
            "confidence_notes": result.get("confidence_notes", []),
        }

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

        return await self._ask(prompt, max_tokens=8192)

    # ==================================================================
    # Internal prompt methods — Pipeline A
    # ==================================================================

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

        return await self._ask(prompt, max_tokens=4096)

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

        result = await self._ask(prompt, max_tokens=8192)
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

        return await self._ask(prompt, max_tokens=4096)
