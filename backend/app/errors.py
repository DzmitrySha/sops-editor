from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Error safe to return to the browser."""

    status_code = 400
    code = "bad_request"
    default_message = "Ошибка запроса"

    def __init__(
        self,
        message: str | None = None,
        *,
        status_code: int | None = None,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.default_message
        if status_code is not None:
            self.status_code = status_code
        if code is not None:
            self.code = code
        self.details = details or {}
        super().__init__(self.message)


class BadRequest(AppError):
    status_code = 400
    code = "bad_request"
    default_message = "Некорректный запрос"


class Forbidden(AppError):
    status_code = 403
    code = "forbidden"
    default_message = "Доступ запрещён"


class NotFound(AppError):
    status_code = 404
    code = "not_found"
    default_message = "Файл не найден"


class Conflict(AppError):
    status_code = 409
    code = "conflict"
    default_message = "Файл изменился на сервере"


class PayloadTooLarge(AppError):
    status_code = 413
    code = "too_large"
    default_message = "Файл слишком большой"


class UnsupportedFormat(AppError):
    status_code = 415
    code = "unsupported_format"
    default_message = "Формат файла не поддерживается"


class KeyRequired(AppError):
    status_code = 422
    code = "key_required"
    default_message = "Нужен приватный age-ключ для расшифровки"


class SopsFailure(AppError):
    status_code = 422
    code = "sops_failed"
    default_message = "SOPS не смог обработать файл"
