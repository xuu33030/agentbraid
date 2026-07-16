from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, TypeVar

from pydantic import BaseModel

_REDACTED = "[REDACTED]"
_SENSITIVE_KEY = re.compile(
    r"(?i)(?:^|[_-])(?:access[_-]?key|api[_-]?key|auth|authorization|client[_-]?secret|"
    r"credentials?|jwt|passphrase|password|private[_-]?key|secret|token|xauthority)(?:$|[_-])"
)
_ASSIGNMENT = re.compile(
    r"(?i)(access[_-]?key|api[_-]?key|authorization|client[_-]?secret|credential|"
    r"passphrase|password|private[_-]?key|secret|token)"
    r"([\"']?\s*[:=]\s*[\"']?)([^\s,;\"'}]+)"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
_CREDENTIAL_URL = re.compile(r"(?i)(https?://)([^/@\s:]+):([^/@\s]+)@")
_KNOWN_TOKEN = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{16,}|"
    r"AIza[A-Za-z0-9_-]{20,}|ya29\.[A-Za-z0-9._-]{16,})\b"
)
_JWT = re.compile(r"\b[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{16,}\b")
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [^-\n]*PRIVATE KEY-----.*?-----END [^-\n]*PRIVATE KEY-----",
    re.DOTALL,
)

ModelT = TypeVar("ModelT", bound=BaseModel)


def redact_text(value: str) -> str:
    redacted = _PRIVATE_KEY.sub(_REDACTED, value)
    redacted = _CREDENTIAL_URL.sub(r"\1[REDACTED]@", redacted)
    redacted = _BEARER.sub("Bearer [REDACTED]", redacted)
    redacted = _KNOWN_TOKEN.sub(_REDACTED, redacted)
    redacted = _JWT.sub(_REDACTED, redacted)
    return _ASSIGNMENT.sub(lambda match: f"{match.group(1)}{match.group(2)}{_REDACTED}", redacted)


def redact_value(value: Any, *, key: str | None = None) -> Any:
    if key is not None and _SENSITIVE_KEY.search(key):
        return _REDACTED
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {
            item_key: redact_value(item_value, key=str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [redact_value(item) for item in value]
    return value


def redact_model(model: ModelT) -> ModelT:
    payload = redact_value(model.model_dump(mode="json"))
    return type(model).model_validate(payload)


def is_sensitive_environment_name(name: str) -> bool:
    return bool(_SENSITIVE_KEY.search(name))
