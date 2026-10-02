import httpx
import pytest
from conftest import fixture

pytestmark = pytest.mark.anyio


async def test_all_tools_are_read_only(client):
    tools = (await client.list_tools()).tools
    assert tools
    for t in tools:
        assert t.annotations.read_only_hint is True, t.name
        assert t.description and t.title, t.name


async def test_find_location(client, api):
    api["/map/address/place"] = fixture("food_place_search.json")
    result = await client.call_tool("find_location", {"query": "سعدی", "near_lat": 29.61, "near_long": 52.53})
    places = result.structured_content["places"]
    assert places[0]["name"] == "ارامگاه سعدی"
    assert places[0]["lat"] == pytest.approx(29.6224825)
    sent = api.calls[0].url.params
    assert sent["lon"] == "52.53" and "client" not in sent


async def test_reverse_geocode(client, api):
    api["/map/address/reverse"] = fixture("food_reverse_geocode.json")
    result = await client.call_tool("reverse_geocode", {"lat": 29.61695, "long": 52.53099})
    assert result.structured_content["labels"][2] == {"name": "محله زند", "type": "neighbourhood"}


async def test_list_cities_filter(client, api):
    api["/mobile/v2/area/cities"] = fixture("food_cities.json")
    result = await client.call_tool("list_cities", {"query": "mashhad"})
    assert [c["title"] for c in result.structured_content["cities"]] == ["مشهد"]


async def test_waf_block_is_actionable(client, api):
    api["/mobile/v2/area/cities"] = lambda r: httpx.Response(403, text="blocked")
    result = await client.call_tool("list_cities", {})
    assert result.is_error
    assert "Iranian IP" in result.content[0].text
