"""Notes CRUD, search, cursor pagination and tags, scoped to the authenticated user."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import Select, func, or_, select, tuple_
from sqlalchemy.sql.elements import ColumnElement

from app.deps import CurrentUserId, Session, State, WriterId
from app.models import Note
from app.schemas import (
    Cursor,
    NoteCreate,
    NoteOut,
    NotePage,
    NoteUpdate,
    TagCount,
    normalise_tags,
)

router = APIRouter(prefix="/api", tags=["notes"])


def escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def search_clause(q: str) -> ColumnElement[bool]:
    pattern = f"%{escape_like(q)}%"
    return or_(
        Note.search.op("@@")(func.websearch_to_tsquery("simple", q)),
        Note.title.ilike(pattern, escape="\\"),
        Note.body.ilike(pattern, escape="\\"),
    )


def list_query(
    user_id: uuid.UUID,
    *,
    q: str | None,
    tag: str | None,
    pinned: bool | None,
    cursor: Cursor | None,
    limit: int,
) -> Select[Note]:
    stmt = select(Note).where(Note.user_id == user_id)
    if q:
        stmt = stmt.where(search_clause(q))
    if tag:
        stmt = stmt.where(Note.tags.contains([tag]))
    if pinned is not None:
        stmt = stmt.where(Note.pinned.is_(pinned))
    if cursor is not None:
        stmt = stmt.where(
            tuple_(Note.pinned, Note.updated_at, Note.id)
            < tuple_(cursor.pinned, cursor.updated_at, cursor.id)
        )
    return stmt.order_by(Note.pinned.desc(), Note.updated_at.desc(), Note.id.desc()).limit(
        limit + 1
    )


async def _owned(db: Session, user_id: uuid.UUID, note_id: uuid.UUID) -> Note:
    note = await db.scalar(select(Note).where(Note.id == note_id, Note.user_id == user_id))
    if note is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Note not found")
    return note


@router.get("/notes", response_model=NotePage)
async def list_notes(
    user_id: CurrentUserId,
    state: State,
    db: Session,
    q: Annotated[str | None, Query(max_length=200, description="Search text")] = None,
    tag: Annotated[str | None, Query(max_length=40)] = None,
    pinned: bool | None = None,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Response:
    q = (q or "").strip() or None
    tag_value: str | None = None
    if tag:
        try:
            tag_value = next(iter(normalise_tags([tag])), None)
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    try:
        decoded = Cursor.decode(cursor) if cursor else None
    except ValueError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid cursor") from None

    params = {"q": q, "tag": tag_value, "pinned": pinned, "cursor": cursor, "limit": limit}
    key, cached = await state.cache.get(user_id, params)
    headers = {"Cache-Control": "private, no-cache", "Vary": "Authorization"}
    if cached is not None:
        return Response(
            cached, media_type="application/json", headers={**headers, "X-Cache": "HIT"}
        )

    rows = list(
        (
            await db.scalars(
                list_query(user_id, q=q, tag=tag_value, pinned=pinned, cursor=decoded, limit=limit)
            )
        ).all()
    )
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = (
        Cursor(pinned=rows[-1].pinned, updated_at=rows[-1].updated_at, id=rows[-1].id).encode()
        if has_more and rows
        else None
    )
    payload = NotePage(
        items=[NoteOut.model_validate(n) for n in rows], next_cursor=next_cursor
    ).model_dump_json()
    await state.cache.put(key, payload)
    return Response(payload, media_type="application/json", headers={**headers, "X-Cache": "MISS"})


@router.post("/notes", status_code=status.HTTP_201_CREATED, response_model=NoteOut)
async def create_note(data: NoteCreate, user_id: WriterId, state: State, db: Session) -> NoteOut:
    note = Note(
        id=uuid.uuid4(),
        user_id=user_id,
        title=data.title,
        body=data.body,
        tags=data.tags,
        pinned=data.pinned,
    )
    db.add(note)
    await db.commit()
    await db.refresh(note)
    await state.cache.invalidate(user_id)
    return NoteOut.model_validate(note)


@router.get("/notes/{note_id}", response_model=NoteOut)
async def get_note(note_id: uuid.UUID, user_id: CurrentUserId, db: Session) -> NoteOut:
    return NoteOut.model_validate(await _owned(db, user_id, note_id))


@router.patch("/notes/{note_id}", response_model=NoteOut)
async def update_note(
    note_id: uuid.UUID, data: NoteUpdate, user_id: WriterId, state: State, db: Session
) -> NoteOut:
    note = await _owned(db, user_id, note_id)
    changes = data.model_dump(exclude_unset=True)
    for field in ("title", "body", "tags", "pinned"):
        if field in changes:
            if changes[field] is None:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT, f"{field} cannot be null"
                )
            setattr(note, field, changes[field])
    note.updated_at = func.now()
    await db.commit()
    await db.refresh(note)
    await state.cache.invalidate(user_id)
    return NoteOut.model_validate(note)


@router.delete("/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_note(note_id: uuid.UUID, user_id: WriterId, state: State, db: Session) -> Response:
    note = await _owned(db, user_id, note_id)
    await db.delete(note)
    await db.commit()
    await state.cache.invalidate(user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/tags", response_model=list[TagCount])
async def list_tags(user_id: CurrentUserId, db: Session) -> list[TagCount]:
    tag = func.unnest(Note.tags).label("tag")
    sub = select(tag).where(Note.user_id == user_id).subquery()
    rows = await db.execute(
        select(sub.c.tag, func.count().label("count"))
        .group_by(sub.c.tag)
        .order_by(func.count().desc(), sub.c.tag)
        .limit(100)
    )
    return [TagCount(tag=r.tag, count=r.count) for r in rows]
