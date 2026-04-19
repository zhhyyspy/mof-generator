"""
Unified LLM client that routes to any configured provider.
All providers use either native SDK or OpenAI-compatible API.
"""
from __future__ import annotations

import json
import asyncio
from typing import Optional

from backend.models.llm_config import LLMProviderConfig


class LLMClient:
    """Unified interface for calling any configured LLM provider."""

    def __init__(self, config: LLMProviderConfig):
        self.config = config

    async def chat(
        self,
        system: str,
        user: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout_override: Optional[int] = None,
        purpose: str = "",
    ) -> str:
        """Send a chat request and return the text response. Tracks stats."""
        import time as _time
        from backend.services.llm_stats import record_call

        temp = temperature if temperature is not None else self.config.temperature
        tokens = max_tokens or self.config.max_tokens
        timeout = timeout_override or self.config.timeout
        prompt_chars = len(system) + len(user)
        start = _time.time()

        try:
            if self.config.provider == "anthropic":
                result = await self._call_anthropic(system, user, temp, tokens, timeout)
            else:
                result = await self._call_openai_compatible(system, user, temp, tokens, timeout)

            duration = _time.time() - start
            record_call(
                provider=self.config.provider, model=self.config.model,
                prompt_chars=prompt_chars, response_chars=len(result),
                duration_s=duration, success=True, purpose=purpose,
            )
            return result
        except Exception as e:
            duration = _time.time() - start
            record_call(
                provider=self.config.provider, model=self.config.model,
                prompt_chars=prompt_chars, response_chars=0,
                duration_s=duration, success=False, error_msg=str(e), purpose=purpose,
            )
            err_str = str(e)
            provider_hint = f"[{self.config.name} | {self.config.provider} | {self.config.model}]"
            if "401" in err_str or "api_key" in err_str.lower() or "auth" in err_str.lower():
                raise RuntimeError(
                    f"LLM认证失败 {provider_hint}: API Key无效或已过期。"
                    f"请点击右上角「LLM」按钮检查配置。\n原始错误: {err_str[:200]}"
                ) from e
            elif "timeout" in err_str.lower() or "timed out" in err_str.lower():
                raise RuntimeError(
                    f"LLM请求超时 {provider_hint}: 请求超过{timeout}秒未响应。"
                    f"可尝试减少文档数量或在LLM配置中增大超时时间。"
                ) from e
            elif "429" in err_str or "rate" in err_str.lower():
                raise RuntimeError(
                    f"LLM限流 {provider_hint}: 请求过于频繁，请稍后重试。\n原始错误: {err_str[:200]}"
                ) from e
            elif "quota" in err_str.lower() or "balance" in err_str.lower() or "insufficient" in err_str.lower():
                raise RuntimeError(
                    f"LLM额度不足 {provider_hint}: 账户余额不足或配额已用完。\n原始错误: {err_str[:200]}"
                ) from e
            elif (
                "n_keep" in err_str
                or "n_ctx" in err_str
                or "context length" in err_str.lower()
                or "context_length" in err_str.lower()
                or "too long" in err_str.lower()
                or "maximum context" in err_str.lower()
                or "exceeds" in err_str.lower() and "token" in err_str.lower()
            ):
                current_batch = getattr(self.config, "batch_max_chars", 8000)
                suggested = max(2000, current_batch // 2)
                raise RuntimeError(
                    f"LLM上下文超限 {provider_hint}: 单次提交的内容超出模型可处理的上下文长度。\n"
                    f"解决: 打开「LLM」设置 → 编辑当前配置 → 将「单批字符上限」从 {current_batch} 调小到约 {suggested}（或更小）后重试。\n"
                    f"说明: 跨批次的上下文关联由系统自动传递（已识别的实体/属性/关联会作为提示注入后续批次），"
                    f"降低此值不会导致模型'忘记'之前发现的内容。\n"
                    f"原始错误: {err_str[:300]}"
                ) from e
            else:
                raise RuntimeError(
                    f"LLM调用失败 {provider_hint}: {err_str[:300]}"
                ) from e

    async def test_connection(self) -> dict:
        """Test connectivity. Returns {"success": bool, "message": str, "model": str}."""
        try:
            response = await self.chat(
                system="You are a helpful assistant.",
                user="Reply with exactly: OK",
                temperature=0,
                max_tokens=10,
            )
            return {
                "success": True,
                "message": f"连接成功！模型响应: {response.strip()[:50]}",
                "model": self.config.model,
                "provider": self.config.provider,
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"连接失败: {str(e)}",
                "model": self.config.model,
                "provider": self.config.provider,
            }

    # ---- Anthropic native SDK ----

    async def _call_anthropic(self, system: str, user: str, temp: float, max_tokens: int, timeout: int = 120) -> str:
        import anthropic

        client = anthropic.Anthropic(
            api_key=self.config.api_key,
            base_url=self.config.base_url or "https://api.anthropic.com",
            timeout=timeout,
        )

        response = await asyncio.to_thread(
            client.messages.create,
            model=self.config.model,
            max_tokens=max_tokens,
            temperature=temp,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return response.content[0].text

    # ---- OpenAI-compatible API (covers OpenAI, DeepSeek, Zhipu, Moonshot, Qwen, Ollama, Azure, custom) ----

    async def _call_openai_compatible(self, system: str, user: str, temp: float, max_tokens: int, timeout: int = 120) -> str:
        from openai import OpenAI

        base_url = (self.config.base_url or "").strip()
        # Auto-clean: OpenAI SDK expects base URL ending at /v1, it adds /chat/completions itself
        # Strip common suffixes users might mistakenly include
        for suffix in ("/chat/completions", "/completions", "/chat", "/"):
            if base_url.endswith(suffix):
                base_url = base_url[:-len(suffix)]
                break

        api_key = self.config.api_key or "not-needed"

        if self.config.provider == "azure_openai":
            from openai import AzureOpenAI
            api_version = (self.config.extra_headers or {}).get("api_version", "2024-06-01")
            client = AzureOpenAI(
                api_key=api_key,
                azure_endpoint=base_url,
                api_version=api_version,
                timeout=timeout,
            )
        else:
            client = OpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=timeout,
            )

        response = await asyncio.to_thread(
            client.chat.completions.create,
            model=self.config.model,
            max_tokens=max_tokens,
            temperature=temp,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )

        # Defensive response parsing (local LLMs sometimes return non-standard shapes)
        if not response:
            raise RuntimeError(f"LLM返回空响应 (base_url={base_url}, model={self.config.model})")
        if not response.choices:
            raise RuntimeError(f"LLM响应缺少choices字段 (原始响应: {str(response)[:300]})")
        choice = response.choices[0]
        if not choice or not choice.message:
            raise RuntimeError(f"LLM响应choice格式异常: {str(choice)[:300]}")
        content = choice.message.content
        finish_reason = getattr(choice, 'finish_reason', 'unknown')
        reasoning = getattr(choice.message, 'reasoning_content', None)

        # Check: empty/None content (推理模型把token全用在思考上的典型症状)
        if not content:  # covers None AND empty string
            # Try reasoning_content fallback (embedded JSON in thinking text)
            if reasoning:
                # If finish_reason=length, reasoning got cut off — JSON likely incomplete
                if finish_reason == "length":
                    raise RuntimeError(
                        f"推理模型token耗尽！\n"
                        f"模型 '{self.config.model}' 是推理模型(reasoning model)，消耗了 "
                        f"{getattr(response.usage, 'completion_tokens', '?')} tokens 全部用于思考，"
                        f"没剩余token写正式答案。\n\n"
                        f"解决方案:\n"
                        f"1. 推荐: 在LM Studio中关闭 'Thinking Mode' / 禁用推理\n"
                        f"2. 或将LLM配置的 max_tokens 增加到 20000+\n"
                        f"3. 或换用非推理模型(如普通的 qwen2.5-32b 而非 qwen3.6)"
                    )
                # Reasoning finished but content empty — try extracting from reasoning
                content = reasoning
            elif hasattr(response, 'content'):
                content = response.content
            else:
                raise RuntimeError(
                    f"LLM返回空content (finish_reason={finish_reason}). "
                    f"可能原因: 1) 本地LLM未正确响应; 2) 提示被过滤; 3) 模型名'{self.config.model}'不匹配; "
                    f"4) base_url应为 http://localhost:1234/v1 (不含/chat/completions)"
                )

        # Even if content is non-empty, warn if hit length limit
        if finish_reason == "length":
            import sys
            print(f"[WARN] LLM response truncated (finish_reason=length). "
                  f"Consider increasing max_tokens beyond {max_tokens}", file=sys.stderr)

        return content


def get_active_client() -> LLMClient:
    """Get client for the currently active LLM provider."""
    from backend.storage.file_store import store
    config = store.get_active_llm_config()
    if config is None:
        raise RuntimeError("未配置LLM大模型。请先在设置中配置并激活一个大模型。")
    return LLMClient(config)
