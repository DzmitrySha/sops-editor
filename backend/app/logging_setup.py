from __future__ import annotations

import re
import sys
from pathlib import Path

from loguru import logger

_SECRET_PATTERNS = [
    re.compile(r"AGE-SECRET-KEY-1[0-9A-Z]+"),
    re.compile(r"ENC\[[^\]]*\]"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
]
_URL_CREDENTIALS_RE = re.compile(r"(https?://)[^/\s:@]+:[^@\s]+@", re.I)


def redact(text: str) -> str:
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[redacted]", text)
    text = _URL_CREDENTIALS_RE.sub(r"\1[redacted]@", text)
    return text


def sanitize_stderr(raw: bytes | str, limit: int = 1200) -> str:
    if isinstance(raw, bytes):
        text = raw.decode("utf-8", errors="replace")
    else:
        text = raw
    text = redact(text)
    text = " ".join(text.split())
    return text[:limit]


def setup_logging(level: str = "INFO", log_file: Path | None = None) -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        level=level.upper(),
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | {message}",
        colorize=False,
    )
    if log_file is not None:
        try:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            logger.add(
                log_file,
                level=level.upper(),
                rotation="5 MB",
                retention=3,
                encoding="utf-8",
                format="{time:YYYY-MM-DD HH:mm:ss} | {level: <7} | {message}",
            )
        except OSError:
            logger.warning("Не удалось открыть файл логов {}", log_file)
