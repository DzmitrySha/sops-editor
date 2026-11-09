from __future__ import annotations

import re
from pathlib import Path

from .errors import Forbidden, NotFound

_BACKUP_ID_RE = re.compile(r"^[0-9A-Za-z._-]{1,180}$")


class PathGuard:
    """Resolves user-supplied paths and keeps them inside configured roots."""

    def __init__(self, roots: list[Path]) -> None:
        self.roots: list[Path] = []
        for root in roots:
            try:
                resolved = root.expanduser().resolve(strict=False)
            except OSError:
                continue
            if resolved not in self.roots:
                self.roots.append(resolved)
        if not self.roots:
            raise ValueError("SOPS_EDITOR_ROOTS is empty")

    @property
    def primary_root(self) -> Path:
        return self.roots[0]

    def _within(self, path: Path, root: Path) -> bool:
        return path == root or root in path.parents

    def _root_for(self, path: Path) -> Path | None:
        for root in self.roots:
            if self._within(path, root):
                return root
        return None

    @staticmethod
    def _has_forbidden_part(path: Path) -> bool:
        return any(part.lower() == ".git" for part in path.parts)

    def _candidate(self, raw: str) -> Path:
        if not isinstance(raw, str):
            raise Forbidden("Путь должен быть строкой")
        raw = raw.strip()
        if not raw or "\x00" in raw:
            raise Forbidden("Пустой или некорректный путь")
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = self.primary_root / candidate
        return candidate

    def resolve_dir(self, raw: str) -> Path:
        candidate = self._candidate(raw)
        try:
            resolved = candidate.resolve(strict=True)
        except FileNotFoundError as exc:
            raise NotFound(f"Каталог не найден: {raw}") from exc
        except OSError as exc:
            raise Forbidden("Не удалось открыть путь") from exc
        root = self._root_for(resolved)
        if root is None:
            raise Forbidden("Путь вне разрешённых каталогов")
        relative = resolved.relative_to(root)
        if self._has_forbidden_part(relative):
            raise Forbidden("Каталог .git недоступен")
        if not resolved.is_dir():
            raise NotFound(f"Это не каталог: {raw}")
        return resolved

    def resolve_file(self, raw: str, *, must_exist: bool = True) -> Path:
        candidate = self._candidate(raw)
        try:
            resolved = candidate.resolve(strict=must_exist)
        except FileNotFoundError as exc:
            raise NotFound(f"Файл не найден: {raw}") from exc
        except OSError as exc:
            raise Forbidden("Не удалось открыть путь") from exc
        root = self._root_for(resolved)
        if root is None:
            raise Forbidden("Путь вне разрешённых каталогов")
        relative = resolved.relative_to(root)
        if self._has_forbidden_part(relative):
            raise Forbidden("Файлы в .git недоступны")
        if not resolved.is_file():
            raise NotFound(f"Это не файл: {raw}")
        return resolved

    @staticmethod
    def backup_id_ok(value: str) -> bool:
        return bool(_BACKUP_ID_RE.fullmatch(value))
