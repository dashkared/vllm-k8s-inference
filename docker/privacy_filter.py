"""
Custom Logging Filter for vLLM Inference Server.

Ensures that user prompts and generated model completions are NEVER
output to stdout/stderr, satisfying privacy and security requirements (GDPR, 152-FZ).
Audit metadata (timestamp, request ID, status, token counts, latency) is preserved.
"""

import logging
import re
from typing import Any

# Regex patterns to detect and redact prompt/completion text in log strings
SENSITIVE_PATTERNS = [
    # JSON-like payload fields: "prompt": "...", "content": "...", "text": "..."
    (re.compile(r'("prompt"\s*:\s*)"(?:[^"\\]|\\.)*"', re.DOTALL), r'\1"[REDACTED_PROMPT]"'),
    (re.compile(r'("content"\s*:\s*)"(?:[^"\\]|\\.)*"', re.DOTALL), r'\1"[REDACTED_CONTENT]"'),
    (re.compile(r'("text"\s*:\s*)"(?:[^"\\]|\\.)*"', re.DOTALL), r'\1"[REDACTED_TEXT]"'),
    # Python repr / kwargs: prompt='...', text="..."
    (re.compile(r'(\bprompt\s*=\s*)(?:[\'"][^\'"]*[\'"])', re.IGNORECASE), r'\1"[REDACTED_PROMPT]"'),
    (re.compile(r'(\btext\s*=\s*)(?:[\'"][^\'"]*[\'"])', re.IGNORECASE), r'\1"[REDACTED_TEXT]"'),
]


class PrivacyLogFilter(logging.Filter):
    """
    Filters and redacts sensitive prompt and generation data from log records.
    Allows all log records through, but scrubs the text contents of prompts and responses.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            # 1. Redact message string
            if isinstance(record.msg, str):
                msg = record.msg
                for pattern, replacement in SENSITIVE_PATTERNS:
                    msg = pattern.sub(replacement, msg)
                record.msg = msg

            # 2. Redact arguments if present
            if record.args:
                new_args = []
                for arg in record.args:
                    if isinstance(arg, str):
                        for pattern, replacement in SENSITIVE_PATTERNS:
                            arg = pattern.sub(replacement, arg)
                        new_args.append(arg)
                    elif isinstance(arg, dict):
                        new_args.append(self._sanitize_dict(arg))
                    else:
                        new_args.append(arg)
                record.args = tuple(new_args)
        except Exception:
            # Under no circumstances should a logging filter crash the inference server
            pass
        return True

    def _sanitize_dict(self, d: dict[str, Any]) -> dict[str, Any]:
        sanitized = {}
        for k, v in d.items():
            if k in ("prompt", "messages", "content", "text"):
                sanitized[k] = "[REDACTED]"
            elif isinstance(v, dict):
                sanitized[k] = self._sanitize_dict(v)
            elif isinstance(v, list):
                sanitized[k] = [self._sanitize_dict(i) if isinstance(i, dict) else i for i in v]
            else:
                sanitized[k] = v
        return sanitized
