# Execution Plan: 014 — CLI Foundation & Setup

**Date**: 2026-03-24
**Author**: gabi
**Status**: Draft
**Priority**: MEDIUM
**Dependency**: Backend API endpoints available (plans 001–005 should be complete first)

---

## Goal

Bootstrap the `testoria-cli` Python package: project structure, configuration management, HTTP client, and package metadata.

---

## Context

The CLI tool plan (`api/cli-tool.md`) defines the full scope. This plan covers Phase 1 only: the scaffolding that all subsequent plans build on. No commands are implemented here — only the plumbing (config file, API client, exceptions, pyproject.toml).

---

## Scope

### In scope
- `cli/` directory structure
- `pyproject.toml` with Python 3.11+, dependencies, entry points
- `testoria_cli/config.py` — YAML config at `~/.testoria/config.yaml`
- `testoria_cli/api_client.py` — httpx sync client, auth header injection, error handling
- `testoria_cli/exceptions.py` — custom exception hierarchy
- `testoria_cli/main.py` — Typer app skeleton (no commands yet)
- `tests/conftest.py` and `tests/unit/test_config.py`, `test_api_client.py`

### Out of scope
- Any CLI commands (covered in plans 007 and 008)
- pytest plugin (plan 008)
- PyPI publishing (plan 009)

---

## Technical approach

### Project layout

```
cli/
├── testoria_cli/
│   ├── __init__.py          # version = "1.0.0"
│   ├── main.py              # Typer app, command groups wired
│   ├── config.py            # ConfigManager, TestoriaConfig (Pydantic)
│   ├── api_client.py        # TestoriaClient (httpx)
│   └── exceptions.py        # TestoriaCLIError hierarchy
├── tests/
│   ├── conftest.py
│   └── unit/
│       ├── test_config.py
│       └── test_api_client.py
├── pyproject.toml
└── requirements.txt
```

### Config file location

`~/.testoria/config.yaml` — stores `url`, `access_token`, `refresh_token`, `default_project`, `output_format`.

### API client design

- Synchronous httpx (`httpx.Client`) — CLI runs in a terminal, async adds no benefit
- Base URL: `{config.url}/api/v1`
- Auth: `Authorization: Bearer {access_token}` on every request
- Error mapping: 401 → `AuthenticationError`, 403/404/4xx → `APIError`

---

## Tasks

### Implementation
- [ ] Create `cli/` directory structure
- [ ] Write `pyproject.toml` (Python 3.11+, typer[all], httpx, pyyaml, python-dotenv, rich, lxml)
- [ ] Write `testoria_cli/__init__.py` with `__version__ = "1.0.0"`
- [ ] Write `testoria_cli/exceptions.py`
- [ ] Write `testoria_cli/config.py` — `TestoriaConfig` (Pydantic BaseModel) + `ConfigManager`
- [ ] Write `testoria_cli/api_client.py` — `TestoriaClient` with `get/post/put/delete` + `login()`
- [ ] Write `testoria_cli/main.py` — `app = typer.Typer(name="testoria")` skeleton
- [ ] Write `tests/unit/test_config.py` — load, save, update, clear
- [ ] Write `tests/unit/test_api_client.py` — 401 raises AuthenticationError, 404 raises APIError, etc.
- [ ] Verify `pip install -e .[dev]` works
- [ ] Verify `testoria --help` prints usage

### Quality check
- [ ] `pytest tests/unit/` passes
- [ ] `ruff check testoria_cli tests` clean
- [ ] `mypy testoria_cli` clean

### Docs
- [ ] Update `api/cli-tool.md` Phase 1 success criteria checkboxes
- [ ] Move to `completed/`

---

## Definition of done

- [ ] `pip install -e .[dev]` from `cli/` completes without errors
- [ ] `testoria --help` prints the CLI help text
- [ ] Config saves to `~/.testoria/config.yaml` and loads back correctly
- [ ] API client raises `AuthenticationError` on 401 and `APIError` on other 4xx
- [ ] Unit tests pass with >80% coverage on `config.py` and `api_client.py`
