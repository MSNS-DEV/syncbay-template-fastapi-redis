import uuid

import jwt
import pytest

from app.security import (
    ALGORITHM,
    TokenError,
    create_token,
    decode_token,
    hash_password,
    needs_rehash,
    verify_password,
)

SECRET = "unit-test-secret-0123456789abcdef0123456789"


def test_hash_is_argon2id_and_verifies() -> None:
    h = hash_password("correct horse 1")
    assert h.startswith("$argon2id$")
    assert verify_password(h, "correct horse 1")
    assert not verify_password(h, "wrong horse 1")
    assert not needs_rehash(h)


def test_hashes_are_salted() -> None:
    assert hash_password("same-pass-1") != hash_password("same-pass-1")


def test_verify_handles_missing_and_garbage_hashes() -> None:
    assert not verify_password(None, "anything")
    assert not verify_password("not-a-hash", "anything")


def test_access_token_round_trip() -> None:
    uid = uuid.uuid4()
    token, claims = create_token(secret=SECRET, user_id=uid, token_type="access", ttl_seconds=60)
    decoded = decode_token(token, secret=SECRET, expected_type="access")
    assert decoded.sub == uid
    assert decoded.jti == claims.jti
    assert decoded.family is None


def test_refresh_token_carries_family() -> None:
    token, _ = create_token(
        secret=SECRET, user_id=uuid.uuid4(), token_type="refresh", ttl_seconds=60, family="fam1"
    )
    assert decode_token(token, secret=SECRET, expected_type="refresh").family == "fam1"


def test_token_type_confusion_is_rejected() -> None:
    access, _ = create_token(
        secret=SECRET, user_id=uuid.uuid4(), token_type="access", ttl_seconds=60
    )
    with pytest.raises(TokenError):
        decode_token(access, secret=SECRET, expected_type="refresh")


def test_refresh_without_family_is_rejected() -> None:
    token, _ = create_token(
        secret=SECRET, user_id=uuid.uuid4(), token_type="refresh", ttl_seconds=60
    )
    with pytest.raises(TokenError):
        decode_token(token, secret=SECRET, expected_type="refresh")


def test_expired_and_tampered_tokens_are_rejected() -> None:
    expired, _ = create_token(
        secret=SECRET, user_id=uuid.uuid4(), token_type="access", ttl_seconds=60, now=1_000
    )
    with pytest.raises(TokenError):
        decode_token(expired, secret=SECRET, expected_type="access")
    good, _ = create_token(secret=SECRET, user_id=uuid.uuid4(), token_type="access", ttl_seconds=60)
    with pytest.raises(TokenError):
        decode_token(good, secret=SECRET + "x", expected_type="access")


def test_alg_none_is_rejected() -> None:
    payload = jwt.decode(
        create_token(secret=SECRET, user_id=uuid.uuid4(), token_type="access", ttl_seconds=60)[0],
        SECRET,
        algorithms=[ALGORITHM],
        options={"verify_iss": False},
    )
    unsigned = jwt.encode(payload, key=None, algorithm="none")
    with pytest.raises(TokenError):
        decode_token(unsigned, secret=SECRET, expected_type="access")
