# Contributing

Thanks for taking the time to improve SOPS Editor.

## Development environment

Requirements: Python 3.12+, Docker (optional), and the `sops` + `age-keygen`
binaries on `PATH` (or in `.tools/` on Windows).

Dependencies are managed with [uv](https://docs.astral.sh/uv/) and pinned in
`backend/uv.lock` — do not add them by hand.

```bash
cd backend
uv sync --extra test      # creates backend/.venv from the lockfile
uv run uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8787
```

Set `SOPS_EDITOR_ROOTS`, `SOPS_EDITOR_STORAGE` and `SOPS_AGE_KEY_FILE` in the shell
before starting. After changing dependencies in `backend/pyproject.toml`, run
`uv lock` and commit the updated `uv.lock`.

The frontend is plain static files (`frontend/`): no build step, no bundler.
Reload the page after editing `app.js`, `styles.css`, `i18n.js` or `help*.html`.

Docker workflow: `docker build -t ai-sops-edit:local .` and
`docker compose --env-file docker/.env --env-file docker/.env.secrets up -d --force-recreate`.

## Tests

```bash
cd backend
uv run pytest -q
uv run ruff check app tests
```

Tests that need `sops`/`age-keygen` are skipped when the binaries are missing,
so install them before relying on a green run.

## Translations

The UI is bilingual (RU/EN). Static labels live in `frontend/i18n.js` (the `EN`
dictionary and the regex `RULES` for interpolated strings); dynamic strings are
translated through the same layer. The documentation has two files:
`frontend/help.html` (RU) and `frontend/help.en.html` (EN) — change both.

## Pull requests

1. Keep the change focused; one topic per PR.
2. Run the tests before opening the PR.
3. Update both READMEs — `.github/README.md` (English, shown on GitHub) and
   `README.md` (Russian, shown on GitLab) — plus the in-app help when behaviour changes.
4. Describe the user-visible effect and how you verified it.

Commit messages: short imperative subject (`fix: dedupe decrypt calls`), body
optional. Never commit real keys or secrets — `keys/age.txt`,
`docker/.env.secrets` and `storage/` are gitignored.

## License

By contributing you agree that your changes are licensed under the MIT License
(see [LICENSE](LICENSE)).
