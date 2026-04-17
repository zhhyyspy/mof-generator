"""LLM provider configuration endpoints."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException

from backend.models.llm_config import LLMProviderConfig, PROVIDER_PRESETS
from backend.storage.file_store import store
from backend.services.llm_client import LLMClient

router = APIRouter(prefix="/api/v1/llm", tags=["llm-config"])


@router.get("/presets")
async def get_presets():
    """Get provider presets for the UI dropdown."""
    return {"presets": PROVIDER_PRESETS}


@router.get("/providers")
async def list_providers():
    """List all saved LLM provider configs."""
    configs = store.load_llm_configs()
    # Mask API keys for display
    safe = []
    for c in configs:
        sc = dict(c)
        if sc.get("api_key"):
            key = sc["api_key"]
            sc["api_key_masked"] = key[:8] + "..." + key[-4:] if len(key) > 12 else "***"
        else:
            sc["api_key_masked"] = ""
        safe.append(sc)
    return {"providers": safe}


@router.post("/providers")
async def create_provider(config: LLMProviderConfig):
    """Create a new LLM provider config."""
    if not config.id:
        config.id = str(uuid.uuid4())[:8]
    configs = store.load_llm_configs()
    # If this is the first, auto-activate
    if not configs:
        config.is_active = True
    configs.append(config.model_dump())
    store.save_llm_configs(configs)
    return {"status": "created", "id": config.id}


@router.put("/providers/{config_id}")
async def update_provider(config_id: str, config: LLMProviderConfig):
    """Update an existing provider config."""
    configs = store.load_llm_configs()
    for i, c in enumerate(configs):
        if c["id"] == config_id:
            config.id = config_id
            config.is_active = c.get("is_active", False)  # preserve active state
            configs[i] = config.model_dump()
            store.save_llm_configs(configs)
            return {"status": "updated"}
    raise HTTPException(404, f"Provider {config_id} not found")


@router.delete("/providers/{config_id}")
async def delete_provider(config_id: str):
    """Delete a provider config."""
    configs = store.load_llm_configs()
    new_configs = [c for c in configs if c["id"] != config_id]
    if len(new_configs) == len(configs):
        raise HTTPException(404, f"Provider {config_id} not found")
    # If deleted was active, activate the first remaining
    if any(c["id"] == config_id and c.get("is_active") for c in configs):
        if new_configs:
            new_configs[0]["is_active"] = True
    store.save_llm_configs(new_configs)
    return {"status": "deleted"}


@router.post("/providers/{config_id}/activate")
async def activate_provider(config_id: str):
    """Set a provider as the active one."""
    if not store.set_active_llm(config_id):
        raise HTTPException(404, f"Provider {config_id} not found")
    return {"status": "activated", "id": config_id}


@router.post("/providers/{config_id}/test")
async def test_provider(config_id: str):
    """Test connectivity of a provider."""
    config_data = store.get_llm_config(config_id)
    if config_data is None:
        raise HTTPException(404, f"Provider {config_id} not found")
    config = LLMProviderConfig(**config_data)
    client = LLMClient(config)
    result = await client.test_connection()
    return result


@router.post("/test-unsaved")
async def test_unsaved(config: LLMProviderConfig):
    """Test a config before saving (for the UI 'test' button during editing)."""
    client = LLMClient(config)
    result = await client.test_connection()
    return result


# ---- LLM Call Statistics ----

@router.get("/stats")
async def get_stats():
    """Get aggregated LLM call statistics."""
    from backend.services.llm_stats import get_stats as _get_stats
    return _get_stats()


@router.delete("/stats")
async def clear_stats():
    """Clear all LLM call statistics."""
    from backend.services.llm_stats import clear_stats as _clear_stats
    _clear_stats()
    return {"status": "cleared"}
