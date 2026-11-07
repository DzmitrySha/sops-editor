from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import secrets
import shutil
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
from pydantic import BaseModel, Field

from .config import Settings
from .editor_service import EditorService
from .errors import (
    AppError,
    BadRequest,
    Forbidden,
    PayloadTooLarge,
    SopsFailure,
)
from .files import cleanup_uploads, read_bounded, write_atomic
from .git_service import GitService
from .logging_setup import setup_logging
from .ops import OperationRegistry
from .security import PathGuard
from .sessions import SessionStore
from .sops_service import KeyMaterial, SopsRunner

_FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"

# --------------------------------------------------------------------- models
class SaveRequest(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    content: str
    format: str = Field(min_length=1, max_length=16)
    expected_sha256: str = Field(min_length=64, max_length=64)


class KeyRequest(BaseModel):
    key: str = Field(min_length=1, max_length=64 * 1024)


class CommitRequest(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    message: str = Field(min_length=1, max_length=500)
    push: bool = False


class CloneRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2000)
    branch: str | None = Field(default=None, max_length=200)
    name: str | None = Field(default=None, max_length=80)


class PushRequest(BaseModel):
    path: str = Field(min_length=1, max_length=4096)


class EncryptRequest(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    recipients: list[str] = Field(default_factory=list)


class PullRequest(BaseModel):
    path: str = Field(min_length=1, max_length=4096)


# ------------------------------------------------------------------- helpers
def _error_response(exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            }
        },
    )


def _session_cookie(request: Request, settings: Settings) -> str | None:
    return request.cookies.get("sops_sid")


def _is_secure_request(request: Request, settings: Settings) -> bool:
    if settings.cookie_secure:
        return True
    if request.url.scheme == "https":
        return True
    if settings.trust_proxy:
        proto = request.headers.get("x-forwarded-proto", "")
        return proto.split(",")[0].strip().lower() == "https"
    return False


def _effective_host(request: Request, settings: Settings) -> str | None:
    host = request.headers.get("host")
    if settings.trust_proxy:
        host = request.headers.get("x-forwarded-host", host)
    return host


def _set_session_cookie(response: Response, request: Request, settings: Settings, sid: str) -> None:
    response.set_cookie(
        "sops_sid",
        sid,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        samesite="strict",
        secure=_is_secure_request(request, settings),
        path="/",
    )


def render_help(frontend_dir: Path, filename: str = "help.html") -> str | None:
    """Serve the standalone help page with styles inlined: one request only."""
    help_path = frontend_dir / filename
    try:
        html = help_path.read_text(encoding="utf-8")
    except OSError:
        return None
    marker = "<!--INLINE_STYLES-->"
    if marker not in html:
        return html
    try:
        css = (frontend_dir / "styles.css").read_text(encoding="utf-8")
    except OSError:
        css = ""
    return html.replace(marker, f"<style>\n{css}\n</style>")


def _safe_upload_name(name: str, fallback: str = "secrets.enc.yaml") -> str:
    safe_name = Path(name).name.strip()
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", safe_name)[:120]
    if safe_name in ("", ".", "..") or safe_name.lower() == ".git":
        return fallback
    return safe_name


def _set_upload_cookie(response: Response, request: Request, settings: Settings, upload_id: str) -> None:
    response.set_cookie(
        "sops_upload",
        upload_id,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        samesite="strict",
        secure=_is_secure_request(request, settings),
        path="/",
    )


def _ambient_key_source() -> str:
    if os.environ.get("SOPS_AGE_KEY"):
        return "environment"
    if any(
        os.environ.get(name)
        for name in (
            "SOPS_PGP_FP",
            "SOPS_KMS_ARN",
            "SOPS_GCP_KMS_IDS",
            "SOPS_AZURE_KEYVAULT_URLS",
            "SOPS_VAULT_URIS",
        )
    ):
        return "environment"
    return "none"


def _key_material(request: Request, settings: Settings, sessions: SessionStore) -> KeyMaterial:
    sid = _session_cookie(request, settings)
    session = sessions.get(sid)
    if session is not None:
        return KeyMaterial(label="session", inline=session.key)
    key_file = settings.effective_age_key_file
    if key_file is not None:
        return KeyMaterial(label="server_file", file=key_file)
    default_key = Path.home() / ".config" / "sops" / "age" / "keys.txt"
    if default_key.is_file():
        return KeyMaterial(label="server_default", file=default_key)
    return KeyMaterial(label=_ambient_key_source())


def _server_key_file(settings: Settings) -> Path | None:
    key_file = settings.effective_age_key_file
    if key_file is not None:
        return key_file
    default_key = Path.home() / ".config" / "sops" / "age" / "keys.txt"
    return default_key if default_key.is_file() else None


def _key_status(
    request: Request,
    settings: Settings,
    sessions: SessionStore,
    sops: SopsRunner,
) -> dict[str, Any]:
    sid = _session_cookie(request, settings)
    session = sessions.get(sid)
    key_file = settings.effective_age_key_file
    ambient = _ambient_key_source()
    if session is not None:
        source = "session"
        recipients = session.recipients
    elif key_file is not None:
        source = "server_file"
        recipients = sops.public_recipients_from_file(key_file)
    else:
        default_key = Path.home() / ".config" / "sops" / "age" / "keys.txt"
        if default_key.is_file():
            source = "server_default"
            recipients = sops.public_recipients_from_file(default_key)
        else:
            source = ambient
            recipients = []
    return {
        "source": source,
        "configured": source != "none",
        "session": session is not None,
        "recipients": recipients,
        "server_key_file": str(key_file) if key_file is not None else None,
        "auth_enabled": settings.auth_enabled,
    }


_AGE_RECIPIENT_RE = re.compile(r"^age1[0-9a-z]{20,}$")


def _clean_recipients(raw: list[str] | str) -> list[str]:
    items = [raw] if isinstance(raw, str) else list(raw or [])
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        for part in re.split(r"[,\s]+", str(item).strip()):
            if not part:
                continue
            if not _AGE_RECIPIENT_RE.fullmatch(part):
                raise BadRequest(f"Некорректный age-получатель: {part[:40]}")
            if part not in seen:
                seen.add(part)
                result.append(part)
    if len(result) > 20:
        raise BadRequest("Слишком много получателей (максимум 20)")
    return result


def _default_recipients(
    request: Request,
    settings: Settings,
    sessions: SessionStore,
    sops: SopsRunner,
) -> list[str]:
    sid = _session_cookie(request, settings)
    session = sessions.get(sid)
    if session is not None:
        return list(session.recipients)
    key_file = _server_key_file(settings)
    if key_file is not None:
        return sops.public_recipients_from_file(key_file)
    return []


def _likely_sops(path: Path) -> bool:
    try:
        if path.stat().st_size > 2 * 1024 * 1024:
            return False
        with open(path, "rb") as handle:
            head = handle.read(256 * 1024)
    except OSError:
        return False
    return b"ENC[AES256_GCM" in head or b"sops_" in head or b"sops:" in head or b'"sops"' in head


def _entry_payload(entry: Path, root: Path) -> dict[str, Any] | None:
    try:
        resolved = entry.resolve(strict=True)
    except OSError:
        return None
    if root not in resolved.parents and resolved != root:
        return None
    if resolved.name.lower() == ".git":
        return None
    try:
        stat = resolved.stat()
    except OSError:
        return None
    is_dir = resolved.is_dir()
    payload: dict[str, Any] = {
        "name": entry.name,
        "path": str(entry),
        "type": "dir" if is_dir else "file",
        "size": None if is_dir else stat.st_size,
        "mtime": round(stat.st_mtime, 3),
    }
    if not is_dir:
        payload["likely_sops"] = _likely_sops(resolved)
    return payload


_SKIP_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".idea",
    ".tox",
    "dist",
    "build",
    "target",
}


# ----------------------------------------------------------------- app factory
def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    storage = settings.ensure_storage()
    uploads_root = settings.uploads_path
    uploads_root.mkdir(parents=True, exist_ok=True)
    setup_logging(
        level=os.environ.get("SOPS_EDITOR_LOG_LEVEL", "INFO"),
        log_file=storage / "logs" / "sops-editor.log",
    )
    roots = list(settings.roots_list)
    uploads_resolved = uploads_root.resolve(strict=False)
    if not any(root.expanduser().resolve(strict=False) == uploads_resolved for root in roots):
        roots.append(uploads_root)
    # Cloned repositories live under the storage dir; allow browsing them even
    # when SOPS_EDITOR_ROOTS was not configured with an explicit /data/repos.
    if settings.allow_git_clone:
        repos_root = settings.storage_path / "repos"
        repos_resolved = repos_root.resolve(strict=False)
        if not any(root.expanduser().resolve(strict=False) == repos_resolved for root in roots):
            roots.append(repos_root)
    guard = PathGuard(roots)
    sessions = SessionStore(settings.session_ttl_seconds)
    ops = OperationRegistry()
    sops = SopsRunner(settings.sops_bin, settings.age_keygen_bin, settings.request_timeout)
    git = GitService(settings)
    editor = EditorService(settings, guard, sops, git)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info("SOPS Editor {} starting; roots={}", settings.version, [str(r) for r in guard.roots])
        if not settings.auth_enabled and (settings.trust_proxy or settings.cookie_secure):
            raise RuntimeError(
                "SOPS_EDITOR_AUTH_PASSWORD обязателен при SOPS_EDITOR_TRUST_PROXY/COOKIE_SECURE"
            )
        if not settings.auth_enabled:
            logger.warning("HTTP Basic auth отключён: задайте SOPS_EDITOR_AUTH_PASSWORD")
        if settings.effective_age_key_file is None and _ambient_key_source() == "none":
            logger.warning("Серверный age-ключ не найден: пользователь должен задать ключ в UI")
        try:
            logger.info("sops: {}", sops.version())
        except AppError as exc:
            logger.error("sops недоступен: {}", exc.message)
        yield

    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.guard = guard
    app.state.sessions = sessions
    app.state.sops = sops
    app.state.git = git
    app.state.editor = editor

    # -------------------------------------------------------------- middleware
    @app.middleware("http")
    async def security_middleware(request: Request, call_next):
        path = request.url.path
        started = time.monotonic()
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} or path.startswith("/api/"):
            logger.debug("--> {} {}", request.method, path)
        # HTTP Basic auth, except for the container health probe.
        if settings.auth_enabled and path != "/api/health":
            header = request.headers.get("authorization", "")
            valid = False
            if header.lower().startswith("basic "):
                try:
                    decoded = base64.b64decode(header[6:].strip(), validate=True).decode("utf-8")
                    user, _, password = decoded.partition(":")
                    valid = secrets.compare_digest(user, settings.auth_user) and secrets.compare_digest(
                        password, settings.auth_password.get_secret_value()  # type: ignore[union-attr]
                    )
                except Exception:
                    valid = False
            if not valid:
                response = JSONResponse(
                    status_code=401,
                    content={"error": {"code": "unauthorized", "message": "Требуется авторизация"}},
                )
                response.headers["WWW-Authenticate"] = 'Basic realm="SOPS Editor", charset="UTF-8"'
                return response

        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("origin")
            if origin:
                expected = _effective_host(request, settings)
                if not expected or urlsplit(origin).netloc.lower() != expected.lower():
                    return JSONResponse(
                        status_code=403,
                        content={"error": {"code": "bad_origin", "message": "Неверный Origin"}},
                    )

        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > settings.max_file_bytes * 2 + 1024 * 1024:
                    return _error_response(PayloadTooLarge())
            except ValueError:
                pass

        response = await call_next(request)
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} or path.startswith("/api/"):
            logger.debug(
                "<-- {} {} {} ms (first byte)",
                request.method,
                path,
                round((time.monotonic() - started) * 1000),
            )

        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
        )
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        )
        if _is_secure_request(request, settings):
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        if path.startswith("/api/") or path in (
            "/",
            "/app.js",
            "/styles.css",
            "/help.html",
            "/help.en.html",
            "/i18n.js",
        ):
            response.headers.setdefault("Cache-Control", "no-store")
        elif path.startswith("/vendor/"):
            response.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")
        elif response.status_code == 200:
            response.headers.setdefault("Cache-Control", "public, max-age=3600")
        return response

    # ---------------------------------------------------------------- handlers
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        logger.info("{} {} -> {} {}: {}", request.method, request.url.path, exc.status_code, exc.code, exc.message)
        return _error_response(exc)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        fields = []
        for item in exc.errors():
            location = ".".join(str(part) for part in item.get("loc", []) if part != "body")
            if location:
                fields.append(location)
        logger.info("{} {} -> 422 validation: {}", request.method, request.url.path, fields)
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "Некорректный запрос",
                    "details": {"fields": fields},
                }
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception):
        logger.exception("Unhandled error on {} {}: {}", request.method, request.url.path, type(exc).__name__)
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "internal", "message": "Внутренняя ошибка сервера"}},
        )

    def _ndjson(work):
        """Stream step events from a synchronous callable as NDJSON."""

        async def generator():
            queue: asyncio.Queue = asyncio.Queue()
            loop = asyncio.get_running_loop()

            def emit(phase: str, text: str) -> None:
                loop.call_soon_threadsafe(queue.put_nowait, {"phase": phase, "text": text})

            def runner() -> None:
                try:
                    result = work(emit)
                    loop.call_soon_threadsafe(queue.put_nowait, {"phase": "done", "file": result})
                except AppError as exc:
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        {
                            "phase": "error",
                            "status": exc.status_code,
                            "error": {
                                "code": exc.code,
                                "message": exc.message,
                                "details": exc.details,
                            },
                        },
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.exception("streamed operation failed: {}", type(exc).__name__)
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        {
                            "phase": "error",
                            "status": 500,
                            "error": {
                                "code": "internal",
                                "message": "Внутренняя ошибка сервера",
                                "details": {},
                            },
                        },
                    )

            threading.Thread(target=runner, daemon=True).start()
            while True:
                event = await queue.get()
                yield json.dumps(event, ensure_ascii=False) + "\n"
                if event["phase"] in ("done", "error"):
                    break

        return StreamingResponse(
            generator(),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    # ------------------------------------------------------------------- API
    @app.get("/api/health")
    async def health():
        try:
            version = sops.version()
        except AppError as exc:
            return JSONResponse(
                status_code=503,
                content={"status": "error", "version": settings.version, "sops": None, "error": exc.message},
            )
        return {"status": "ok", "version": settings.version, "sops": version}

    _OP_ID_RE = re.compile(r"[A-Za-z0-9_-]{8,64}")

    @app.get("/api/ops/{op_id}")
    async def operation_status(request: Request, op_id: str):
        if not _OP_ID_RE.fullmatch(op_id):
            raise BadRequest("Некорректный идентификатор операции")
        return ops.get(op_id)

    @app.get("/api/status")
    async def status(request: Request):
        return {
            "version": settings.version,
            "auth_enabled": settings.auth_enabled,
            "roots": [
                {
                    "path": str(root),
                    "exists": root.is_dir(),
                    "label": root.name or str(root),
                }
                for root in settings.roots_list
            ],
            "key": _key_status(request, settings, sessions, sops),
            "allow_git_clone": settings.allow_git_clone,
            "git_token_configured": bool(settings.git_token and settings.git_token.get_secret_value()),
            "max_file_bytes": settings.max_file_bytes,
            "backup_keep": settings.backup_keep,
        }

    @app.get("/api/session")
    async def session_status(request: Request):
        return _key_status(request, settings, sessions, sops)

    @app.post("/api/session/key")
    async def set_key(request: Request, body: KeyRequest):
        recipients = sops.validate_age_key(body.key)
        old_sid = _session_cookie(request, settings)
        sessions.delete(old_sid)
        sid = sessions.create(body.key.strip(), recipients)
        response = JSONResponse(
            {
                "ok": True,
                "recipients": recipients,
                "ttl_seconds": settings.session_ttl_seconds,
                "key_source": "session",
            }
        )
        _set_session_cookie(response, request, settings, sid)
        logger.info("Session age key set ({} recipient(s))", len(recipients))
        return response

    @app.delete("/api/session/key")
    async def clear_key(request: Request):
        sid = _session_cookie(request, settings)
        if sid:
            sessions.delete(sid)
        response = JSONResponse({"ok": True, "key": _key_status(request, settings, sessions, sops)})
        response.delete_cookie("sops_sid", path="/")
        return response

    @app.post("/api/upload")
    async def upload_file(
        request: Request, name: str = "secrets.enc.yaml", op: str | None = None
    ):
        raw = await request.body()
        if not raw:
            raise BadRequest("Пустой файл")
        if len(raw) > settings.max_file_bytes:
            raise PayloadTooLarge(f"Файл больше лимита {settings.max_file_bytes} байт")
        upload_id = request.cookies.get("sops_upload", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{20,80}", upload_id):
            upload_id = secrets.token_urlsafe(24)
        safe_name = _safe_upload_name(name)

        def work(progress):
            progress("upload", "Приняли файл, сохраняем на сервере")
            cleanup_uploads(uploads_root)
            upload_dir = uploads_root / upload_id / secrets.token_urlsafe(12)
            upload_dir.mkdir(parents=True, exist_ok=False)
            try:
                os.chmod(upload_dir, 0o700)
                target = upload_dir / safe_name
                write_atomic(target, raw)
            except Exception:
                shutil.rmtree(upload_dir, ignore_errors=True)
                raise
            return {
                "path": str(target),
                "name": safe_name,
                "original_name": Path(name).name or safe_name,
                "size": len(raw),
                "upload_id": upload_id,
                "download_url": f"/api/download?path={quote(str(target), safe='')}",
            }

        payload = _tracked(op, work)
        response = JSONResponse(payload)
        _set_upload_cookie(response, request, settings, upload_id)
        logger.info("Uploaded {} ({} bytes) to {}", safe_name, len(raw), payload["path"])
        return response

    @app.get("/api/download")
    async def download_file(path: str):
        resolved = guard.resolve_file(path)
        uploads_resolved = uploads_root.resolve(strict=False)
        if uploads_resolved not in resolved.parents:
            raise Forbidden("Скачивание доступно только для загруженных файлов")
        data = read_bounded(resolved, settings.max_file_bytes)
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", resolved.name)[:120] or "secrets.enc.yaml"
        return Response(
            content=data,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
        )

    @app.post("/api/encrypt/upload")
    async def encrypt_upload(
        request: Request,
        name: str = "secrets.yaml",
        recipients: str = "",
        op: str | None = None,
    ):
        raw = await request.body()
        if not raw:
            raise BadRequest("Пустой файл")
        if len(raw) > settings.max_file_bytes:
            raise PayloadTooLarge(f"Файл больше лимита {settings.max_file_bytes} байт")
        chosen = _clean_recipients(recipients) or _default_recipients(
            request, settings, sessions, sops
        )
        if not chosen:
            raise BadRequest(
                "Укажите age-получателя: у загруженного файла нет .sops.yaml, "
                "а серверный ключ не задан"
            )
        upload_id = request.cookies.get("sops_upload", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{20,80}", upload_id):
            upload_id = secrets.token_urlsafe(24)
        safe_name = _safe_upload_name(name, "secrets.yaml")

        def work(progress):
            progress("encrypt", "Шифруем через sops encrypt")
            cleanup_uploads(uploads_root)
            upload_dir = uploads_root / upload_id / secrets.token_urlsafe(12)
            upload_dir.mkdir(parents=True, exist_ok=False)
            try:
                os.chmod(upload_dir, 0o700)
                target = upload_dir / safe_name
                result = editor.encrypt_plain(safe_name, raw, chosen, target)
            except Exception:
                shutil.rmtree(upload_dir, ignore_errors=True)
                raise
            return {
                "path": result["path"],
                "name": result["name"],
                "original_name": Path(name).name or result["name"],
                "size": result["size"],
                "upload_id": upload_id,
                "recipients": result["recipients"],
                "encrypted": result["encrypted"],
                "download_url": f"/api/download?path={quote(str(target), safe='')}",
            }

        payload = _tracked(op, work)
        response = JSONResponse(payload)
        _set_upload_cookie(response, request, settings, upload_id)
        logger.info("Encrypted upload {} ({} bytes) -> {}", safe_name, len(raw), payload["path"])
        return response

    def _encrypt_impl(request: Request, body: EncryptRequest, progress) -> dict:
        explicit = _clean_recipients(body.recipients)
        progress("encrypt", "Шифруем через sops encrypt")
        if explicit:
            result = editor.encrypt_path(body.path, explicit)
        else:
            try:
                result = editor.encrypt_path(body.path, [])
            except SopsFailure as exc:
                if exc.code != "no_creation_rule":
                    raise
                fallback = _default_recipients(request, settings, sessions, sops)
                logger.info("no creation rule for {}; fallback recipients={}", body.path, fallback)
                if not fallback:
                    raise BadRequest(
                        "Нет подходящего правила .sops.yaml и не задан age-получатель. "
                        "Укажите публичный age-ключ."
                    ) from exc
                result = editor.encrypt_path(body.path, fallback)
        logger.info(
            "Encrypted {} -> {} ({} recipient(s))",
            body.path,
            result["path"],
            len(result["recipients"]),
        )
        return result

    @app.post("/api/encrypt")
    def encrypt_file(request: Request, body: EncryptRequest, op: str | None = None):
        return _tracked(op, lambda progress: _encrypt_impl(request, body, progress))

    @app.get("/api/browse")
    async def browse(request: Request, path: str | None = None, hidden: bool = False):
        raw = path or str(guard.primary_root)
        directory = guard.resolve_dir(raw)
        root = guard.roots[0]
        for candidate in guard.roots:
            if directory == candidate or candidate in directory.parents:
                root = candidate
                break
        entries: list[dict[str, Any]] = []
        try:
            iterator = directory.iterdir()
        except OSError as exc:
            raise Forbidden("Не удалось прочитать каталог") from exc
        for entry in iterator:
            name = entry.name
            if name == ".git":
                continue
            if name.startswith(".") and not hidden and not name.startswith(".env") and not name.startswith(".sops"):
                continue
            payload = _entry_payload(entry, root)
            if payload is not None:
                entries.append(payload)
            if len(entries) >= 500:
                break
        entries.sort(key=lambda item: (item["type"] != "dir", item["name"].casefold()))
        parent = None if directory == root else str(directory.parent)
        return {
            "path": str(directory),
            "parent": parent,
            "root": str(root),
            "entries": entries,
            "truncated": len(entries) >= 500,
        }

    @app.get("/api/search")
    async def search(
        request: Request,
        q: str,
        path: str | None = None,
        hidden: bool = False,
    ):
        query = (q or "").strip().lower()
        if len(query) < 2:
            raise BadRequest("Строка поиска слишком короткая")
        directory = guard.resolve_dir(path or str(guard.primary_root))
        root = guard.roots[0]
        for candidate in guard.roots:
            if directory == candidate or candidate in directory.parents:
                root = candidate
                break
        deadline = time.monotonic() + 4.0
        matches: list[dict[str, Any]] = []
        seen = 0
        for base, dirs, files in os.walk(directory):
            dirs[:] = [
                d
                for d in dirs
                if d not in _SKIP_DIRS
                and (hidden or not d.startswith("."))
            ]
            for filename in files:
                if not hidden and filename.startswith(".") and not filename.startswith(".env"):
                    continue
                seen += 1
                if seen > 6000 or time.monotonic() > deadline:
                    break
                if query not in filename.lower() and query not in str(
                    (Path(base) / filename).relative_to(directory)
                ).lower():
                    continue
                payload = _entry_payload(Path(base) / filename, root)
                if payload is not None:
                    payload["relative"] = str((Path(base) / filename).relative_to(directory))
                    matches.append(payload)
                if len(matches) >= 200:
                    break
            if seen > 6000 or time.monotonic() > deadline or len(matches) >= 200:
                break
        matches.sort(key=lambda item: item.get("relative", "").casefold())
        return {"query": q, "matches": matches, "truncated": len(matches) >= 200}

    @app.get("/api/file")
    async def open_file(request: Request, path: str):
        key = _key_material(request, settings, sessions)
        result = editor.open_file(path, key)
        logger.info(
            "Opened {} ({} bytes, format={}, key={})",
            result["path"],
            result["content_size"],
            result["format"],
            key.label,
        )
        return result

    def _tracked(op: str | None, work) -> dict:
        if op and _OP_ID_RE.fullmatch(op):
            ops.start(op)
        else:
            op = None

        def progress(phase: str, text: str) -> None:
            if op:
                ops.progress(op, phase, text)

        try:
            result = work(progress)
        except AppError as exc:
            if op:
                ops.fail(op, exc)
            raise
        if op:
            ops.finish(op, result)
        return result

    @app.get("/api/file/job")
    def open_file_job(request: Request, path: str, op: str | None = None):
        key = _key_material(request, settings, sessions)

        def work(progress):
            result = editor.open_file(path, key, progress)
            logger.info(
                "Opened {} ({} bytes, format={}, key={})",
                result["path"],
                result["content_size"],
                result["format"],
                key.label,
            )
            return result

        return _tracked(op, work)

    @app.post("/api/file/job")
    def save_file_job(request: Request, body: SaveRequest, op: str | None = None):
        key = _key_material(request, settings, sessions)

        def work(progress):
            result = editor.save_file(
                body.path, body.content, body.format, body.expected_sha256, key, progress
            )
            if result.get("saved"):
                logger.info(
                    "Saved {} ({} bytes, key={}, backup={})",
                    result["path"],
                    result.get("content_size"),
                    key.label,
                    (result.get("backup") or {}).get("id"),
                )
            return result

        return _tracked(op, work)

    @app.get("/api/file/stream")
    async def open_file_stream(request: Request, path: str):
        key = _key_material(request, settings, sessions)

        def work(emit):
            result = editor.open_file(path, key, emit)
            logger.info(
                "Opened {} ({} bytes, format={}, key={})",
                result["path"],
                result["content_size"],
                result["format"],
                key.label,
            )
            return result

        return _ndjson(work)

    @app.post("/api/file/stream")
    async def save_file_stream(request: Request, body: SaveRequest):
        key = _key_material(request, settings, sessions)

        def work(emit):
            result = editor.save_file(
                body.path, body.content, body.format, body.expected_sha256, key, emit
            )
            if result.get("saved"):
                logger.info(
                    "Saved {} ({} bytes, key={}, backup={})",
                    result["path"],
                    result.get("content_size"),
                    key.label,
                    (result.get("backup") or {}).get("id"),
                )
            return result

        return _ndjson(work)

    @app.post("/api/file")
    async def save_file(request: Request, body: SaveRequest):
        key = _key_material(request, settings, sessions)
        result = editor.save_file(
            body.path, body.content, body.format, body.expected_sha256, key
        )
        if result.get("saved"):
            logger.info(
                "Saved {} ({} bytes, key={}, backup={})",
                result["path"],
                result.get("content_size"),
                key.label,
                (result.get("backup") or {}).get("id"),
            )
        return result

    @app.get("/api/backups")
    async def list_backups(path: str):
        return {"backups": editor.list_backups(path)}

    @app.get("/api/backups/{backup_id}")
    async def download_backup(backup_id: str, path: str):
        _name, data = editor.read_backup(path, backup_id)
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(path).name)[:80] or "file"
        filename = f"{safe_name}.{backup_id}.enc"
        return Response(
            content=data,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/api/git")
    async def git_status(path: str):
        resolved = guard.resolve_file(path)
        return {"git": git.repo_info(resolved)}

    @app.post("/api/git/commit")
    async def git_commit(body: CommitRequest):
        resolved = guard.resolve_file(body.path)
        result = git.commit(resolved, body.message, body.push)
        logger.info("Git commit for {}: {}", resolved, result.get("hash"))
        return result

    @app.post("/api/git/push")
    async def git_push(body: PushRequest):
        resolved = guard.resolve_file(body.path)
        return git.push(resolved)

    @app.post("/api/git/pull")
    async def git_pull(body: PullRequest):
        resolved = guard.resolve_file(body.path)
        return git.pull(resolved)

    @app.post("/api/git/clone")
    async def git_clone(body: CloneRequest):
        result = git.clone(body.url, body.branch, body.name)
        logger.info("Cloned {} -> {}", result.get("remote"), result.get("path"))
        return result

    # ---------------------------------------------------------------- static
    frontend_dir = Path(os.environ.get("SOPS_EDITOR_FRONTEND_DIR", str(_FRONTEND_DIR)))
    help_ru = render_help(frontend_dir, "help.html")
    help_en = render_help(frontend_dir, "help.en.html") or help_ru
    if help_ru is not None:

        @app.get("/help.html", response_class=HTMLResponse)
        async def help_page(request: Request):
            theme = request.cookies.get("sops_theme", "")
            if theme not in ("dark", "light"):
                theme = "dark"
            requested = request.query_params.get("lang", "")
            lang = requested or request.cookies.get("sops_lang", "")
            if lang not in ("ru", "en"):
                lang = "ru"
            source = help_en if lang == "en" and help_en else help_ru
            page = source.replace(
                'data-theme="dark"', f'data-theme="{theme}"', 1
            ).replace('lang="ru"', f'lang="{lang}"', 1)
            response = HTMLResponse(
                page,
                headers={
                    # Revalidate so a language/theme switch is reflected immediately.
                    "Cache-Control": "private, no-cache",
                    "Vary": "Cookie",
                },
            )
            if requested in ("ru", "en"):
                response.set_cookie(
                    "sops_lang", requested, max_age=31536000, samesite="lax", path="/"
                )
            return response

    if frontend_dir.is_dir():
        app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
    else:
        @app.get("/", response_class=HTMLResponse)
        async def missing_frontend():
            return HTMLResponse(
                "<h1>SOPS Editor</h1><p>Каталог frontend не найден.</p>", status_code=500
            )

    return app


def run() -> None:
    import uvicorn

    settings = Settings()
    uvicorn.run(
        "app.main:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        log_level="info",
    )


if __name__ == "__main__":
    run()
