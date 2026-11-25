# FastAPI & Redis — architect spec

## Purpose

A production-shaped starting point for a Python JSON API on Syncbay: a **Notes REST API** with JWT
authentication, PostgreSQL persistence and Redis for caching, rate limiting and refresh-token
revocation, plus a small single-page web UI that exercises every endpoint. It shows the patterns a
real service needs (migrations at start-up, health checks, structured logs, graceful shutdown,
input validation, per-user authorization) in a codebase small enough to read in one sitting.

## Features

- **Accounts:** register, log in, refresh, log out, current user.
  - Passwords hashed with argon2id (`argon2-cffi`, library defaults: t=3, m=64 MiB, p=4).
  - Short-lived access token (JWT HS256, 15 min, `Authorization: Bearer`).
  - Refresh token (JWT HS256, 14 days) with **rotation**: every refresh consumes the old token
    (`GETDEL rt:<jti>` in Redis) and issues a new one in the same *family*. Re-using a consumed
    token revokes the whole family (theft detection). Logout revokes the family.
  - API clients get both tokens in the JSON body. Browsers additionally get the refresh token in an
    `httpOnly`, `Secure`, `SameSite=Lax` cookie scoped to `/api/auth`; cookie-based refresh/logout
    require a double-submit CSRF header (`X-CSRF-Token` = `csrf_token` cookie).
- **Notes** (per user): title, Markdown body, tags, pinned flag, timestamps.
  - CRUD with strict per-user authorization (another user's note is a 404).
  - Search `q`: Postgres full-text (`tsvector`, `simple` config, GIN index, `websearch_to_tsquery`)
    OR case-insensitive substring match on title/body (`ILIKE`, wildcards escaped).
  - Filters: `tag`, `pinned`.
  - Keyset (cursor) pagination ordered by `pinned DESC, updated_at DESC, id DESC`; the cursor is an
    opaque base64url token. `limit` 1–100 (default 20).
  - Tag list with counts.
- **Redis read-through cache** for note lists: key `notes:list:<user>:v<version>:<sha256(params)>`,
  TTL 60 s. Every write `INCR`s `notes:ver:<user>`, so all cached pages for that user become
  unreachable at once (no `SCAN`/`DEL`). `X-Cache: HIT|MISS` response header.
- **Rate limiting** (Redis fixed window, one-minute windows, `INCR` + `EXPIRE`):
  - auth endpoints (register, login, refresh): 10/min per client IP;
  - note writes (create, update, delete): 60/min per user.
  - Exceeding returns `429` with `Retry-After` (seconds to the window end) and `X-RateLimit-*`
    headers. On a Redis error the limiter fails open and logs a warning.
- **Web UI** at `/`: static HTML + vanilla JS + CSS, no build step. Register / log in, list,
  search, filter by tag, create, edit, pin, delete notes; Markdown preview (safe renderer: HTML is
  escaped before formatting, links limited to http/https/mailto). Accessible (semantic landmarks,
  labels, live region for status, visible focus, keyboard-usable), responsive from 360 px, dark
  mode following `prefers-color-scheme` with a persisted toggle. Branding from `/config.json`.
- **OpenAPI** docs at `/docs` (Swagger UI) and `/openapi.json`.
- **Demo data:** with `SEED_DEMO_DATA=true`, every newly registered account starts with three
  welcome notes (no shared demo credentials are ever created).

## Data model (PostgreSQL)

```
users
  id            uuid pk
  email         citext-like: varchar(254), stored lower-case, unique
  display_name  varchar(80)
  password_hash varchar(255)
  created_at    timestamptz default now()

notes
  id            uuid pk
  user_id       uuid fk -> users.id on delete cascade
  title         varchar(200) not null
  body          text not null default ''   (≤ 20 000 chars, validated)
  tags          varchar(32)[] not null default '{}'  (≤ 10, lower-case, [a-z0-9-_])
  pinned        boolean not null default false
  created_at    timestamptz default now()
  updated_at    timestamptz default now()
  search        tsvector GENERATED ALWAYS AS (to_tsvector('simple', title || ' ' || body)) STORED
  indexes: (user_id, pinned, updated_at, id) btree; search GIN; tags GIN
```

Redis keys: `rt:<jti>` (refresh token → user/family, TTL = token TTL), `rtfam:<family>` (set of
jtis), `rl:<bucket>:<subject>:<window>` (rate-limit counters, TTL 60 s), `notes:ver:<user>`,
`notes:list:…` (cache, TTL 60 s).

## Routes

| Method | Path | Auth | Notes |
| --- | --- | --- | --- |
| GET | `/health` | – | `200 {"status":"ok","checks":{"db":"ok","redis":"ok"}}` or 503 |
| GET | `/config.json` | – | `{brandName, brandLogoUrl, brandPrimary}` |
| GET | `/` , `/static/*` | – | Web UI |
| GET | `/docs`, `/openapi.json` | – | OpenAPI |
| POST | `/api/auth/register` | – | 201, rate-limited (IP) |
| POST | `/api/auth/login` | – | 200, rate-limited (IP) |
| POST | `/api/auth/refresh` | refresh token (body or cookie+CSRF) | rotation, rate-limited (IP) |
| POST | `/api/auth/logout` | refresh token (body or cookie+CSRF) | 204, revokes family |
| GET | `/api/auth/me` | Bearer | current user |
| GET | `/api/notes` | Bearer | `q`, `tag`, `pinned`, `cursor`, `limit`; cached |
| POST | `/api/notes` | Bearer | 201, rate-limited (user) |
| GET | `/api/notes/{id}` | Bearer | |
| PATCH | `/api/notes/{id}` | Bearer | partial update, rate-limited (user) |
| DELETE | `/api/notes/{id}` | Bearer | 204, rate-limited (user) |
| GET | `/api/tags` | Bearer | `[{tag, count}]` |

Errors use FastAPI's shape: `{"detail": "..."}` or, for validation, `{"detail": [ ... ]}` (422).

## Environment variables

| Key | Required | Default | Purpose |
| --- | --- | --- | --- |
| `DATABASE_URL` | yes | – | `postgresql://…` (converted to `postgresql+asyncpg://`; `sslmode` mapped to asyncpg `ssl`) |
| `REDIS_URL` | yes | – | `redis://…` or `rediss://…` |
| `JWT_SECRET` | yes | – | HS256 key, ≥ 32 characters |
| `PORT` | no | 8000 | Listen port |
| `ACCESS_TOKEN_TTL_SECONDS` | no | 900 | |
| `REFRESH_TOKEN_TTL_SECONDS` | no | 1209600 | |
| `RATE_LIMIT_AUTH_PER_MINUTE` | no | 10 | per IP |
| `RATE_LIMIT_WRITE_PER_MINUTE` | no | 60 | per user |
| `CACHE_TTL_SECONDS` | no | 60 | list cache TTL |
| `TRUSTED_PROXY_HOPS` | no | 0 | how many reverse proxies append to `X-Forwarded-For` (Syncbay: 1) |
| `COOKIE_SECURE` | no | true | `Secure` flag on cookies |
| `DB_POOL_SIZE` | no | 5 | SQLAlchemy pool size |
| `LOG_LEVEL` | no | info | |
| `SEED_DEMO_DATA` | no | true | welcome notes for new accounts |
| `BRAND_NAME` / `BRAND_LOGO_URL` / `BRAND_PRIMARY` | no | Syncbay / – / `#06b6d4` | branding |

## Services

- PostgreSQL 16 (`DATABASE_URL`), migrations with Alembic, run by the container entrypoint before
  uvicorn starts (serialised with a Postgres advisory lock).
- Redis 7 (`REDIS_URL`).

## Runtime

uvicorn, 1 worker, uvloop + httptools, graceful shutdown ≤ 10 s; lifespan disposes the SQLAlchemy
engine and closes the Redis pool. JSON logs on stdout (one line per request: method, path, status,
duration_ms, client IP). Security headers on every response (CSP, `X-Content-Type-Options`,
`Referrer-Policy`, `frame-ancestors 'none'`); `/docs` gets a CSP that allows the Swagger UI CDN.

## Non-goals

Email verification and password reset, OAuth providers, sharing notes between users, attachments,
real-time sync, server-side Markdown rendering, multi-worker deployment, admin UI.
