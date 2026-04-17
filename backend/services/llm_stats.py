"""LLM call statistics tracking and aggregation."""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Optional

from backend.config import settings

_STATS_FILE = settings.data_dir / "llm_stats.json"
_lock = Lock()


def _load() -> list[dict]:
    if _STATS_FILE.exists():
        try:
            return json.loads(_STATS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _save(records: list[dict]):
    _STATS_FILE.write_text(json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")


def record_call(
    provider: str,
    model: str,
    prompt_chars: int,
    response_chars: int,
    duration_s: float,
    success: bool,
    error_msg: Optional[str] = None,
    purpose: str = "",
):
    """Record a single LLM call."""
    entry = {
        "timestamp": datetime.now().isoformat(),
        "provider": provider,
        "model": model,
        "prompt_chars": prompt_chars,
        "response_chars": response_chars,
        "duration_s": round(duration_s, 2),
        "success": success,
        "error": error_msg[:200] if error_msg else None,
        "purpose": purpose,
    }
    with _lock:
        records = _load()
        records.append(entry)
        # Keep max 500 records
        if len(records) > 500:
            records = records[-500:]
        _save(records)


def get_stats() -> dict:
    """Aggregate statistics from recorded calls."""
    records = _load()
    if not records:
        return {
            "total_calls": 0, "success_calls": 0, "failed_calls": 0,
            "success_rate": 0, "total_prompt_chars": 0, "total_response_chars": 0,
            "estimated_tokens": 0, "avg_duration_s": 0, "total_duration_s": 0,
            "by_model": {}, "by_provider": {}, "hourly_trend": [],
            "recent_calls": [],
        }

    total = len(records)
    successes = [r for r in records if r.get("success")]
    failures = [r for r in records if not r.get("success")]
    total_prompt = sum(r.get("prompt_chars", 0) for r in records)
    total_response = sum(r.get("response_chars", 0) for r in records)
    durations = [r.get("duration_s", 0) for r in records if r.get("duration_s")]

    # By model
    by_model = {}
    for r in records:
        m = r.get("model", "unknown")
        if m not in by_model:
            by_model[m] = {"calls": 0, "success": 0, "total_duration": 0, "total_chars": 0}
        by_model[m]["calls"] += 1
        if r.get("success"):
            by_model[m]["success"] += 1
        by_model[m]["total_duration"] += r.get("duration_s", 0)
        by_model[m]["total_chars"] += r.get("prompt_chars", 0) + r.get("response_chars", 0)

    # By provider
    by_provider = {}
    for r in records:
        p = r.get("provider", "unknown")
        if p not in by_provider:
            by_provider[p] = {"calls": 0, "success": 0}
        by_provider[p]["calls"] += 1
        if r.get("success"):
            by_provider[p]["success"] += 1

    # Hourly trend (last 24 hours)
    from collections import Counter
    hourly = Counter()
    for r in records:
        try:
            ts = datetime.fromisoformat(r["timestamp"])
            hour_key = ts.strftime("%m-%d %H:00")
            hourly[hour_key] += 1
        except Exception:
            pass
    hourly_trend = [{"hour": k, "calls": v} for k, v in sorted(hourly.items())[-24:]]

    return {
        "total_calls": total,
        "success_calls": len(successes),
        "failed_calls": len(failures),
        "success_rate": round(len(successes) / total * 100, 1) if total else 0,
        "total_prompt_chars": total_prompt,
        "total_response_chars": total_response,
        "estimated_tokens": (total_prompt + total_response) // 2,  # rough estimate for Chinese
        "avg_duration_s": round(sum(durations) / len(durations), 1) if durations else 0,
        "total_duration_s": round(sum(durations), 1),
        "by_model": by_model,
        "by_provider": by_provider,
        "hourly_trend": hourly_trend,
        "recent_calls": records[-20:][::-1],  # Last 20, newest first
    }


def clear_stats():
    with _lock:
        _save([])
