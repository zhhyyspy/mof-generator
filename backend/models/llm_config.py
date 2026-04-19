"""LLM provider configuration models."""
from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field


class LLMProviderConfig(BaseModel):
    """A saved LLM provider configuration."""
    id: str
    name: str                                  # Display name, e.g. "My GPT-4o"
    provider: str                              # anthropic | openai | azure_openai | deepseek | zhipu | moonshot | qwen | ollama | custom
    api_key: str = ""                          # API key (stored locally only)
    base_url: Optional[str] = None             # Custom endpoint URL (required for azure, ollama, custom)
    model: str = ""                            # Model ID, e.g. "gpt-4o", "claude-sonnet-4-20250514"
    temperature: float = 0.0
    max_tokens: int = 4096
    top_p: float = 1.0
    timeout: int = 120                         # Request timeout in seconds
    is_active: bool = False                    # Currently selected provider
    extra_headers: Optional[dict] = None       # Additional headers (e.g. Azure api-version)
    notes: Optional[str] = None                # User notes
    # Extraction batch size: max characters of document content per AI call.
    # Smaller values work around models with inefficient tokenization (e.g. some local models
    # that produce 10+ tokens per Chinese char). Cross-batch context is still preserved
    # via context-hint passing — chunking only affects how docs are split, not context association.
    # Recommended: 8000 (local LLMs / small context), 20000 (cloud LLMs / large context).
    batch_max_chars: int = 8000


class LLMProviderList(BaseModel):
    providers: list[LLMProviderConfig] = Field(default_factory=list)


# Provider presets: common defaults for quick setup
PROVIDER_PRESETS = {
    "anthropic": {
        "label": "Anthropic (Claude)",
        "default_base_url": "https://api.anthropic.com",
        "models": [
            "claude-opus-4-20250514",
            "claude-sonnet-4-20250514",
            "claude-haiku-4-5-20251001",
        ],
        "default_model": "claude-sonnet-4-20250514",
    },
    "openai": {
        "label": "OpenAI",
        "default_base_url": "https://api.openai.com/v1",
        "models": [
            "gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-4", "o1", "o1-mini", "o3-mini",
        ],
        "default_model": "gpt-4o",
    },
    "azure_openai": {
        "label": "Azure OpenAI",
        "default_base_url": "",
        "placeholder_url": "https://{resource}.openai.azure.com/openai/deployments/{deployment}",
        "models": [],
        "default_model": "",
        "extra_fields": ["api_version"],
    },
    "deepseek": {
        "label": "DeepSeek",
        "default_base_url": "https://api.deepseek.com/v1",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "default_model": "deepseek-chat",
    },
    "zhipu": {
        "label": "智谱 (GLM)",
        "default_base_url": "https://open.bigmodel.cn/api/paas/v4",
        "models": ["glm-4-plus", "glm-4", "glm-4-flash"],
        "default_model": "glm-4-plus",
    },
    "moonshot": {
        "label": "月之暗面 (Kimi)",
        "default_base_url": "https://api.moonshot.cn/v1",
        "models": ["moonshot-v1-128k", "moonshot-v1-32k", "moonshot-v1-8k"],
        "default_model": "moonshot-v1-32k",
    },
    "qwen": {
        "label": "通义千问 (Qwen)",
        "default_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": ["qwen-max", "qwen-plus", "qwen-turbo"],
        "default_model": "qwen-plus",
    },
    "ollama": {
        "label": "Ollama (本地)",
        "default_base_url": "http://localhost:11434/v1",
        "models": [],
        "default_model": "",
        "no_api_key": True,
    },
    "custom": {
        "label": "自定义 (OpenAI兼容)",
        "default_base_url": "",
        "models": [],
        "default_model": "",
    },
}
