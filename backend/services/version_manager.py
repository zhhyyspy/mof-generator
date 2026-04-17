"""Manage M1 model version snapshots."""
from __future__ import annotations

import copy
from datetime import datetime

from backend.models.m1_model import M1Model, M1ModelVersion
from backend.storage.file_store import store


class VersionManager:

    def create_version(self, model_id: str, changelog: str | None = None) -> M1ModelVersion:
        model = store.get_model(model_id)
        if model is None:
            raise ValueError(f"Model {model_id} not found")

        # Determine next version number
        if model.versions:
            last = model.versions[-1].version
            parts = last.split(".")
            parts[-1] = str(int(parts[-1]) + 1)
            next_ver = ".".join(parts)
        else:
            next_ver = "1.0"

        # Get current package from latest version or create initial
        if model.versions:
            pkg = copy.deepcopy(model.versions[-1].package)
        else:
            from backend.models.m3_schema import Package
            pkg = Package(name=model.name, label=model.label)

        version = M1ModelVersion(
            version=next_ver,
            created_at=datetime.now(),
            changelog=changelog,
            package=pkg,
        )

        model.versions.append(version)
        model.current_version = next_ver
        store.save_model(model)

        return version

    def save_current_as_version(self, model: M1Model, changelog: str | None = None) -> str:
        """Create a new version from the current model state."""
        if not model.versions:
            next_ver = "1.0"
        else:
            last = model.versions[-1].version
            parts = last.split(".")
            parts[-1] = str(int(parts[-1]) + 1)
            next_ver = ".".join(parts)

        current_pkg = model.versions[-1].package if model.versions else None
        if current_pkg is None:
            raise ValueError("Model has no package data to version")

        version = M1ModelVersion(
            version=next_ver,
            created_at=datetime.now(),
            changelog=changelog,
            package=copy.deepcopy(current_pkg),
        )

        model.versions.append(version)
        model.current_version = next_ver
        store.save_model(model)
        return next_ver


version_manager = VersionManager()
