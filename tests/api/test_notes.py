import asyncio

import httpx

from tests.api.conftest import AppFactory, bearer, register


async def _create(client: httpx.AsyncClient, headers: dict[str, str], **body: object) -> dict:
    res = await client.post("/api/notes", json={"title": "Untitled", **body}, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


async def test_crud(client: httpx.AsyncClient) -> None:
    h = bearer(await register(client))
    note = await _create(client, h, title="  First ", body="Hello **world**", tags=["Work"])
    assert note["title"] == "First"
    assert note["tags"] == ["work"]
    got = await client.get(f"/api/notes/{note['id']}", headers=h)
    assert got.json() == note

    await asyncio.sleep(0.01)
    patched = await client.patch(
        f"/api/notes/{note['id']}", json={"pinned": True, "body": "Edited"}, headers=h
    )
    assert patched.status_code == 200
    data = patched.json()
    assert data["pinned"] is True
    assert data["body"] == "Edited"
    assert data["title"] == "First"
    assert data["updated_at"] > note["updated_at"]

    assert (
        await client.patch(f"/api/notes/{note['id']}", json={"title": None}, headers=h)
    ).status_code == 422
    assert (await client.delete(f"/api/notes/{note['id']}", headers=h)).status_code == 204
    assert (await client.get(f"/api/notes/{note['id']}", headers=h)).status_code == 404


async def test_validation_error(client: httpx.AsyncClient) -> None:
    h = bearer(await register(client))
    res = await client.post("/api/notes", json={"title": "", "tags": ["no spaces"]}, headers=h)
    assert res.status_code == 422
    assert {e["loc"][1] for e in res.json()["detail"]} == {"title", "tags"}
    res = await client.post("/api/notes", json={"title": "x", "owner": "someone"}, headers=h)
    assert res.status_code == 422


async def test_notes_are_private(client: httpx.AsyncClient) -> None:
    alice = bearer(await register(client))
    bob = bearer(await register(client))
    note = await _create(client, alice, title="Alice's secret")
    for method in ("GET", "PATCH", "DELETE"):
        res = await client.request(
            method,
            f"/api/notes/{note['id']}",
            headers=bob,
            json={"title": "x"} if method == "PATCH" else None,
        )
        assert res.status_code == 404
    assert (await client.get("/api/notes", headers=bob)).json()["items"] == []


async def test_search_tag_and_pinned_filters(client: httpx.AsyncClient) -> None:
    h = bearer(await register(client))
    await _create(client, h, title="Deploy checklist", body="run migrations", tags=["ops"])
    await _create(client, h, title="Recipes", body="Pasta with 100% tomatoes", tags=["food"])
    await _create(client, h, title="Ideas", body="Deploying on Fridays", pinned=True)

    async def titles(**params: object) -> list[str]:
        res = await client.get("/api/notes", params=params, headers=h)
        assert res.status_code == 200, res.text
        return [n["title"] for n in res.json()["items"]]

    assert await titles(q="deploy") == ["Ideas", "Deploy checklist"]
    assert await titles(q="MIGRATIONS") == ["Deploy checklist"]
    assert await titles(q="100%") == ["Recipes"]
    assert await titles(q="%") == ["Recipes"]
    assert await titles(q="nothing-matches") == []
    assert await titles(tag="FOOD") == ["Recipes"]
    assert await titles(pinned="true") == ["Ideas"]
    bad = await client.get("/api/notes", params={"tag": "bad tag"}, headers=h)
    assert bad.status_code == 422


async def test_cursor_pagination(client: httpx.AsyncClient) -> None:
    h = bearer(await register(client))
    for i in range(7):
        await _create(client, h, title=f"n{i}", pinned=i == 3)
    seen: list[str] = []
    cursor = None
    pages = 0
    while True:
        params: dict[str, object] = {"limit": 3}
        if cursor:
            params["cursor"] = cursor
        page = (await client.get("/api/notes", params=params, headers=h)).json()
        seen += [n["title"] for n in page["items"]]
        pages += 1
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert pages == 3
    assert seen == ["n3", "n6", "n5", "n4", "n2", "n1", "n0"]
    bad = await client.get("/api/notes", params={"cursor": "garbage"}, headers=h)
    assert bad.status_code == 422


async def test_list_cache_hit_and_invalidation(client: httpx.AsyncClient) -> None:
    h = bearer(await register(client))
    await _create(client, h, title="one")
    first = await client.get("/api/notes", headers=h)
    assert first.headers["x-cache"] == "MISS"
    second = await client.get("/api/notes", headers=h)
    assert second.headers["x-cache"] == "HIT"
    assert second.json() == first.json()
    await _create(client, h, title="two")
    third = await client.get("/api/notes", headers=h)
    assert third.headers["x-cache"] == "MISS"
    assert [n["title"] for n in third.json()["items"]] == ["two", "one"]


async def test_tags_endpoint(client: httpx.AsyncClient) -> None:
    h = bearer(await register(client))
    await _create(client, h, tags=["a", "b"])
    await _create(client, h, tags=["a"])
    res = await client.get("/api/tags", headers=h)
    assert res.json() == [{"tag": "a", "count": 2}, {"tag": "b", "count": 1}]


async def test_write_rate_limit(make_client: AppFactory) -> None:
    c = await make_client(rate_limit_write_per_minute=2)
    h = bearer(await register(c))
    codes = [
        (await c.post("/api/notes", json={"title": f"t{i}"}, headers=h)).status_code
        for i in range(3)
    ]
    assert codes == [201, 201, 429]
