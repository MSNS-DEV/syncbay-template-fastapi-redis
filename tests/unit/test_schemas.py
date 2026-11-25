import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.schemas import Cursor, LoginIn, NoteCreate, NoteUpdate, RegisterIn, normalise_tags


def test_register_normalises_email() -> None:
    data = RegisterIn(email="  Alice@Example.COM ", password="hunter2!x")
    assert data.email == "alice@example.com"
    assert data.display_name == ""


@pytest.mark.parametrize(
    "password",
    ["short1!", "onlyletters", "1234567890", " leadingspace1", "x" * 129],
)
def test_register_rejects_weak_passwords(password: str) -> None:
    with pytest.raises(ValidationError):
        RegisterIn(email="a@example.com", password=password)


def test_register_rejects_bad_email_and_extra_fields() -> None:
    with pytest.raises(ValidationError):
        RegisterIn(email="not-an-email", password="hunter2!x")
    with pytest.raises(ValidationError):
        RegisterIn.model_validate({"email": "a@example.com", "password": "hunter2!x", "role": "x"})


def test_login_requires_password() -> None:
    with pytest.raises(ValidationError):
        LoginIn(email="a@example.com", password="")


def test_note_create_trims_title_and_normalises_tags() -> None:
    note = NoteCreate(title="  Groceries  ", tags=["#Food", "food", " Shopping-List "])
    assert note.title == "Groceries"
    assert note.tags == ["food", "shopping-list"]
    assert note.body == ""
    assert note.pinned is False


@pytest.mark.parametrize(
    "payload",
    [
        {"title": ""},
        {"title": "   "},
        {"title": "x" * 201},
        {"title": "ok", "body": "x" * 20_001},
        {"title": "ok", "tags": ["bad tag"]},
        {"title": "ok", "tags": [f"t{i}" for i in range(11)]},
        {"title": "ok", "pinned": "maybe"},
    ],
)
def test_note_create_validation(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        NoteCreate.model_validate(payload)


def test_note_update_is_partial() -> None:
    update = NoteUpdate.model_validate({"pinned": True})
    assert update.model_dump(exclude_unset=True) == {"pinned": True}
    assert NoteUpdate(tags=["A"]).tags == ["a"]


def test_normalise_tags_dedupes_and_skips_empty() -> None:
    assert normalise_tags(["a", "", "  ", "A", "b_c"]) == ["a", "b_c"]
    with pytest.raises(ValueError, match="invalid tag"):
        normalise_tags(["-leading"])


def test_cursor_round_trip() -> None:
    cursor = Cursor(
        pinned=True, updated_at=datetime(2026, 1, 2, 3, 4, 5, 678901, UTC), id=uuid.uuid4()
    )
    assert Cursor.decode(cursor.encode()) == cursor


@pytest.mark.parametrize("token", ["", "%%%", "bm90LWpzb24", "eyJwIjp0cnVlfQ", "a" * 201])
def test_cursor_rejects_garbage(token: str) -> None:
    with pytest.raises(ValueError, match="invalid cursor"):
        Cursor.decode(token)


def test_cursor_rejects_naive_datetime() -> None:
    import base64
    import json

    raw = json.dumps({"p": False, "u": "2026-01-01T00:00:00", "i": str(uuid.uuid4())}).encode()
    token = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    with pytest.raises(ValueError, match="invalid cursor"):
        Cursor.decode(token)
