"""
Complete Model Package I/O — V1.0 format.

Produces and consumes `.mofpkg.zip` archives containing:
  - manifest.json      (version + catalog + checksum)
  - README.md          (human-readable overview)
  - models/*.json      (M1 + M2 full model JSON)
  - documents/{id}/    (optional: meta + text + original binary)
  - llm/providers.json (optional: provider configs, api_key stripped)

Single source of truth for both export and import:
  - `PackageExporter.export(m1_id, options)` → bytes (zip)
  - `PackageImporter.preview(zip_bytes)`     → preview dict
  - `PackageImporter.do_import(zip_bytes, options)` → result dict
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from backend.config import settings
from backend.models.m1_model import M1Model
from backend.models.m3_schema import Package
from backend.storage.file_store import FileStore


FORMAT_VERSION = "1.0"
MOF_SYSTEM_VERSION = "V3.0"

# The set of roles recognized in a package
ROLE_M1 = "m1"
ROLE_M2 = "m2"

# ----------------------------------------------------------------------------
#                                   Helpers
# ----------------------------------------------------------------------------

_SAFE_NAME_RE = re.compile(r"[^\w\-._()\[\] ]", re.UNICODE)


def _safe_filename(name: str, fallback: str = "model") -> str:
    """Make a string safe to use as a filename. Strips path separators and control chars."""
    if not name:
        return fallback
    cleaned = _SAFE_NAME_RE.sub("_", name).strip().strip(".")
    return (cleaned or fallback)[:80]


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _json_dumps(obj: Any, *, indent: int = 2) -> str:
    """json.dumps with a default=str fallback so stray datetime / Path values
    (e.g. nested in pydantic .model_dump() output if mode='json' missed a field)
    get coerced to strings instead of raising TypeError."""
    return json.dumps(obj, ensure_ascii=False, indent=indent, default=str)


def _new_id_from(old_id: str) -> str:
    """Generate a fresh ID while preserving the m2_ prefix convention for M2s."""
    suffix = uuid.uuid4().hex[:8]
    if old_id.startswith("m2_"):
        return f"m2_imported_{suffix}"
    return f"imported_{suffix}"


# ----------------------------------------------------------------------------
#                                  Exporter
# ----------------------------------------------------------------------------


class PackageExporter:
    """Build a .mofpkg.zip byte stream for the given M1 model (+ optional assets)."""

    def __init__(self, store: Optional[FileStore] = None):
        self.store = store or FileStore()

    def export(
        self,
        m1_id: str,
        *,
        include_m2: bool = True,
        include_all_versions: bool = False,
        include_documents: bool = False,
        include_llm_providers: bool = False,
        note: str = "",
        exported_by: str = "",
    ) -> tuple[bytes, str]:
        """Returns (zip_bytes, suggested_filename)."""
        m1 = self.store.get_model(m1_id)
        if m1 is None:
            raise ValueError(f"M1 model not found: {m1_id}")

        # Strip to current_version if requested. Use mode="json" so datetime
        # and other non-JSON-native types get serialized to strings.
        m1_payload = m1.model_dump(mode="json")
        if not include_all_versions and m1.versions:
            cur = m1.current_version
            kept = [v for v in m1.versions if v.version == cur]
            if not kept:
                kept = [m1.versions[-1]]
            m1_payload["versions"] = [v.model_dump(mode="json") for v in kept]
            m1_payload["current_version"] = kept[0].version

        # Resolve M2 if requested and present
        m2_payload = None
        m2_id = m1.m2_template_id
        if include_m2 and m2_id:
            m2 = self.store.get_model(m2_id)
            if m2 is not None:
                m2_payload = m2.model_dump(mode="json")
                if not include_all_versions and m2.versions:
                    cur = m2.current_version
                    kept = [v for v in m2.versions if v.version == cur]
                    if not kept:
                        kept = [m2.versions[-1]]
                    m2_payload["versions"] = [v.model_dump(mode="json") for v in kept]
                    m2_payload["current_version"] = kept[0].version

        # Documents (optional)
        doc_payloads: list[dict] = []
        if include_documents:
            pkg_dict = self._current_package_dict(m1_payload)
            doc_ids = set(m1_payload.get("source_document_ids") or [])
            # Also include any doc referenced in the package (future-proof)
            for did in list(doc_ids):
                meta = self.store.get_document_meta(did)
                if not meta:
                    continue
                text = self.store.get_document_text(did) or ""
                # Find original file (non .meta.json, non .txt)
                original_bytes = None
                original_name = None
                for p in settings.documents_dir.glob(f"{did}.*"):
                    if p.suffix in (".json", ".txt"):
                        continue
                    if p.name.endswith(".meta.json"):
                        continue
                    try:
                        original_bytes = p.read_bytes()
                        original_name = p.name
                    except Exception:
                        pass
                    break
                doc_payloads.append({
                    "id": did,
                    "meta": meta,
                    "text": text,
                    "original_bytes": original_bytes,
                    "original_name": original_name,
                })

        # LLM providers (optional; strip api_key)
        llm_payload = None
        if include_llm_providers:
            providers = self.store.load_llm_configs() or []
            stripped = []
            for p in providers:
                clean = dict(p)
                clean.pop("api_key", None)
                stripped.append(clean)
            llm_payload = stripped

        # ---- Build manifest ----
        contents: dict[str, Any] = {"models": [], "documents": [], "llm_providers": []}
        checksums: dict[str, str] = {}

        m1_file = "models/" + _safe_filename(m1.id) + ".json"
        m1_bytes = _json_dumps(m1_payload).encode("utf-8")
        checksums[m1_file] = _sha256_bytes(m1_bytes)
        contents["models"].append({
            "id": m1.id, "role": ROLE_M1,
            "label": m1.label or m1.name,
            "file": m1_file,
            "class_count": len(self._current_package_dict(m1_payload).get("classes") or []),
            "assoc_count": len(self._current_package_dict(m1_payload).get("associations") or []),
            "versions_included": len(m1_payload.get("versions") or []),
            "current_version": m1_payload.get("current_version"),
        })

        m2_file = None
        if m2_payload:
            m2_file = "models/" + _safe_filename(m2_payload["id"]) + ".json"
            m2_bytes = _json_dumps(m2_payload).encode("utf-8")
            checksums[m2_file] = _sha256_bytes(m2_bytes)
            m2_cur_pkg = self._current_package_dict(m2_payload)
            contents["models"].append({
                "id": m2_payload["id"], "role": ROLE_M2,
                "label": m2_payload.get("label") or m2_payload.get("name"),
                "file": m2_file,
                "class_count": len(m2_cur_pkg.get("classes") or []),
                "pattern_count": len(m2_cur_pkg.get("structural_patterns") or []),
                "versions_included": len(m2_payload.get("versions") or []),
                "current_version": m2_payload.get("current_version"),
            })

        for dp in doc_payloads:
            entry = {
                "id": dp["id"],
                "filename": dp["meta"].get("filename"),
                "has_text": bool(dp["text"]),
                "has_original": dp["original_bytes"] is not None,
                "size": len(dp["text"] or ""),
            }
            # Directory layout: documents/{id}/meta.json etc.
            base = f"documents/{dp['id']}/"
            meta_bytes = _json_dumps(dp["meta"]).encode("utf-8")
            text_bytes = (dp["text"] or "").encode("utf-8")
            checksums[base + "meta.json"] = _sha256_bytes(meta_bytes)
            checksums[base + "text.txt"] = _sha256_bytes(text_bytes)
            if dp["original_bytes"] is not None:
                checksums[base + "original/" + dp["original_name"]] = _sha256_bytes(dp["original_bytes"])
            contents["documents"].append(entry)

        if llm_payload is not None:
            llm_bytes = _json_dumps(llm_payload).encode("utf-8")
            checksums["llm/providers.json"] = _sha256_bytes(llm_bytes)
            contents["llm_providers"] = [
                {"id": p.get("id"), "name": p.get("name"), "provider": p.get("provider"),
                 "model": p.get("model"), "api_key_included": False}
                for p in llm_payload
            ]

        manifest = {
            "format_version": FORMAT_VERSION,
            "mof_system_version": MOF_SYSTEM_VERSION,
            "exported_at": _now_iso(),
            "exported_by": exported_by or "",
            "title": (m1.label or m1.name) + (" + M2" if m2_payload else ""),
            "note": note or "",
            "contents": contents,
            "dependencies": {
                "m2_template_id": m2_id or None,
                "_note": "M1's required M2. If null or bundled, import is self-contained.",
            },
            "integrity": {"sha256": checksums},
        }
        manifest_bytes = _json_dumps(manifest).encode("utf-8")

        # README
        readme = _build_readme(manifest, m1, m2_payload)
        readme_bytes = readme.encode("utf-8")

        # ---- Build zip ----
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", manifest_bytes)
            zf.writestr("README.md", readme_bytes)
            zf.writestr(m1_file, m1_bytes)
            if m2_payload and m2_file:
                zf.writestr(m2_file, m2_bytes)
            for dp in doc_payloads:
                base = f"documents/{dp['id']}/"
                zf.writestr(base + "meta.json", _json_dumps(dp["meta"]))
                zf.writestr(base + "text.txt", dp["text"] or "")
                if dp["original_bytes"] is not None:
                    zf.writestr(base + "original/" + dp["original_name"], dp["original_bytes"])
            if llm_payload is not None:
                zf.writestr("llm/providers.json",
                            _json_dumps(llm_payload))

        zip_bytes = buf.getvalue()
        filename = _safe_filename(m1.label or m1.name, fallback=m1.id)
        filename = f"{filename}_{datetime.utcnow().strftime('%Y%m%d')}.mofpkg.zip"
        return zip_bytes, filename

    @staticmethod
    def _current_package_dict(model_payload: dict) -> dict:
        """Given an M1Model dict, return the current version's package dict."""
        versions = model_payload.get("versions") or []
        if not versions:
            return {}
        cur = model_payload.get("current_version")
        for v in versions:
            if v.get("version") == cur:
                return v.get("package") or {}
        return versions[-1].get("package") or {}


def _build_readme(manifest: dict, m1: M1Model, m2_payload: Optional[dict]) -> str:
    """Human-readable overview; stored at README.md inside the zip."""
    lines: list[str] = []
    lines.append(f"# {manifest['title']}")
    lines.append("")
    lines.append(f"- 导出时间: {manifest['exported_at']}")
    lines.append(f"- MOF 系统版本: {manifest['mof_system_version']}")
    lines.append(f"- 包格式版本: {manifest['format_version']}")
    if manifest.get("note"):
        lines.append(f"- 备注: {manifest['note']}")
    lines.append("")
    lines.append("## 包含内容")
    for m in manifest["contents"]["models"]:
        icon = "🏷️" if m["role"] == "m1" else "🧬"
        extra = []
        if m.get("class_count") is not None: extra.append(f"{m['class_count']} 类")
        if m.get("assoc_count"): extra.append(f"{m['assoc_count']} 关联")
        if m.get("pattern_count"): extra.append(f"{m['pattern_count']} 元结构")
        lines.append(f"- {icon} **{m['role'].upper()}** `{m['id']}` · {m['label']}"
                     + (f" · ({', '.join(extra)})" if extra else ""))
    if manifest["contents"].get("documents"):
        lines.append("")
        lines.append("### 源文档")
        for d in manifest["contents"]["documents"]:
            orig = "带原始二进制" if d.get("has_original") else "仅文本"
            lines.append(f"- 📄 `{d['id']}` · {d.get('filename', '')} ({orig})")
    if manifest["contents"].get("llm_providers"):
        lines.append("")
        lines.append("### LLM Provider (API Key 已剥离)")
        for p in manifest["contents"]["llm_providers"]:
            lines.append(f"- 🔌 {p['name']} · {p['provider']} / {p['model']}")
    lines.append("")
    lines.append("## 如何导入")
    lines.append("在 MOF Generator 前端点击 **📥 导入包** 按钮,选择此 zip 文件。")
    lines.append("导入时可选择冲突策略(跳过 / 改名 / 覆盖),推荐选择"
                 "**🎯 改名导入** 以避免覆盖现有模型。")
    lines.append("")
    lines.append("## 版本兼容")
    lines.append(f"- 此包要求导入端 MOF 系统版本 ≥ `{manifest['mof_system_version']}`。")
    lines.append("- 不兼容的字段将被忽略并提示警告。")
    return "\n".join(lines)


# ----------------------------------------------------------------------------
#                                 Importer
# ----------------------------------------------------------------------------


# Conflict strategies
STRATEGY_SKIP = "skip"          # keep local, skip imported
STRATEGY_OVERWRITE = "overwrite"  # replace local with imported (risky)
STRATEGY_RENAME = "rename"      # assign fresh IDs to imported entities

VALID_STRATEGIES = (STRATEGY_SKIP, STRATEGY_OVERWRITE, STRATEGY_RENAME)


class PackageImporter:
    """Reads a .mofpkg.zip and replays its content into the local store."""

    def __init__(self, store: Optional[FileStore] = None):
        self.store = store or FileStore()

    # ------ preview (dry run) ------

    def preview(self, zip_bytes: bytes) -> dict:
        """Parse manifest + detect local conflicts. No side effects."""
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            manifest = self._read_manifest(zf)
            # Build conflict table for models
            existing_ids = {m["id"] for m in self.store.list_models()}
            existing_doc_ids = {d["id"] for d in self.store.list_documents()}
            model_conflicts = []
            for m in manifest["contents"]["models"]:
                model_conflicts.append({
                    **m,
                    "conflict": m["id"] in existing_ids,
                })
            doc_conflicts = []
            for d in manifest["contents"].get("documents", []):
                doc_conflicts.append({
                    **d,
                    "conflict": d["id"] in existing_doc_ids,
                })
            # Dependency check: is m2_template_id bundled or locally available?
            dep = manifest.get("dependencies") or {}
            m2_dep = dep.get("m2_template_id")
            bundled_ids = {m["id"] for m in manifest["contents"]["models"]}
            dep_status = None
            if m2_dep:
                if m2_dep in bundled_ids:
                    dep_status = "bundled"
                elif m2_dep in existing_ids:
                    dep_status = "local"
                else:
                    dep_status = "missing"
            else:
                dep_status = "none"
        return {
            "manifest": manifest,
            "conflicts": {
                "models": model_conflicts,
                "documents": doc_conflicts,
            },
            "dependency": {"m2_template_id": m2_dep, "status": dep_status},
            "warnings": self._version_warnings(manifest),
        }

    # ------ actual import ------

    def do_import(
        self,
        zip_bytes: bytes,
        *,
        strategy: str = STRATEGY_RENAME,
        import_documents: bool = True,
        import_llm: bool = False,
    ) -> dict:
        """Apply the import per strategy. Returns a result summary.

        Returns dict with keys: imported (list), skipped (list), failed (list),
        id_map ({old_id: new_id} when strategy=rename), primary_m1_id (for UI to auto-load).
        """
        if strategy not in VALID_STRATEGIES:
            raise ValueError(f"invalid strategy: {strategy}")

        imported: list[dict] = []
        skipped: list[dict] = []
        failed: list[dict] = []
        id_map: dict[str, str] = {}
        primary_m1_id: Optional[str] = None

        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            manifest = self._read_manifest(zf)
            existing_model_ids = {m["id"] for m in self.store.list_models()}

            # Plan ID mapping upfront when renaming, so intra-package refs can be rewritten.
            # Keep M2 tied to its M1 via the `m2_${m1}` convention the frontend relies on:
            #   M1  → `imported_<hash>`
            #   its bundled M2 → `m2_imported_<same hash>`
            if strategy == STRATEGY_RENAME:
                m1_entries = [m for m in manifest["contents"]["models"] if m.get("role") == ROLE_M1]
                other_entries = [m for m in manifest["contents"]["models"] if m.get("role") != ROLE_M1]
                suffix_by_old: dict[str, str] = {}
                for m in m1_entries:
                    suffix = uuid.uuid4().hex[:8]
                    id_map[m["id"]] = f"imported_{suffix}"
                    suffix_by_old[m["id"]] = suffix
                # Second pass: bind bundled M2 to its M1's suffix (typical: 1 M1 + 1 M2).
                dep_m2 = (manifest.get("dependencies") or {}).get("m2_template_id")
                for m in other_entries:
                    if m.get("role") == ROLE_M2 and m["id"] == dep_m2 and m1_entries:
                        id_map[m["id"]] = f"m2_imported_{suffix_by_old[m1_entries[0]['id']]}"
                    else:
                        id_map[m["id"]] = _new_id_from(m["id"])

            # Dependency rewrite: if an imported M1 points at an M2 NOT in the package
            # and we're renaming, keep the original m2_template_id (points to local M2).
            # (Covered naturally because id_map only contains bundled IDs.)

            # Load model payloads & apply strategy
            for entry in manifest["contents"]["models"]:
                try:
                    raw = zf.read(entry["file"])
                    # Verify checksum
                    expected = manifest.get("integrity", {}).get("sha256", {}).get(entry["file"])
                    if expected and _sha256_bytes(raw) != expected:
                        failed.append({**entry, "error": "校验和不匹配,文件可能损坏"})
                        continue
                    data = json.loads(raw.decode("utf-8"))
                    old_id = entry["id"]

                    if old_id in existing_model_ids and strategy == STRATEGY_SKIP:
                        skipped.append({**entry, "reason": "已存在,策略=跳过"})
                        continue

                    # Apply renaming
                    if strategy == STRATEGY_RENAME:
                        new_id = id_map[old_id]
                        data["id"] = new_id
                        # Rewrite M1's m2_template_id to new ID IF bundled M2 was also renamed
                        if data.get("m2_template_id") in id_map:
                            data["m2_template_id"] = id_map[data["m2_template_id"]]
                        imported_id = new_id
                    else:
                        # overwrite or skip-but-not-colliding
                        imported_id = old_id

                    model = M1Model.model_validate(data)
                    self.store.save_model(model)
                    imported.append({
                        "role": entry.get("role"),
                        "old_id": old_id,
                        "new_id": imported_id,
                        "label": entry.get("label"),
                    })
                    if entry.get("role") == ROLE_M1 and primary_m1_id is None:
                        primary_m1_id = imported_id
                except Exception as e:
                    failed.append({**entry, "error": str(e)})

            # Documents (only imported if the option is on AND strategy allows)
            if import_documents and manifest["contents"].get("documents"):
                existing_doc_ids = {d["id"] for d in self.store.list_documents()}
                for dentry in manifest["contents"]["documents"]:
                    did = dentry["id"]
                    try:
                        if did in existing_doc_ids and strategy == STRATEGY_SKIP:
                            skipped.append({"type": "document", **dentry, "reason": "已存在"})
                            continue
                        # Rename doc if strategy=rename and collision
                        target_did = did
                        if did in existing_doc_ids and strategy == STRATEGY_RENAME:
                            target_did = "doc_imported_" + uuid.uuid4().hex[:8]

                        meta_raw = zf.read(f"documents/{did}/meta.json")
                        text_raw = zf.read(f"documents/{did}/text.txt")
                        meta = json.loads(meta_raw.decode("utf-8"))
                        meta["id"] = target_did
                        self.store.save_document_meta(target_did, meta)
                        self.store.save_document_text(target_did, text_raw.decode("utf-8"))
                        # Original binary if present
                        orig_prefix = f"documents/{did}/original/"
                        for name in zf.namelist():
                            if name.startswith(orig_prefix):
                                origname = name[len(orig_prefix):]
                                if not origname:
                                    continue
                                suffix = Path(origname).suffix or ""
                                (settings.documents_dir / f"{target_did}{suffix}").write_bytes(zf.read(name))
                                break
                        imported.append({"type": "document", "old_id": did, "new_id": target_did,
                                         "filename": meta.get("filename")})
                    except Exception as e:
                        failed.append({"type": "document", **dentry, "error": str(e)})

            # LLM providers (structure only, keys stripped on export)
            if import_llm and "llm/providers.json" in zf.namelist():
                try:
                    providers = json.loads(zf.read("llm/providers.json").decode("utf-8"))
                    existing = self.store.load_llm_configs() or []
                    existing_ids = {p.get("id") for p in existing}
                    merged = list(existing)
                    llm_imported = 0
                    for p in providers:
                        if p.get("id") in existing_ids and strategy == STRATEGY_SKIP:
                            continue
                        if p.get("id") in existing_ids and strategy == STRATEGY_RENAME:
                            p["id"] = "llm_imported_" + uuid.uuid4().hex[:8]
                        p.setdefault("api_key", "")  # must be re-entered by user
                        p["is_active"] = False       # do not auto-activate imported provider
                        merged.append(p)
                        llm_imported += 1
                    self.store.save_llm_configs(merged)
                    imported.append({"type": "llm_providers", "count": llm_imported})
                except Exception as e:
                    failed.append({"type": "llm_providers", "error": str(e)})

        return {
            "status": "ok",
            "imported": imported,
            "skipped": skipped,
            "failed": failed,
            "id_map": id_map,
            "primary_m1_id": primary_m1_id,
            "warnings": self._version_warnings(manifest),
        }

    # ------ internal ------

    @staticmethod
    def _read_manifest(zf: zipfile.ZipFile) -> dict:
        try:
            raw = zf.read("manifest.json")
        except KeyError:
            raise ValueError("manifest.json 缺失 — 不是合法的 .mofpkg.zip")
        try:
            manifest = json.loads(raw.decode("utf-8"))
        except Exception as e:
            raise ValueError(f"manifest.json 解析失败: {e}")
        if not isinstance(manifest, dict) or "format_version" not in manifest:
            raise ValueError("manifest.json 格式不合规")
        return manifest

    @staticmethod
    def _version_warnings(manifest: dict) -> list[str]:
        warnings: list[str] = []
        fv = manifest.get("format_version", "")
        if fv != FORMAT_VERSION:
            warnings.append(
                f"包格式版本 {fv} 与当前系统 {FORMAT_VERSION} 不一致,"
                f"未识别的字段将被忽略"
            )
        sv = manifest.get("mof_system_version", "")
        if sv and sv != MOF_SYSTEM_VERSION:
            warnings.append(
                f"源系统版本 {sv} 与当前 {MOF_SYSTEM_VERSION} 不同,"
                f"若出现异常请检查 M3 元元模型差异"
            )
        return warnings
