from __future__ import annotations

import configparser
import json
import re
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .errors import BadRequest, UnsupportedFormat

SUPPORTED_FORMATS = ("yaml", "json", "dotenv", "ini")

_DOTENV_KEY_RE = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_.-]*)\s*=(.*)$")
_INI_SECTION_RE = re.compile(r"^\s*\[[^\]]+\]\s*$")


@dataclass(frozen=True)
class FormatInfo:
    name: str
    language: str
    tree: bool


def _extension(path: Path) -> str:
    return path.suffix.lower()


def _looks_like_json(text: str) -> bool:
    try:
        json.loads(text)
        return True
    except Exception:
        return False


def detect_format(path: Path, text: str | None = None) -> FormatInfo:
    name = path.name.lower()
    ext = _extension(path)
    fmt: str | None = None
    if ext in (".yaml", ".yml"):
        fmt = "yaml"
    elif ext == ".json":
        fmt = "json"
    elif name == ".env" or name.startswith(".env.") or ext in (".env", ".dotenv"):
        fmt = "dotenv"
    elif ext in (".ini", ".cfg", ".conf"):
        fmt = "ini"
    elif text is not None:
        stripped = text.lstrip("\ufeff \t\r\n")
        if stripped and stripped[0] in "{[" and _looks_like_json(text):
            fmt = "json"
        else:
            meaningful = [
                line.strip()
                for line in text.splitlines()
                if line.strip() and not line.lstrip().startswith(("#", ";"))
            ]
            if meaningful and all(_DOTENV_KEY_RE.match(line) for line in meaningful):
                fmt = "dotenv"
            elif any(_INI_SECTION_RE.match(line) for line in meaningful):
                fmt = "ini"
            else:
                fmt = "yaml"
    return format_info(fmt or "yaml")


def format_info(fmt: str) -> FormatInfo:
    if fmt == "yaml":
        return FormatInfo("yaml", "yaml", True)
    if fmt == "json":
        return FormatInfo("json", "application/json", True)
    if fmt == "dotenv":
        return FormatInfo("dotenv", "text/x-properties", False)
    if fmt == "ini":
        return FormatInfo("ini", "text/x-properties", False)
    raise UnsupportedFormat(f"Формат {fmt!r} не поддерживается")


def _parse_dotenv(text: str) -> OrderedDict[str, str]:
    result: OrderedDict[str, str] = OrderedDict()
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", ";")):
            continue
        match = _DOTENV_KEY_RE.match(line)
        if not match:
            raise BadRequest(f"Строка {number}: ожидается KEY=VALUE")
        key, value = match.groups()
        if key in result:
            raise BadRequest(f"Строка {number}: повторяющийся ключ {key}")
        result[key] = value
    return result


def _parse_ini(text: str) -> dict[str, Any]:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str  # type: ignore[assignment]
    try:
        parser.read_string(text)
    except configparser.Error as exc:
        raise BadRequest(f"Некорректный INI: {exc}") from exc
    return {section: dict(parser.items(section)) for section in parser.sections()}


def parse_document(text: str, fmt: str) -> Any:
    if fmt == "yaml":
        try:
            return yaml.safe_load(text)
        except yaml.YAMLError as exc:
            mark = getattr(exc, "problem_mark", None)
            where = f" (строка {mark.line + 1}, столбец {mark.column + 1})" if mark else ""
            problem = getattr(exc, "problem", None) or "синтаксическая ошибка"
            raise BadRequest(f"Некорректный YAML{where}: {problem}") from exc
    if fmt == "json":
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise BadRequest(f"Некорректный JSON (строка {exc.lineno}, столбец {exc.colno}): {exc.msg}") from exc
    if fmt == "dotenv":
        return _parse_dotenv(text)
    if fmt == "ini":
        return _parse_ini(text)
    raise UnsupportedFormat(f"Формат {fmt!r} не поддерживается")


def validate_text(text: str, fmt: str) -> None:
    if not text.strip():
        raise BadRequest("Пустой документ")
    parse_document(text, fmt)


def semantic_equal(left: str, right: str, fmt: str) -> bool:
    if fmt in ("dotenv", "ini"):
        # Comments and spacing are not semantic for these formats.
        try:
            return parse_document(left, fmt) == parse_document(right, fmt)
        except BadRequest:
            return left == right
    try:
        return parse_document(left, fmt) == parse_document(right, fmt)
    except BadRequest:
        return False


_ENVELOPE_PATTERNS = (
    ("dotenv", re.compile(r"^sops_[A-Za-z0-9_]+=", re.MULTILINE)),
    ("ini", re.compile(r"^\s*\[sops\]\s*$", re.MULTILINE)),
    ("json", re.compile(r'^\s*"sops"\s*:\s*\{', re.MULTILINE)),
    ("yaml", re.compile(r"^sops\s*:", re.MULTILINE)),
)


def guess_encrypted_format(text: str) -> str | None:
    """Detect the SOPS envelope format from its plaintext metadata markers.

    Works without any key and does not depend on the file name, so files such
    as ``.env_enc.test`` or ``secrets.backup`` are recognised as dotenv.
    """
    for name, pattern in _ENVELOPE_PATTERNS:
        if pattern.search(text):
            return name
    return None


def summarize_sops_metadata(encrypted_text: str, fmt: str) -> dict[str, Any]:
    """Public metadata only: key types and public recipients."""
    summary: dict[str, Any] = {
        "key_types": [],
        "age_recipients": [],
        "pgp_fingerprints": [],
        "lastmodified": None,
        "version": None,
        "encrypted_regex": None,
    }
    meta: dict[str, Any] | None = None
    if fmt in ("yaml", "json"):
        try:
            data = parse_document(encrypted_text, fmt)
        except BadRequest:
            data = None
        if isinstance(data, dict) and isinstance(data.get("sops"), dict):
            meta = data["sops"]
    elif fmt == "ini":
        try:
            parsed = _parse_ini(encrypted_text)
        except BadRequest:
            parsed = {}
        meta = parsed.get("sops") if isinstance(parsed, dict) else None
        flat = {str(k): str(v) for k, v in (meta or {}).items()}
        prefixes = ("age__", "pgp__", "kms__", "gcp_kms__", "azure_kv__", "hc_vault__", "hckms__")
        summary["key_types"] = sorted(
            {key.split("__", 1)[0] for key in flat if key.startswith(prefixes)}
        )
        summary["age_recipients"] = sorted(
            value for key, value in flat.items() if key.endswith("__map_recipient")
        )
        summary["pgp_fingerprints"] = sorted(
            value for key, value in flat.items() if key.endswith("__map_fp")
        )
        summary["lastmodified"] = flat.get("lastmodified")
        summary["version"] = flat.get("version")
        summary["encrypted_regex"] = flat.get("encrypted_regex")
        summary["unencrypted_suffix"] = flat.get("unencrypted_suffix")
        return summary
    elif fmt == "dotenv":
        flat: dict[str, str] = {}
        for line in encrypted_text.splitlines():
            match = _DOTENV_KEY_RE.match(line)
            if match and match.group(1).startswith("sops_"):
                flat[match.group(1)] = match.group(2)
        key_types = sorted(
            {
                key[len("sops_") :].split("__", 1)[0]
                for key in flat
                if key.startswith("sops_") and "__list_" in key
            }
        )
        summary["key_types"] = key_types
        summary["age_recipients"] = sorted(
            value for key, value in flat.items() if key.endswith("__map_recipient")
        )
        summary["lastmodified"] = flat.get("sops_lastmodified")
        summary["version"] = flat.get("sops_version")
        return summary

    if not meta:
        return summary

    for key in ("age", "pgp", "kms", "gcp_kms", "azure_kv", "hc_vault", "hckms"):
        if meta.get(key):
            summary["key_types"].append(key)
    for item in meta.get("age") or []:
        if isinstance(item, dict) and item.get("recipient"):
            summary["age_recipients"].append(str(item["recipient"]))
    for item in meta.get("pgp") or []:
        if isinstance(item, dict) and item.get("fp"):
            summary["pgp_fingerprints"].append(str(item["fp"]))
    summary["lastmodified"] = meta.get("lastmodified")
    summary["version"] = meta.get("version")
    summary["encrypted_regex"] = meta.get("encrypted_regex")
    summary["unencrypted_suffix"] = meta.get("unencrypted_suffix")
    return summary
