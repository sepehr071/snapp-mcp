"""Smoke tests against the real Snappfood API (Shiraz test point). Run with: pytest -m live"""

import pytest

pytestmark = [pytest.mark.anyio, pytest.mark.live]

SHIRAZ = {"lat": 29.61695, "long": 52.53099}
VENDOR = "947evd"


async def call(client, name, args):
    result = await client.call_tool(name, args)
    assert not result.is_error, result.content[0].text
    return result.structured_content


async def test_search(client):
    out = await call(client, "food_search", {"query": "پیتزا", **SHIRAZ, "limit": 5})
    assert out["dishes"] and out["dishes"][0]["vendor_code"]


async def test_find_cheapest(client):
    out = await call(client, "food_find_cheapest", {"query": "پیتزا", **SHIRAZ, "max_restaurants": 3})
    assert out["restaurants_scanned"] == 3
    totals = [d["total"] for d in out["dishes"]]
    assert totals == sorted(totals)


async def test_restaurants(client):
    out = await call(client, "food_restaurants", {**SHIRAZ, "sort": "max_rate"})
    assert all(r["code"] and r["open"] for r in out["restaurants"])


async def test_menu(client):
    out = await call(client, "food_menu", {"vendor_code": VENDOR, **SHIRAZ, "limit": 5})
    assert out["vendor"]["code"] == VENDOR and out["vendor"]["min_order"] > 0
    assert out["items"] and all(i["final_price"] > 0 for i in out["items"])


async def test_order_costs(client):
    out = await call(client, "food_order_costs", {"vendor_code": VENDOR, **SHIRAZ})
    assert "errors" not in out, out["errors"]
    assert out["min_order"] > 0 and out["delivery"]["fee"] is not None


async def test_reviews(client):
    out = await call(client, "food_reviews", {"vendor_code": VENDOR})
    assert out["total"] and out["reviews"]


async def test_party_deals(client):
    out = await call(client, "food_party_deals", {**SHIRAZ, "limit": 5})
    assert out["active"] is False or all(d["remaining"] > 0 for d in out["deals"])


async def test_meal_for_one(client):
    out = await call(client, "food_meal_for_one", {**SHIRAZ, "limit": 5})
    assert all(m["final_price"] <= 299000 for m in out["meals"])


async def test_discounted_vendors(client):
    out = await call(client, "food_discounted_vendors", SHIRAZ)
    assert "discounted" in out and "gem" in out
