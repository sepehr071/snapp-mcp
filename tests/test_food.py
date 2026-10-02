import json

import httpx
import pytest
from conftest import fixture

pytestmark = pytest.mark.anyio

SHIRAZ = {"lat": 29.61695, "long": 52.53099}


async def test_food_search(client, api):
    api["/search/api/v5/search"] = fixture("food_search.json")
    result = await client.call_tool("food_search", {"query": "پیتزا", **SHIRAZ, "discounted_only": True})
    out = result.structured_content
    dishes = out["dishes"]
    assert out["total"] == 1291
    assert dishes[3] == {
        "id": 21543750, "title": "پیتزا تورینو ", "price": 834000, "discount_pct": 0, "final_price": 834000,
        "rating": 3.5, "vendor_code": "09o21d", "vendor": "فست فود پینو پینو (قصرالدشت)", "delivery_fee": 40000,
        "min_order": 300000, "open": True, "sponsored": False,
    }
    assert len(api.calls) == 1  # relevance: search index only, no menu calls
    sent = api.calls[0].url.params
    assert sent["filters"] == '["has_discount"]' and sent["client"] == "PWA" and "superType" not in sent


def _search_item(id, title, price, ratio, vendor):
    return {"id": id, "title": title, "price": price, "discountRatio": ratio, "priceAfterDiscount": price * (100 - ratio) // 100,
            "normalized_rating": 8, "vendor": {"code": vendor, "title": vendor, "deliveryFee": 0}}


def _menu(*categories):
    menus = [{"category": cat, "categoryId": i + 1, "products": [
        {"id": id, "title": title, "price": price, "discount": discount, "discountRatio": discount * 100 // price, "containerPrice": 0}
        for id, title, price, discount in products
    ]} for i, (cat, products) in enumerate(categories)]
    return {"status": True, "data": {"vendor": {"title": "v"}, "menus": menus}}


async def test_food_search_cheapest_uses_live_menu_prices(client, api):
    api["/search/api/v5/search"] = {"status": True, "data": {"product_variations": {"total": 4, "items": [
        _search_item(1, "پیتزا میکس", 438000, 90, "v1"),  # stale index: the menu has no discount
        _search_item(2, "سس پیتزا", 30000, 0, "v1"),  # side item
        _search_item(3, "پیتزا پپرونی", 479000, 30, "v2"),
        _search_item(4, "پیتزا گوشت", 300000, 0, "v2"),  # no longer on the menu
    ]}}}
    menus = {
        "v1": _menu(("پیتزا", [(1, "پیتزا میکس", 438000, 0)]), ("سرویس اضافه", [(2, "سس پیتزا", 30000, 0)])),
        "v2": _menu(("پیتزا", [(3, "پیتزا پپرونی", 479000, 143700)])),
    }
    api["/mobile/v2/restaurant/details/dynamic"] = lambda r: httpx.Response(200, json=menus[r.url.params["vendorCode"]])
    result = await client.call_tool("food_search", {"query": "پیتزا", **SHIRAZ, "sort": "cheapest"})
    dishes = result.structured_content["dishes"]
    assert [(d["id"], d["final_price"], d["discount_pct"]) for d in dishes] == [(3, 335300, 30), (1, 438000, 0)]


async def test_food_search_protein_makes_second_call(client, api):
    api["/search/api/v5/search"] = fixture("food_search.json")
    result = await client.call_tool("food_search", {"query": "مرغ", **SHIRAZ, "include_protein": True})
    assert [c.url.params.get("superType") for c in api.calls] == [None, "[11]"]
    assert len(result.structured_content["dishes"]) == 4  # deduped by id


async def test_food_find_cheapest(client, api):
    vendors = fixture("food_vendors_list.json")
    vendors["data"]["finalResult"][2]["data"]["is_open"] = False  # closed: never scanned
    rows = vendors["data"]["finalResult"]
    rows.append({**rows[0], "data": {**rows[0]["data"], "code": "0nqj9r"}})  # party vendor, open
    rows.append({**rows[0], "data": {**rows[0]["data"], "code": "p8gq62", "is_open": False}})  # party vendor, closed
    api["/search/api/v1/desktop/vendors-list"] = vendors
    api["/search/api/v4/food-party"] = fixture("food_party.json")

    def menu(request):
        if request.url.params["vendorCode"] == "0rx16o":
            return httpx.Response(500)
        return httpx.Response(200, json=fixture("food_menu.json"))

    api["/mobile/v2/restaurant/details/dynamic"] = menu
    result = await client.call_tool("food_find_cheapest", {"query": "پیتزا", **SHIRAZ, "max_restaurants": 5})
    out = result.structured_content
    assert out["restaurants_scanned"] == 3 and out["failed_calls"] == 1
    first = out["dishes"][0]
    assert first["title"] == "مینی پیتزا پپرونی" and first["total"] == 416500 and first["source"] == "food_party"
    assert "p8gq62" not in {d["vendor_code"] for d in out["dishes"]}  # closed vendor's party deal dropped
    menu_dish = next(d for d in out["dishes"] if d["source"] == "menu")
    assert menu_dish["vendor_code"] == "p6mr7e" and menu_dish["total"] == 1208000 + 32000 and menu_dish["min_order"] == 699000
    assert "تنوری برگر" not in {d["title"] for d in out["dishes"]}
    assert {c.url.params.get("vendorCode") for c in api.calls} >= {"p6mr7e", "0rx16o"}
    assert "pz88qe" not in {c.url.params.get("vendorCode") for c in api.calls}


async def test_food_restaurants(client, api):
    api["/search/api/v1/desktop/vendors-list"] = fixture("food_vendors_list.json")
    result = await client.call_tool("food_restaurants", {**SHIRAZ, "has_discount": True, "free_delivery": True, "sort": "max_rate"})
    rows = result.structured_content["restaurants"]
    assert rows[1] == {
        "code": "0rx16o", "title": "فست فود تند و تیز هدایت", "rating": 4.2, "reviews": 5730, "open": True,
        "delivers": True, "delivery_fee": 21700, "eta_min": 65, "discount_pct": 25,
        "best_coupon": "ارسال رایگان و 5% تخفیف ویژه کاربران PRO",
    }
    assert rows[0]["best_coupon"] is None
    sent = api.calls[0].url.params
    assert json.loads(sent["filters"]) == {"filters": ["delivery_fee_until_0", "has_discount"], "sortings": ["max_rate"]}
    assert sent["page_size"] == "20" and sent["sp_alias"] == "restaurant"


async def test_food_restaurants_name_filter(client, api):
    api["/search/api/v1/desktop/vendors-list"] = fixture("food_vendors_list.json")
    result = await client.call_tool("food_restaurants", {**SHIRAZ, "query": "برگر"})
    assert [r["code"] for r in result.structured_content["restaurants"]] == ["p6mr7e"]
    assert api.calls[0].url.params["page_size"] == "500"


async def test_food_menu(client, api):
    menu = fixture("food_menu.json")
    popular, diet, italian = menu["data"]["menus"]
    popular["products"].append(dict(diet["products"][0]))  # popular repeats real items
    diet["products"][1].update(discount=238000, discountRatio=19, containerPrice=10000)
    italian["products"][1]["disabledUntil"] = "2026-10-03 12:00:00"
    api["/mobile/v2/restaurant/details/dynamic"] = menu

    result = await client.call_tool("food_menu", {"vendor_code": "947evd", **SHIRAZ})
    out = result.structured_content
    assert out["vendor"] == {
        "code": "947evd", "title": "پیتزا وای فای (سرباز)", "rating": 4.7, "reviews": 3275, "open": True,
        "min_order": 699000, "delivery_fee": 25000,
    }
    items = {i["id"]: i for i in out["items"]}
    assert out["total_items"] == 6 and len(out["items"]) == 6
    assert items[34224007]["category"] == "پیتزا رژیمی"
    assert items[34224063] == {
        "id": 34224063, "title": "پیتزا رژیمی فیله گوشت", "category": "پیتزا رژیمی", "price": 1238000,
        "discount_pct": 19, "final_price": 1000000, "packaging": 10000, "available": True,
    }
    assert items[26639918]["available"] is False
    assert api.calls[0].url.params["vendorCode"] == "947evd"

    filtered = await client.call_tool("food_menu", {"vendor_code": "947evd", **SHIRAZ, "query": "رژیمی", "limit": 1})
    assert filtered.structured_content["total_items"] == 2 and len(filtered.structured_content["items"]) == 1


async def test_food_menu_rejects_bad_vendor_code(client, api):
    result = await client.call_tool("food_menu", {"vendor_code": "../x", **SHIRAZ})
    assert result.is_error and not api.calls


async def test_food_order_costs(client, api):
    api["/mobile/v2/restaurant/details/state"] = fixture("food_vendor_state.json")
    api["/customer/order/v1/vendor/947evd/min-basket-rules"] = fixture("food_min_basket_rules.json")
    api["/menu-read-model/vendor-rewards/947evd"] = fixture("food_vendor_rewards.json")
    result = await client.call_tool("food_order_costs", {"vendor_code": "947evd", **SHIRAZ})
    out = result.structured_content
    assert out["delivery"] == {"fee": 25000, "discount": 0, "eta_min": 45, "open": True, "state": "ONNRML-1034"}
    assert out["min_order"] == 699000
    assert out["coupons"][0]["only_order_number"] == 1 and out["coupons"][0]["delivery_discount_pct"] == 100
    assert out["coupons"][1]["min_basket"] == 2980000 and out["coupons"][1]["free_items"] == ["نان سیر"]
    assert out["other_rewards"] == [{"type": "cashback", "data": {"is_active": True}}]
    assert "errors" not in out


async def test_food_order_costs_partial_failure(client, api):
    api["/mobile/v2/restaurant/details/state"] = fixture("food_vendor_state.json")
    api["/customer/order/v1/vendor/947evd/min-basket-rules"] = fixture("food_min_basket_rules.json")
    result = await client.call_tool("food_order_costs", {"vendor_code": "947evd", **SHIRAZ})
    out = result.structured_content
    assert out["min_order"] == 699000 and "coupons" not in out
    assert out["errors"][0].startswith("coupons: Not found")


async def test_food_reviews(client, api):
    api["/mobile/v1/restaurant/vendor-comment"] = fixture("food_reviews.json")
    result = await client.call_tool("food_reviews", {"vendor_code": "947evd", "page": 1})
    out = result.structured_content
    assert out["total"] == 1020
    assert out["reviews"][0] == {
        "date": "2026-10-02 21:28:51", "rating": 5.0, "text": "واقعا عالی بود مچکرم",
        "ordered": ["سالاد سزار گریل", "آب معدنی کوچک"], "reply": None,
    }
    assert out["reviews"][2]["reply"]
    assert api.calls[0].url.params["page"] == "1"


async def test_food_party_deals(client, api):
    api["/search/api/v4/food-party"] = fixture("food_party.json")
    result = await client.call_tool("food_party_deals", {**SHIRAZ, "sort": "cheapest"})
    out = result.structured_content
    assert out["active"] and out["ends"] == "2026-10-02T23:59:15+03:30"
    assert [d["id"] for d in out["deals"]] == [37857050, 8305850, 8308366]  # sold-out item dropped
    assert out["deals"][1]["delivery_fee"] == 125000 and out["deals"][1]["rating"] == 4.1
    assert api.calls[0].url.params["deal_project_list_id"] == "23"

    filtered = await client.call_tool("food_party_deals", {**SHIRAZ, "query": "پیتزا"})
    assert [d["id"] for d in filtered.structured_content["deals"]] == [8308366, 37857050]


async def test_food_party_deals_outside_window(client, api):
    api["/search/api/v4/food-party"] = {"success": False, "message": "no active deal"}
    result = await client.call_tool("food_party_deals", SHIRAZ)
    assert result.structured_content == {"active": False, "deals": []}


async def test_food_meal_for_one(client, api):
    api["/search/api/v1/meal-for-one/product-list"] = fixture("food_meal_for_one.json")
    result = await client.call_tool("food_meal_for_one", {**SHIRAZ, "max_price": 247900, "limit": 2})
    out = result.structured_content
    assert len(out["meals"]) == 2
    assert out["meals"][0] == {
        "id": 37360889, "title": "مینی زینگربرگر با سالادکلم", "price": 335000, "discount_pct": 26,
        "final_price": 247900, "rating": 4.2, "vendor_code": "0nqj9r", "vendor": "ایران برگر (فلکه گاز)",
        "open": True, "delivery_fee": 0, "min_order": 420000, "eta_min": 60,
    }
    none = await client.call_tool("food_meal_for_one", {**SHIRAZ, "max_price": 1000})
    assert none.structured_content["meals"] == []


async def test_food_discounted_vendors(client, api):
    api["/search/api/v1/user/off-box"] = fixture("food_off_box.json")
    api["/search/api/v1/user/gem-session"] = fixture("food_gem_session.json")
    api["/search/api/v1/user/gem-vendor-list"] = fixture("food_gem_vendor_list.json")
    result = await client.call_tool("food_discounted_vendors", SHIRAZ)
    out = result.structured_content
    assert out["discounted"][0] == {
        "code": "0nqj9r", "title": "ایران برگر (فلکه گاز)", "promotion": "۳۰% تخفیف پارتی", "open": True,
        "delivery_fee": 0, "eta_min": 60,
    }
    assert out["gem"]["ends"] == "2026-10-02T23:30:00+03:30"
    assert out["gem"]["vendors"][0]["discount"] == "%20 تخفیف شکار" and out["gem"]["vendors"][0]["rating"] == 4.7


async def test_food_discounted_vendors_skips_gem_when_closed(client, api):
    api["/search/api/v1/user/off-box"] = fixture("food_off_box.json")
    api["/search/api/v1/user/gem-session"] = {"success": True, "data": {"can_show": False}}
    result = await client.call_tool("food_discounted_vendors", SHIRAZ)
    assert result.structured_content["gem"] is None
    assert all(c.url.path != "/search/api/v1/user/gem-vendor-list" for c in api.calls)


async def test_food_discounted_vendors_survives_gem_failure(client, api):
    api["/search/api/v1/user/off-box"] = fixture("food_off_box.json")
    api["/search/api/v1/user/gem-session"] = lambda r: httpx.Response(500)
    out = (await client.call_tool("food_discounted_vendors", SHIRAZ)).structured_content
    assert out["discounted"] and out["gem"] is None and out["errors"][0].startswith("gem:")


async def test_food_reviews_null_count(client, api):
    body = fixture("food_reviews.json")
    body["data"]["count"] = None
    api["/mobile/v1/restaurant/vendor-comment"] = body
    out = (await client.call_tool("food_reviews", {"vendor_code": "947evd"})).structured_content
    assert out["total"] is None and out["reviews"]


async def test_food_restaurants_name_filter_pages_after_open_filter(client, api):
    vendors = fixture("food_vendors_list.json")
    rows = vendors["data"]["finalResult"]
    template = rows[0]
    rows[:] = [{**template, "data": {**template["data"], "code": f"c{i:03}", "title": "پیتزا " + str(i), "is_open": i >= 20}} for i in range(25)]
    api["/search/api/v1/desktop/vendors-list"] = vendors
    out = (await client.call_tool("food_restaurants", {**SHIRAZ, "query": "پيتزا"})).structured_content  # Arabic yeh
    assert [r["code"] for r in out["restaurants"]] == [f"c{i:03}" for i in range(20, 25)]


def test_delivery_fee_and_side_items():
    from snapp_mcp.food import _delivery_fee, _has, _is_side

    assert _delivery_fee({"isDeliveryFeeHasDiscount": True, "deliveryFeeAfterDiscount": "50000"}) == 50000
    assert _delivery_fee({"deliveryFee": -1}) is None
    assert _is_side("پیتزا", "سس مخصوص پیتزا") and not _is_side("سس", "سس پیتزا")
    assert _is_side("پیتزا", "پیتزا", "پیش\u200cغذا") and not _is_side("پیتزا", "پیتزا میکس", "پیتزا")
    assert _has("كباب", "چلو کباب") and _has("پیش غذا", "پیش\u200cغذا")
