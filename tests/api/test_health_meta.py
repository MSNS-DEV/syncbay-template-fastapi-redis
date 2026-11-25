import httpx

from tests.api.conftest import AppFactory


async def test_health_ok(client: httpx.AsyncClient) -> None:
    res = await client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "checks": {"db": "ok", "redis": "ok"}}
    assert res.headers["cache-control"] == "no-store"


async def test_health_reports_failing_redis(make_client: AppFactory) -> None:
    broken = await make_client(redis_url="redis://127.0.0.1:1/0")
    res = await broken.get("/health")
    assert res.status_code == 503
    assert res.json() == {"status": "error", "checks": {"db": "ok", "redis": "error"}}


async def test_config_json_reflects_branding(make_client: AppFactory) -> None:
    c = await make_client(brand_name="Acme", brand_primary="#ff0066")
    res = await c.get("/config.json")
    assert res.json() == {"brandName": "Acme", "brandLogoUrl": "", "brandPrimary": "#ff0066"}


async def test_ui_and_security_headers(client: httpx.AsyncClient) -> None:
    res = await client.get("/")
    assert res.status_code == 200
    assert "<main" in res.text
    csp = res.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp
    assert "script-src 'self'" in csp
    assert res.headers["x-content-type-options"] == "nosniff"
    assert res.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    static = await client.get("/static/app.js")
    assert static.status_code == 200


async def test_openapi_docs(client: httpx.AsyncClient) -> None:
    spec = (await client.get("/openapi.json")).json()
    assert "/api/notes" in spec["paths"]
    docs = await client.get("/docs")
    assert docs.status_code == 200
    assert "cdn.jsdelivr.net" in docs.headers["content-security-policy"]
