import httpx
import pytest
from conftest import fixture

from snapp_mcp import market

pytestmark = pytest.mark.anyio

LOC = {"lat": 35.773643, "long": 51.418311}
TOKEN = {"status": True, "data": {"access_token": "fresh", "expires_in": 259200, "token_type": "Bearer"}}


async def test_market_search(client, api):
    api["/mobile/v3/search"] = fixture("market_search.json")
    result = await client.call_tool("market_search", {"query": "شیر", **LOC, "limit": 5})
    stores = result.structured_content["stores"]
    assert [s["code"] for s in stores] == ["09egly", "0gkdjr", "0gknr2"]
    assert stores[2]["rating"] == 4.1 and stores[0]["rating"] is None
    assert stores[1]["products"][2] == {
        "id": 4088053,
        "title": "شیر کم چرب روزانه 200 میلی لیتری",
        "brand": "روزانه",
        "final_price": 32000,
        "price": 40000,
        "discount_pct": 20,
        "stock": 122,
    }
    assert api.calls[0].url.params["size"] == "5"


async def test_market_find_cheapest(client, api):
    api["/mobile/v3/search"] = fixture("market_search.json")
    result = await client.call_tool("market_find_cheapest", {"query": "شیر", **LOC, "pages": 1})
    offers = result.structured_content["offers"]
    # closed store 0gknr2 and the out-of-stock offer are skipped
    assert [(o["store_code"], o["final_price"]) for o in offers] == [
        ("0gkdjr", 32000),
        ("09egly", 158000),
        ("0gkdjr", 158000),
        ("0gkdjr", 199000),
    ]
    assert offers[0]["delivery_fee"] == 35000 and offers[0]["min_order"] == 450000


async def test_market_find_cheapest_caps_pages(client, api):
    result = await client.call_tool("market_find_cheapest", {"query": "شیر", **LOC, "pages": 9})
    assert result.is_error and not api.calls


async def test_market_stores(client, api):
    api["/express-vendor/general/vendors-list"] = fixture("market_stores.json")
    result = await client.call_tool("market_stores", {**LOC, "sort": "highest_rating"})
    stores = result.structured_content["stores"]
    assert stores[0]["code"] == "097o9d" and stores[0]["rating"] == 3.8
    assert stores[2]["coupons"] == ["ارسال رایگان"] and stores[2]["delivery_fee"] == 30400
    assert api.calls[0].url.params["sort"] == "highest_rating"


async def test_market_store_info(client, api, monkeypatch):
    monkeypatch.setattr(market, "_token", None)
    api["/oauth2/default/token"] = TOKEN
    api["/express-home/vendor/32xxwe"] = fixture("market_store_info.json")
    api["/belladonna/api/v1/coupon/vendor/72395"] = fixture("market_store_coupons.json")
    result = await client.call_tool("market_store_info", {"vendor_code": "32xxwe", **LOC})
    info = result.structured_content
    assert info["id"] == 72395 and info["min_order"] == 140000 and info["rating"] == 4.3
    assert info["hours"][0] == {"weekday": 1, "open": "08:00", "close": "21:59", "type": "OPENED"}
    assert info["coupons"][0]["reward_value"] == 34000
    assert info["coupons"][0]["conditions"] == ["خرید حداقل 280,000 تومان", "ویژه کاربران پرو"]
    assert "errors" not in info
    assert all(c.headers["Authorization"] == "Bearer fresh" for c in api.calls[1:])
    assert api.calls[2].url.params["vendorPro"] == "true"


async def test_market_store_info_partial_failure(client, api, monkeypatch):
    monkeypatch.setattr(market, "_token", "t")
    api["/express-home/vendor/32xxwe"] = fixture("market_store_info.json")
    api["/belladonna/api/v1/coupon/vendor/72395"] = lambda r: httpx.Response(500, json={})
    info = (await client.call_tool("market_store_info", {"vendor_code": "32xxwe", **LOC})).structured_content
    assert info["title"] and "coupons" not in info
    assert "HTTP 500" in info["errors"][0]


async def test_token_refreshed_once_on_401(client, api, monkeypatch):
    monkeypatch.setattr(market, "_token", "expired")
    api["/oauth2/default/token"] = TOKEN

    def comments(request):
        if request.headers["Authorization"] != "Bearer fresh":
            return httpx.Response(401, json={"error": {"code": 3004}})
        return httpx.Response(200, json=fixture("market_party_deals.json"))

    api["/landing/market-party/35.773643/51.418311"] = comments
    result = await client.call_tool("market_party_deals", LOC)
    assert not result.is_error
    assert [c.url.path for c in api.calls] == [
        "/landing/market-party/35.773643/51.418311",
        "/oauth2/default/token",
        "/landing/market-party/35.773643/51.418311",
    ]
    assert market._token == "fresh"


async def test_market_store_products_modes(client, api):
    api["/mobile/v2/product-variation/search"] = fixture("market_store_products.json")
    api["/mobile/search/categories"] = fixture("market_store_categories.json")
    api["/express-search/product-list/vendor"] = fixture("market_store_category_products.json")
    base = {"vendor_code": "32xxwe", **LOC}

    found = (await client.call_tool("market_store_products", {**base, "query": "ماست"})).structured_content
    assert found["total"] == 75 and found["products"][0]["id"] == 5260662

    cats = (await client.call_tool("market_store_products", base)).structured_content
    assert cats["categories"][0] == {"id": 731205, "title": "لبنیات و بستنی", "sub_categories": [{"id": 731224, "title": "شیر"}, {"id": 731225, "title": "پنیر آشپزی"}]}

    listed = (await client.call_tool("market_store_products", {**base, "category_id": 731205, "subcategory_id": 731225})).structured_content
    assert listed["total"] == 409 and listed["products"][0]["price"] == 658000
    assert api.calls[-1].url.params["subcat_id"] == "731225"


async def test_market_store_products_wrong_category(client, api):
    api["/express-search/product-list/vendor"] = lambda r: httpx.Response(204)
    result = await client.call_tool("market_store_products", {"vendor_code": "32xxwe", **LOC, "category_id": 731224})
    assert result.is_error and "subcategory_id" in result.content[0].text


async def test_market_categories(client, api):
    api["/express-search/categories"] = fixture("market_categories.json")
    cats = (await client.call_tool("market_categories", LOC)).structured_content["categories"]
    assert [c["id"] for c in cats] == [99999999, 731205]
    assert cats[0]["sub_categories"][0] == {"id": 731224, "title": "شیر"}


async def test_market_product(client, api):
    api["/express-search/mobile/v2/product-variation/view"] = fixture("market_product.json")
    p = (await client.call_tool("market_product", {"product_id": 4076320, "vendor_code": "32xxwe"})).structured_content
    assert p["final_price"] == 159000 and p["brand"] == "مانا" and p["badges"] == ["کالابرگ"]
    assert api.calls[0].url.params["productVariationId"] == "4076320"


async def test_market_party_deals(client, api, monkeypatch):
    monkeypatch.setattr(market, "_token", "t")
    api["/landing/market-party/35.773643/51.418311"] = fixture("market_party_deals.json")
    data = (await client.call_tool("market_party_deals", {**LOC, "limit": 10})).structured_content
    assert data["capacity_per_order"] == 2
    # biggest discount first; the product repeated in both lists appears once
    assert [(d["id"], d["discount_pct"]) for d in data["deals"]] == [(7244153, 99), (15011450, 40), (9731846, 34)]
    assert data["deals"][0]["final_price"] == 500 and data["deals"][0]["segment"] == "new_user"


async def test_market_reviews(client, api):
    api["/mobile/v1/restaurant/vendor-comment"] = fixture("market_reviews.json")
    data = (await client.call_tool("market_reviews", {"vendor_code": "32xxwe"})).structured_content
    assert data["count"] == 6723
    assert [c["rating"] for c in data["comments"]] == [1, 5]


async def test_bad_vendor_code_rejected(client, api):
    result = await client.call_tool("market_reviews", {"vendor_code": "../x"})
    assert result.is_error and not api.calls
