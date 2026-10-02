import pytest

pytestmark = [pytest.mark.anyio, pytest.mark.live]

LOC = {"lat": 35.773643, "long": 51.418311}
STORE = "32xxwe"


async def call(client, name, args):
    result = await client.call_tool(name, args)
    assert not result.is_error, result.content[0].text
    return result.structured_content


async def test_market_search(client):
    data = await call(client, "market_search", {"query": "شیر", **LOC, "limit": 3})
    assert data["stores"] and data["stores"][0]["products"]


async def test_market_find_cheapest(client):
    data = await call(client, "market_find_cheapest", {"query": "شیر", **LOC, "pages": 1, "limit": 5})
    prices = [o["final_price"] for o in data["offers"]]
    assert prices == sorted(prices)


async def test_market_stores(client):
    data = await call(client, "market_stores", LOC)
    assert data["stores"] and data["stores"][0]["code"]


async def test_market_store_info(client):
    data = await call(client, "market_store_info", {"vendor_code": STORE, **LOC})
    assert data["id"] == 72395 and data["min_order"] > 0
    assert "errors" not in data


async def test_market_store_products(client):
    data = await call(client, "market_store_products", {"vendor_code": STORE, **LOC, "query": "ماست"})
    assert data["products"]


async def test_market_categories(client):
    data = await call(client, "market_categories", LOC)
    assert any(c["slug"] == "dairy" for c in data["categories"])


async def test_market_product(client):
    data = await call(client, "market_product", {"product_id": 4076320, "vendor_code": STORE})
    assert data["id"] == 4076320 and data["price"] > 0


async def test_market_party_deals(client):
    data = await call(client, "market_party_deals", {**LOC, "limit": 5})
    assert data["capacity_per_order"] is not None


async def test_market_reviews(client):
    data = await call(client, "market_reviews", {"vendor_code": STORE})
    assert data["comments"] and 1 <= data["comments"][0]["rating"] <= 5
