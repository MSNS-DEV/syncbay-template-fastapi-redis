"""Demo data: welcome notes for new accounts when SEED_DEMO_DATA=true."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Note

WELCOME_NOTES: tuple[tuple[str, str, list[str], bool], ...] = (
    (
        "Welcome to your notes",
        "This app is a **FastAPI** service with *PostgreSQL* and *Redis*.\n\n"
        "- Create, edit, pin and delete notes\n"
        "- Tag notes and filter by tag\n"
        "- Search titles and bodies\n\n"
        "Try the API at [/docs](/docs).",
        ["welcome"],
        True,
    ),
    (
        "How caching works",
        "Note lists are cached in Redis for a minute. Every write bumps a per-user version, "
        "so the next read misses the cache and sees your change at once.\n\n"
        "Check the `X-Cache` response header: `HIT` or `MISS`.",
        ["redis", "cache"],
        False,
    ),
    (
        "Markdown cheatsheet",
        "# Heading\n## Smaller heading\n\n**bold**, *italic*, `code`\n\n"
        "- list item\n- another item\n\n"
        "```\ncode block\n```\n\n[A link](https://syncbay.app)",
        ["markdown"],
        False,
    ),
)


def seed_welcome_notes(db: AsyncSession, user_id: uuid.UUID) -> None:
    for title, body, tags, pinned in WELCOME_NOTES:
        db.add(
            Note(id=uuid.uuid4(), user_id=user_id, title=title, body=body, tags=tags, pinned=pinned)
        )
