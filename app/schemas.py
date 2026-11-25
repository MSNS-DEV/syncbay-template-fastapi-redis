"""Request and response models (Pydantic v2)."""

from __future__ import annotations

import base64
import binascii
import json
import re
import uuid
from datetime import datetime
from typing import Annotated

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
    field_validator,
)

MAX_TAGS = 10
_TAG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def _normalise_email(v: str) -> str:
    return v.strip().lower()


def normalise_tags(tags: list[str]) -> list[str]:
    seen: list[str] = []
    for raw in tags:
        tag = raw.strip().lower().lstrip("#")
        if not tag:
            continue
        if not _TAG_RE.match(tag):
            raise ValueError(
                f"invalid tag {raw!r}: use 1-32 characters a-z, 0-9, '-' or '_', "
                "starting with a letter or digit"
            )
        if tag not in seen:
            seen.append(tag)
    if len(seen) > MAX_TAGS:
        raise ValueError(f"at most {MAX_TAGS} tags")
    return seen


Email = Annotated[EmailStr, AfterValidator(_normalise_email)]
Password = Annotated[str, StringConstraints(min_length=8, max_length=128)]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Body = Annotated[str, StringConstraints(max_length=20_000)]
Tags = Annotated[list[Annotated[str, StringConstraints(max_length=40)]], Field(max_length=50)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class RegisterIn(StrictModel):
    email: Email
    password: Password
    display_name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=80)] = ""

    @field_validator("password")
    @classmethod
    def _password_strength(cls, v: str) -> str:
        if v.strip() != v:
            raise ValueError("password must not start or end with whitespace")
        if v.isdigit() or v.isalpha():
            raise ValueError("password must mix letters with digits or symbols")
        return v


class LoginIn(StrictModel):
    email: Email
    password: Annotated[str, StringConstraints(min_length=1, max_length=128)]


class RefreshIn(StrictModel):
    refresh_token: Annotated[str, StringConstraints(min_length=1, max_length=2048)] | None = None


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    display_name: str
    created_at: datetime


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


class NoteCreate(StrictModel):
    title: Title
    body: Body = ""
    tags: Tags = Field(default_factory=list)
    pinned: bool = False

    @field_validator("tags")
    @classmethod
    def _tags(cls, v: list[str]) -> list[str]:
        return normalise_tags(v)


class NoteUpdate(StrictModel):
    title: Title | None = None
    body: Body | None = None
    tags: Tags | None = None
    pinned: bool | None = None

    @field_validator("tags")
    @classmethod
    def _tags(cls, v: list[str] | None) -> list[str] | None:
        return None if v is None else normalise_tags(v)


class NoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    body: str
    tags: list[str]
    pinned: bool
    created_at: datetime
    updated_at: datetime


class NotePage(BaseModel):
    items: list[NoteOut]
    next_cursor: str | None


class TagCount(BaseModel):
    tag: str
    count: int


class Cursor(BaseModel):
    """Position after the last row of a page, in `pinned DESC, updated_at DESC, id DESC` order."""

    pinned: bool
    updated_at: datetime
    id: uuid.UUID

    def encode(self) -> str:
        raw = json.dumps(
            {"p": self.pinned, "u": self.updated_at.isoformat(), "i": str(self.id)},
            separators=(",", ":"),
        ).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    @classmethod
    def decode(cls, token: str) -> Cursor:
        if len(token) > 200:
            raise ValueError("invalid cursor")
        try:
            raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
            data = json.loads(raw)
            cursor = cls(pinned=data["p"], updated_at=data["u"], id=data["i"])
        except (binascii.Error, ValueError, KeyError, TypeError) as exc:
            raise ValueError("invalid cursor") from exc
        if cursor.updated_at.tzinfo is None:
            raise ValueError("invalid cursor")
        return cursor


class HealthOut(BaseModel):
    status: str
    checks: dict[str, str]


class BrandConfig(BaseModel):
    brandName: str
    brandLogoUrl: str
    brandPrimary: str
