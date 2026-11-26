# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-11-26

### Added

- Notes REST API on FastAPI with PostgreSQL (SQLAlchemy 2 async + asyncpg) and Alembic
  migrations that run at start-up.
- Accounts: register, login, refresh with rotation and reuse detection, logout (revokes the
  refresh-token family in Redis); argon2id password hashing; JWT access and refresh tokens.
- Notes CRUD with tags, pinning, full-text + substring search, tag filter and cursor pagination.
- Redis read-through cache for note lists with versioned invalidation (`X-Cache` header).
- Redis fixed-window rate limits: auth 10/min per IP, writes 60/min per user, `429` + `Retry-After`.
- Single-page web UI (vanilla JS, no build step): accessible, responsive, dark mode with toggle,
  branding from `BRAND_*` variables via `/config.json`.
- `/health` (Postgres `SELECT 1` + Redis `PING`), JSON request logs, security headers,
  graceful shutdown.
- Alpine multi-stage Docker image (non-root), `compose.yaml`, `syncbay.json` manifest.
- Tests: ruff, mypy (strict), pytest unit + API suites, Playwright end-to-end tests, GitHub
  Actions CI.
