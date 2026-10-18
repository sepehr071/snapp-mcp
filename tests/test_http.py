import asyncio
import sys

import httpx
import pytest
from conftest import fixture
from mcp import Client
from mcp.types import TextContent

from snapp_mcp import http, market
from snapp_mcp.registry import clip
from snapp_mcp.server import mcp


@pytest.mark.anyio
async def test_get_cached_and_retried_once(api, monkeypatch):
    async def no_sleep(_):
        pass

    monkeypatch.setattr(http.asyncio, "sleep", no_sleep)
    hits = iter([httpx.Response(503), httpx.Response(200, json={"a": 1})])
    api["/x"] = lambda request: next(hits)

    assert await http.fetch("https://snappfood.ir/x", {"q": 1}) == {"a": 1}
    assert await http.fetch("https://snappfood.ir/x", {"q": 1}) == {"a": 1}  # served from cache
    assert len(api.calls) == 2


@pytest.mark.anyio
async def test_post_not_retried(api):
    api["/t"] = lambda request: httpx.Response(503)
    with pytest.raises(http.ApiError):
        await http.fetch("https://snappfood.ir/t", method="POST", json={})
    assert len(api.calls) == 1


def test_clip():
    assert clip(None) is None and clip("ab") == "ab"
    assert clip("x" * 500) == "x" * 400 + "...[cut]"
    assert clip("x" * 500, True) == "x" * 500


@pytest.mark.anyio
async def test_cached_body_is_not_shared(api):
    api["/m"] = lambda request: httpx.Response(200, json={"a": [1]})
    first = await http.fetch("https://snappfood.ir/m")
    first["a"].append(2)
    assert await http.fetch("https://snappfood.ir/m") == {"a": [1]}


@pytest.mark.anyio
async def test_unknown_host_and_redirect_refused(api):
    with pytest.raises(http.ApiError, match="unexpected host"):
        await http.fetch("https://evil.example/x")
    api["/r"] = lambda request: httpx.Response(302, headers={"Location": "https://evil.example/"})
    with pytest.raises(http.ApiError, match="non-JSON"):
        await http.fetch("https://snappfood.ir/r")
    assert len(api.calls) == 1


@pytest.mark.anyio
async def test_oversized_body_refused(api, monkeypatch):
    monkeypatch.setattr(http, "MAX_BODY", 100)
    api["/big"] = lambda request: httpx.Response(200, content=b"[" + b"1," * 100 + b"1]")
    with pytest.raises(http.ApiError, match="refused"):
        await http.fetch("https://snappfood.ir/big")


@pytest.mark.anyio
async def test_concurrent_first_calls_fetch_one_token(api, monkeypatch):
    monkeypatch.setattr(market, "_token", None)
    api["/oauth2/default/token"] = {"status": True, "data": {"access_token": "tok"}}
    api["/a"] = {"ok": 1}
    await asyncio.gather(*(market._fetch_auth("https://svc.snapp.market/a", {"i": i}) for i in range(5)))
    assert [c.url.path for c in api.calls].count("/oauth2/default/token") == 1


@pytest.mark.anyio
async def test_tool_result_is_one_text_block_and_no_output_schema(api):
    api["/map/address/reverse"] = fixture("food_reverse_geocode.json")
    async with Client(mcp, raise_exceptions=True) as c:  # real client, without the conftest patch
        tools = (await c.list_tools()).tools
        result = await c.call_tool("reverse_geocode", {"lat": 35.7, "long": 51.4})
    assert all(t.output_schema is None for t in tools)
    assert "title" not in str(tools[0].input_schema.get("properties"))
    assert len(result.content) == 1 and isinstance(result.content[0], TextContent)
    assert result.structured_content is None


def test_is_challenge():
    html = {"content-type": "text/html"}
    page = b"<html>Just a moment...</html>"
    assert http._is_challenge(httpx.Response(403, headers=html), page)
    assert http._is_challenge(httpx.Response(503), b"<!doctype html><title>ArvanCloud</title>")
    assert not http._is_challenge(httpx.Response(403), b"<html>Access Denied</html>")
    assert not http._is_challenge(httpx.Response(403, headers={"content-type": "application/json"}), b'{"a": 1}')
    assert not http._is_challenge(httpx.Response(200, headers=html), page)


@pytest.mark.anyio
async def test_frontend_headers_per_host(api):
    api["/a"] = {"ok": 1}
    await http.fetch("https://snappfood.ir/a")
    await http.fetch("https://apigw.snappfood.ir/a")
    await http.fetch("https://svc.snapp.market/a")
    await http.fetch("https://svc.snapp.market/a", method="POST", json={})
    food, gw, market_get, market_post = (c.headers for c in api.calls)
    assert food["Referer"] == "https://snappfood.ir/" and "Origin" not in food
    assert food["sec-fetch-site"] == "same-origin"
    assert (gw["Origin"], gw["sec-fetch-site"]) == ("https://snappfood.ir", "same-site")
    assert (market_get["Origin"], market_get["Referer"]) == ("https://snapp.market", "https://snapp.market/")
    assert market_post["Origin"] == "https://snapp.market"
    assert "User-Agent" not in food or "Chrome/154" not in food["User-Agent"]


@pytest.mark.anyio
async def test_retries_up_to_twice_but_never_after_403(api):
    api["/e"] = lambda request: httpx.Response(429, json={"a": 1})
    with pytest.raises(http.ApiError, match="rate limiting"):
        await http.fetch("https://snappfood.ir/e")
    assert len(api.calls) == 3
    api["/f"] = lambda request: httpx.Response(403, json={"a": 1})
    with pytest.raises(http.ApiError, match="blocked"):
        await http.fetch("https://snappfood.ir/f")
    assert sum(c.url.path == "/f" for c in api.calls) == 1


@pytest.mark.anyio
async def test_retry_after_is_honoured_and_capped(api, monkeypatch):
    waits = []

    async def fake_sleep(s):
        waits.append(s)

    monkeypatch.setattr(http.asyncio, "sleep", fake_sleep)
    hits = iter(
        [httpx.Response(429, headers={"Retry-After": "3"}), httpx.Response(503, headers={"Retry-After": "999"})]
    )
    api["/r"] = lambda request: next(hits, httpx.Response(200, json={"a": 1}))
    assert await http.fetch("https://snappfood.ir/r") == {"a": 1}
    assert [w for w in waits if w] == [3, http.MAX_RETRY_WAIT]


@pytest.mark.anyio
async def test_proxy_list_rotates_on_connect_error(monkeypatch, api):
    monkeypatch.setenv("SNAPP_MCP_PROXY", "http://p1:1, http://p2:2")
    assert http._proxies() == ["http://p1:1", "http://p2:2"]

    def boom(request):
        raise httpx.ConnectError("down", request=request)

    api["/c"] = boom
    http._get_client()
    with pytest.raises(http.ApiError):
        await http.fetch("https://snappfood.ir/c")
    assert http._proxy_index >= 1 and len(api.calls) == 3  # retried; later attempts ran on the next proxy
    old, _ = http._get_client()
    index = http._proxy_index
    http._next_proxy(object())  # a late failure on a retired client must not skip the proxy in use
    assert http._proxy_index == index and http._client is old


@pytest.mark.anyio
async def test_challenge_cleared_by_browser_once(monkeypatch, api):
    runs = []

    async def fake_browser(site, proxy):
        runs.append(site)
        await asyncio.sleep(0.01)
        return [{"name": "clearance", "value": "ok", "domain": ".snappfood.ir", "path": "/"}]

    monkeypatch.setattr(http, "_browser_cookies", fake_browser)
    blocked = httpx.Response(403, text="<html>Just a moment</html>", headers={"content-type": "text/html"})

    def guarded(request):
        return httpx.Response(200, json={"a": 1}) if "clearance=ok" in request.headers.get("cookie", "") else blocked

    api["/g"] = guarded
    await asyncio.gather(http.fetch("https://snappfood.ir/g", {"a": 1}), http.fetch("https://snappfood.ir/g", {"a": 2}))
    assert runs == ["https://snappfood.ir"]  # the second caller reused the cookies the first one fetched

    api["/h"] = lambda request: httpx.Response(403, text="<html>Just a moment</html>")
    with pytest.raises(http.ApiError) as e:  # still blocked after the one retry: final
        await http.fetch("https://snappfood.ir/h")
    assert e.value.status == 403 and sum(c.url.path == "/h" for c in api.calls) == 2


@pytest.mark.anyio
async def test_challenge_without_patchright(monkeypatch, api):
    monkeypatch.setitem(sys.modules, "patchright.async_api", None)  # makes the import fail
    api["/p"] = lambda request: httpx.Response(403, text="<html>Just a moment</html>")
    with pytest.raises(http.ApiError, match=r"snapp-mcp\[browser\].*patchright install chromium"):
        await http.fetch("https://snappfood.ir/p")


@pytest.mark.anyio
async def test_default_transport_ignores_env_proxy(monkeypatch):
    from curl_cffi.const import CurlOpt

    monkeypatch.setenv("HTTPS_PROXY", "http://env-proxy:1")
    client, _ = http._get_client()
    assert client._transport._session.curl_options[CurlOpt.NOPROXY] == "*"
