"""JSON file-based persistence for models and documents."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from backend.config import settings
from backend.models.m1_model import M1Model
from backend.models.m2_template import M2Template


class FileStore:

    # ---- Documents metadata ----

    def _doc_meta_path(self, doc_id: str) -> Path:
        return settings.documents_dir / f"{doc_id}.meta.json"

    def _doc_text_path(self, doc_id: str) -> Path:
        return settings.documents_dir / f"{doc_id}.txt"

    def save_document_meta(self, doc_id: str, meta: dict) -> None:
        self._doc_meta_path(doc_id).write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def save_document_text(self, doc_id: str, text: str) -> None:
        self._doc_text_path(doc_id).write_text(text, encoding="utf-8")

    def get_document_meta(self, doc_id: str) -> Optional[dict]:
        p = self._doc_meta_path(doc_id)
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8"))

    def get_document_text(self, doc_id: str) -> Optional[str]:
        p = self._doc_text_path(doc_id)
        if not p.exists():
            return None
        return p.read_text(encoding="utf-8")

    def list_documents(self) -> list[dict]:
        results = []
        for p in settings.documents_dir.glob("*.meta.json"):
            results.append(json.loads(p.read_text(encoding="utf-8")))
        return sorted(results, key=lambda d: d.get("uploaded_at", ""), reverse=True)

    def delete_document(self, doc_id: str) -> bool:
        meta = self._doc_meta_path(doc_id)
        txt = self._doc_text_path(doc_id)
        deleted = False
        if meta.exists():
            meta.unlink()
            deleted = True
        if txt.exists():
            txt.unlink()
            deleted = True
        # Delete the original uploaded file if it exists
        for f in settings.documents_dir.glob(f"{doc_id}.*"):
            if f.suffix not in (".meta.json", ".txt"):
                f.unlink()
                deleted = True
        return deleted

    # ---- M1 Models ----

    def _model_path(self, model_id: str) -> Path:
        return settings.models_dir / f"{model_id}.json"

    def save_model(self, model: M1Model) -> None:
        self._model_path(model.id).write_text(
            model.model_dump_json(indent=2), encoding="utf-8"
        )

    def get_model(self, model_id: str) -> Optional[M1Model]:
        p = self._model_path(model_id)
        if not p.exists():
            return None
        return M1Model.model_validate_json(p.read_text(encoding="utf-8"))

    def list_models(self) -> list[dict]:
        results = []
        for p in settings.models_dir.glob("*.json"):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                results.append({
                    "id": data["id"],
                    "name": data["name"],
                    "label": data.get("label"),
                    "m2_template_id": data["m2_template_id"],
                    "current_version": data["current_version"],
                    "status": data["status"],
                    "mtime": p.stat().st_mtime,  # for sorting by recency
                })
            except Exception:
                continue
        # Sort by mtime desc (newest first)
        results.sort(key=lambda x: x.get("mtime", 0), reverse=True)
        return results

    def delete_model(self, model_id: str) -> bool:
        p = self._model_path(model_id)
        if p.exists():
            p.unlink()
            return True
        return False

    # ---- M2 Templates ----

    def list_m2_templates(self) -> list[dict]:
        results = []
        for p in settings.m2_templates_dir.glob("*.json"):
            data = json.loads(p.read_text(encoding="utf-8"))
            results.append({
                "id": data["id"],
                "name": data["name"],
                "label": data.get("label"),
                "domain": data.get("domain"),
                "version": data.get("version", "1.0"),
            })
        return results

    def get_m2_template(self, template_id: str) -> Optional[M2Template]:
        p = settings.m2_templates_dir / f"{template_id}.json"
        if not p.exists():
            return None
        return M2Template.model_validate_json(p.read_text(encoding="utf-8"))


    # ---- LLM Provider Configs ----

    def _llm_config_path(self) -> Path:
        return settings.data_dir / "llm_providers.json"

    def save_llm_configs(self, configs: list[dict]) -> None:
        self._llm_config_path().write_text(
            json.dumps(configs, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def load_llm_configs(self) -> list[dict]:
        p = self._llm_config_path()
        if not p.exists():
            return []
        return json.loads(p.read_text(encoding="utf-8"))

    def get_llm_config(self, config_id: str) -> Optional[dict]:
        for c in self.load_llm_configs():
            if c["id"] == config_id:
                return c
        return None

    def get_active_llm_config(self):
        from backend.models.llm_config import LLMProviderConfig
        for c in self.load_llm_configs():
            if c.get("is_active"):
                return LLMProviderConfig(**c)
        return None

    def set_active_llm(self, config_id: str) -> bool:
        configs = self.load_llm_configs()
        found = False
        for c in configs:
            c["is_active"] = (c["id"] == config_id)
            if c["id"] == config_id:
                found = True
        if found:
            self.save_llm_configs(configs)
        return found


store = FileStore()
