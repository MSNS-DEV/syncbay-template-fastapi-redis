import httpx

from tests.api.conftest import AppFactory, bearer, register, unique_email


async def test_register_login_me(client: httpx.AsyncClient) -> None:
    email = unique_email()
    tokens = await register(client, email.upper())
    assert tokens["user"]["email"] == email
    assert tokens["token_type"] == "bearer"
    me = await client.get("/api/auth/me", headers=bearer(tokens))
    assert me.status_code == 200
    assert me.json()["email"] == email
    login = await client.post("/api/auth/login", json={"email": email, "password": "pa55word!"})
    assert login.status_code == 200
    assert "refresh_token=" in login.headers["set-cookie"]
    assert "httponly" in login.headers["set-cookie"].lower()


async def test_duplicate_email_conflicts(client: httpx.AsyncClient) -> None:
    email = unique_email()
    await register(client, email)
    res = await client.post("/api/auth/register", json={"email": email, "password": "pa55word!"})
    assert res.status_code == 409


async def test_oversized_body_is_refused_before_parsing(client: httpx.AsyncClient) -> None:
    huge = b'{"email": "a@example.com", "password": "' + b"x" * (300 * 1024) + b'"}'
    res = await client.post(
        "/api/auth/login", content=huge, headers={"content-type": "application/json"}
    )
    assert res.status_code == 413


async def test_validation_errors(client: httpx.AsyncClient) -> None:
    res = await client.post("/api/auth/register", json={"email": "nope", "password": "short"})
    assert res.status_code == 422
    fields = {e["loc"][-1] for e in res.json()["detail"]}
    assert fields == {"email", "password"}


async def test_wrong_password_and_unknown_user(client: httpx.AsyncClient) -> None:
    email = unique_email()
    await register(client, email)
    bad = await client.post("/api/auth/login", json={"email": email, "password": "wrong-pass1"})
    assert bad.status_code == 401
    unknown = await client.post(
        "/api/auth/login", json={"email": unique_email(), "password": "pa55word!"}
    )
    assert unknown.status_code == 401
    assert bad.json() == unknown.json()


async def test_protected_routes_need_a_valid_access_token(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/notes")).status_code == 401
    tokens = await register(client)
    # A refresh token is not an access token.
    res = await client.get(
        "/api/notes", headers={"Authorization": f"Bearer {tokens['refresh_token']}"}
    )
    assert res.status_code == 401
    assert res.headers["www-authenticate"] == "Bearer"


async def test_refresh_rotation_and_reuse_detection(client: httpx.AsyncClient) -> None:
    tokens = await register(client)
    first = tokens["refresh_token"]
    rotated = await client.post("/api/auth/refresh", json={"refresh_token": first})
    assert rotated.status_code == 200
    second = rotated.json()["refresh_token"]
    assert second != first
    # Re-using the consumed token fails and revokes the family, including the new token.
    reuse = await client.post("/api/auth/refresh", json={"refresh_token": first})
    assert reuse.status_code == 401
    after = await client.post("/api/auth/refresh", json={"refresh_token": second})
    assert after.status_code == 401


async def test_logout_revokes_refresh_token(client: httpx.AsyncClient) -> None:
    tokens = await register(client)
    out = await client.post("/api/auth/logout", json={"refresh_token": tokens["refresh_token"]})
    assert out.status_code == 204
    res = await client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert res.status_code == 401


async def test_cookie_refresh_requires_csrf_header(client: httpx.AsyncClient) -> None:
    await register(client)  # sets refresh_token + csrf_token cookies on the client
    csrf = client.cookies.get("csrf_token")
    assert csrf
    missing = await client.post("/api/auth/refresh")
    assert missing.status_code == 403
    wrong = await client.post("/api/auth/refresh", headers={"X-CSRF-Token": "nope"})
    assert wrong.status_code == 403
    ok = await client.post("/api/auth/refresh", headers={"X-CSRF-Token": csrf})
    assert ok.status_code == 200, ok.text


async def test_auth_rate_limit_returns_429(make_client: AppFactory) -> None:
    c = await make_client(rate_limit_auth_per_minute=3)
    codes = []
    for _ in range(4):
        res = await c.post(
            "/api/auth/login", json={"email": unique_email(), "password": "pa55word!"}
        )
        codes.append(res.status_code)
    assert codes == [401, 401, 401, 429]
    assert 1 <= int(res.headers["retry-after"]) <= 60
    assert res.headers["x-ratelimit-remaining"] == "0"


async def test_seed_creates_welcome_notes(make_client: AppFactory) -> None:
    c = await make_client(seed_demo_data=True)
    tokens = await register(c)
    notes = (await c.get("/api/notes", headers=bearer(tokens))).json()["items"]
    assert len(notes) == 3
    assert notes[0]["pinned"] is True
