import asyncio

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
