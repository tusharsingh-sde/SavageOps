"""Small, deterministic safeguards for untrusted log and model supplied text."""

import re
from typing import Any

_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+"),
    re.compile(r"(?i)(api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret)(\s*[:=]\s*)['\"]?[^\s,;'\"]+"),
    re.compile(r"\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9]{20,})\b"),
    re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    re.compile(r"(?<!\w)\+?\d[\d ().-]{8,}\d(?!\w)"),
)


def redact_untrusted_text(text: str) -> str:
    """Redact common credentials and email addresses before external LLM/API calls."""
    result = text
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub("[REDACTED]", result)
    return result


def redact_untrusted_data(value: Any) -> Any:
    """Recursively redact string values while preserving alert payload structure."""
    if isinstance(value, str):
        return redact_untrusted_text(value)
    if isinstance(value, list):
        return [redact_untrusted_data(item) for item in value]
    if isinstance(value, dict):
        return {key: redact_untrusted_data(item) for key, item in value.items()}
    return value


def traceback_context(logs: str, max_chars: int = 12000) -> str:
    """Keep a complete first traceback and nearby lines, collapsing repeated noise."""
    lines = logs.splitlines()
    if not lines:
        return ""
    collapsed: list[str] = []
    previous = None
    repeat_count = 0
    for line in lines:
        if line == previous:
            repeat_count += 1
            continue
        if repeat_count:
            collapsed.append(f"[previous line repeated {repeat_count} times]")
            repeat_count = 0
        collapsed.append(line)
        previous = line
    if repeat_count:
        collapsed.append(f"[previous line repeated {repeat_count} times]")

    starts = [i for i, line in enumerate(collapsed) if "Traceback (most recent call last):" in line]
    if starts:
        start = starts[0]
        next_trace = next((i for i in starts if i > start), len(collapsed))
        selected = collapsed[max(0, start - 12):min(next_trace, start + 200)]
        while len("\n".join(selected)) > max_chars and len(selected) > 1:
            selected.pop(0)
        return "\n".join(selected)
    joined = "\n".join(collapsed)
    return joined[-max_chars:]
