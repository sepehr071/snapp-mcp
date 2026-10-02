"""SnappMarket tools (grocery stores, products, deals)."""

from __future__ import annotations

import asyncio
import uuid
from typing import Annotated, Any, Literal

from pydantic import Field

from .http import MARKET_BASE, MARKET_PARAMS, ApiError, fetch
from .location import Lat, Long
from .registry import tool

VendorCode = Annotated[str, Field(pattern=r"^[0-9a-z]{4,10}$", description="Store code from market_search / market_stores, e.g. '32xxwe'.")]
Page = Annotated[int, Field(ge=0, le=50, description="Page number, from 0.")]

# Anonymous token for the endpoints that answer 401 without one (valid 3 days).
_token: str | None = None
_device_uid = str(uuid.uuid4())


@tool("Search groceries")
async def market_search(
    query: Annotated[str, Field(min_length=2, description="Product name, Persian works best, e.g. 'شیر' or 'ماست کاله'.")],
    lat: Lat,
    long: Long,
    page: Page = 0,
    limit: Annotated[int, Field(ge=1, le=30, description="Stores per page.")] = 20,
) -> dict[str, Any]:
    """Search a grocery product across all SnappMarket stores delivering to the point.

    Use to see which stores carry an item and at what price. Returns stores (open or not)
    with up to ~5 matching products each. For a single flat cheapest-first list use
    market_find_cheapest instead.
    """
    data = await _search(query, lat, long, page, limit)
    return {
        "total_products": data.get("total_product"),
        "total_stores": data.get("total_vendor"),
        "stores": [
            {
                **_store(v),
                "products": [_product(p) for p in v.get("products") or []],
            }
            for v in data.get("items") or []
        ],
    }


@tool("Find cheapest groceries")
async def market_find_cheapest(
    query: Annotated[str, Field(min_length=2, description="Product name, Persian works best, e.g. 'شیر کم چرب'.")],
    lat: Lat,
    long: Long,
    pages: Annotated[int, Field(ge=1, le=5, description="Search pages to scan, 12 stores each.")] = 3,
    limit: Annotated[int, Field(ge=1, le=100, description="Max offers to return.")] = 20,
) -> dict[str, Any]:
    """Find the cheapest in-stock offers for a grocery product near the point.

    Use when the user wants the lowest price for an item. Scans the search results of
    open stores that deliver to the point and returns one flat list sorted by final price
    (price - discount). Check delivery_fee and min_order before recommending a store.
    """
    results = await asyncio.gather(*(_search(query, lat, long, page, 12) for page in range(pages)))
    offers = []
    for data in results:
        for v in data.get("items") or []:
            if not (v.get("is_open") and v.get("deliver") and v.get("does_deliver", True)):
                continue
            store = {"store_code": v.get("code"), "store_title": v.get("title"), "delivery_fee": v.get("deliveryFee"), "min_order": v.get("minOrder")}
            offers += [{**_product(p), **store} for p in v.get("products") or [] if (p.get("stock") or 0) > 0]
    offers.sort(key=lambda o: o["final_price"])
    return {"offers": offers[:limit], "offers_found": len(offers)}


@tool("List grocery stores")
async def market_stores(
    lat: Lat,
    long: Long,
    sort: Annotated[
        Literal["default", "lowest_delivery_price", "highest_rating"], Field(description="Order of the stores.")
    ] = "lowest_delivery_price",
    page: Page = 0,
) -> dict[str, Any]:
    """List SnappMarket stores delivering to the point, with delivery fee, minimum order and rating.

    Use to pick a store (cheapest delivery or best rated) before browsing it with
    market_store_products.
    """
    body = await fetch(
        f"{MARKET_BASE}/express-vendor/general/vendors-list",
        {
            "lat": lat,
            "long": long,
            "sort": sort,
            "page": page,
            "page_size": 18,
            "is_home": "false",
            "page_type": "vendor_list",
            "extra-filter[vendor_collection]": -1,
        },
        common=MARKET_PARAMS,
    )
    data = body["data"]
    stores = []
    for item in data.get("finalResult") or []:
        d = item.get("data") or {}
        if item.get("type") != "VENDOR":
            continue
        stores.append(
            {
                "code": d.get("code"),
                "title": d.get("title"),
                "area": d.get("area"),
                "rating": _rating(d.get("rating")),
                "comment_count": d.get("commentCount"),
                "open": d.get("isOpen"),
                "delivery_fee": d.get("deliveryFee"),
                "delivery_time_min": d.get("deliveryTime"),
                "min_order": d.get("minimumOrderValue"),
                "is_pro": d.get("is_pro"),
                "coupons": [c.get("title") for c in d.get("coupons") or []],
            }
        )
    return {"count": data.get("count"), "open_count": data.get("open_count"), "stores": stores}


@tool("Grocery store details")
async def market_store_info(vendor_code: VendorCode, lat: Lat, long: Long) -> dict[str, Any]:
    """Get one store's delivery fee, minimum order, opening hours, rating and coupons for the point.

    Use before recommending a store to know the true cost of a basket:
    sum(final prices) + delivery_fee - applicable coupon, and the basket must reach min_order.
    Read each coupon's conditions; isApplicable is false for logged-out users.
    """
    loc = {"lat": lat, "long": long}
    body = await _fetch_auth(f"{MARKET_BASE}/express-home/vendor/{vendor_code}", {**loc, "variable": vendor_code})
    v = body.get("vendorInfo")
    if not v:
        raise ApiError(f"Unknown store code '{vendor_code}'. Get codes from market_stores or market_search.")
    delivery = v.get("deliveryData") or {}
    result: dict[str, Any] = {
        "code": v.get("code"),
        "id": v.get("id"),
        "title": v.get("title"),
        "address": v.get("address"),
        "area": v.get("area"),
        "rating": v.get("rating") or None,  # already 0-5 here, unlike the store lists
        "review_stars_percent": v.get("reviewStars"),
        "comment_count": v.get("commentCount"),
        "open": v.get("isOpen"),
        "state": v.get("vendorStateText"),
        "delivers_here": delivery.get("doesDeliver"),
        "delivery_status": v.get("deliveryStatus"),
        "delivery_fee": delivery.get("deliveryFee"),
        "min_order": v.get("minimumOrderValue"),
        "distance_m": delivery.get("distance"),
        "is_pro": v.get("is_pro"),
        "badges": [b.get("name") for b in v.get("badges") or []],
        "hours": [
            {"weekday": s.get("weekDay"), "open": s.get("startHour"), "close": s.get("stopHour"), "type": s.get("type")}
            for s in v.get("schedules") or []
        ],
    }
    # Coupons need the numeric id from vendor-info, and the store's pro flag or pro coupons are hidden.
    try:
        body = await _fetch_auth(
            f"{MARKET_BASE}/belladonna/api/v1/coupon/vendor/{v['id']}", {**loc, "vendorPro": str(bool(v.get("is_pro"))).lower()}
        )
        result["coupons"] = [
            {
                "title": c.get("title"),
                "description": c.get("description"),
                "conditions": [x["title"] for x in c.get("conditions") or [] if x.get("title")],
                "reward_mode": c.get("rewardMode"),
                "reward_value": c.get("rewardValue"),
                "is_applicable": c.get("isApplicable"),
            }
            for c in body.get("coupons") or []
        ]
    except ApiError as e:
        result["errors"] = [f"coupons: {e}"]
    return result


@tool("Browse a grocery store")
async def market_store_products(
    vendor_code: VendorCode,
    lat: Lat,
    long: Long,
    query: Annotated[str | None, Field(min_length=2, description="Search text inside this store, e.g. 'ماست'.")] = None,
    category_id: Annotated[int | None, Field(ge=1, description="Top-level category id (from this tool without arguments, or market_categories).")] = None,
    subcategory_id: Annotated[int | None, Field(ge=1, description="Sub-category id; needs its parent category_id.")] = None,
    page: Page = 0,
) -> dict[str, Any]:
    """Browse or search the products of one store.

    - With query: search the store (takes precedence over category ids).
    - With category_id (and optional subcategory_id): list that category's products.
    - With neither: list the store's categories and sub-categories with their ids.
    """
    loc = {"lat": lat, "long": long}
    if query:
        body = await fetch(
            f"{MARKET_BASE}/mobile/v2/product-variation/search",
            {**loc, "query": query, "vendorCode": vendor_code, "page": page, "page_size": 20, "size": 20},
            common=MARKET_PARAMS,
        )
        data = body["data"]
        return {"total": data.get("total"), "products": [_product(p) for p in data.get("result") or []]}
    if category_id:
        params = {**loc, "vendor_code": vendor_code, "category_id": category_id, "page": page, "size": 20}
        if subcategory_id:
            params["subcat_id"] = subcategory_id
        try:
            body = await fetch(f"{MARKET_BASE}/express-search/product-list/vendor", params, common=MARKET_PARAMS)
        except ApiError as e:
            if e.status != 204:
                raise
            raise ApiError(
                "No products for that category in this store. Pass a top-level id as category_id and a "
                "sub-category id as subcategory_id (call without ids to list them)."
            ) from e
        data = body.get("data") or {}
        return {
            "category": body.get("category_title"),
            "subcategory": body.get("sub_category_title"),
            "total": data.get("count"),
            "products": [_product(x["data"]) for x in data.get("finalResult") or [] if x.get("data")],
        }
    body = await fetch(f"{MARKET_BASE}/mobile/search/categories", {**loc, "vendorCode": vendor_code}, common=MARKET_PARAMS)
    return {
        "categories": [
            {"id": c.get("id"), "title": c.get("title"), "sub_categories": [{"id": s.get("id"), "title": s.get("title")} for s in c.get("sub_categories") or []]}
            for c in body.get("items") or []
        ]
    }


@tool("Grocery categories")
async def market_categories(lat: Lat, long: Long) -> dict[str, Any]:
    """List SnappMarket product categories and sub-categories with their ids.

    The ids work as category_id / subcategory_id in market_store_products. The first
    entry (id 99999999) is the virtual kalabarg (government e-coupon eligible) category.
    """
    body = await fetch(f"{MARKET_BASE}/express-search/categories", {"lat": lat, "long": long}, common=MARKET_PARAMS)
    categories = []
    for c in body.get("categories") or []:
        cid = c.get("id")
        categories.append(
            {
                "id": cid[0] if isinstance(cid, list) and cid else cid,
                "title": c.get("title"),
                "slug": c.get("slug"),
                "sub_categories": [{"id": s.get("id"), "title": s.get("title")} for s in c.get("sub_categories") or []],
            }
        )
    return {"categories": categories}


@tool("Grocery product details")
async def market_product(
    product_id: Annotated[int, Field(ge=1, description="Product id from a market_* product list.")],
    vendor_code: VendorCode,
) -> dict[str, Any]:
    """Get one product's details and price in one store.

    Use to confirm the current price/discount of an item the user picked.
    """
    body = await fetch(
        f"{MARKET_BASE}/express-search/mobile/v2/product-variation/view",
        {"productVariationId": product_id, "vendorCode": vendor_code},
        common=MARKET_PARAMS,
    )
    d = body["data"]["details"]
    return {
        **_product(d),
        "brand": d.get("brandTitle"),
        "category": d.get("categoryTitle"),
        "subcategory": d.get("subcategoryTitle"),
        "description": d.get("description") or None,
        "badges": [b.get("name") for b in d.get("badges") or []],
        "store_code": vendor_code,
    }


@tool("Grocery party deals")
async def market_party_deals(
    lat: Lat,
    long: Long,
    limit: Annotated[int, Field(ge=1, le=100, description="Max deals to return.")] = 30,
) -> dict[str, Any]:
    """List current SnappMarket "market party" flash deals near the point, biggest discount first.

    Use when the user wants the best grocery discounts right now. Each order may contain
    at most capacity_per_order deal items. segment 'new_user' deals are only for new customers.
    """
    body = await _fetch_auth(
        f"{MARKET_BASE}/landing/market-party/{lat}/{long}", {"lat": lat, "long": long, "deal_type": "supermarket", "isPro": "false"}
    )
    data = body.get("data") or {}
    deals, seen = [], set()
    for group in ("products", "personalizedProducts"):
        for p in (data.get(group) or {}).get("List") or []:
            key = (p.get("productVariationId"), p.get("vendorCode"))
            if key in seen or p.get("is_out_of_stock"):
                continue
            seen.add(key)
            deals.append(
                {
                    **_product({**p, "id": p.get("productVariationId")}),
                    "store_code": p.get("vendorCode"),
                    "store_title": p.get("vendorTitle"),
                    "delivery_fee": int(p.get("deliveryFee") or 0),
                    "min_order": p.get("minOrder"),
                    "segment": p.get("segment"),
                }
            )
    deals.sort(key=lambda d: -d["discount_pct"])
    return {
        "title": data.get("title"),
        "starts_utc": data.get("firstActivePeriodStartRFC"),
        "ends_utc": data.get("firstActivePeriodEndRFC"),
        "capacity_per_order": data.get("capacityPerOrder"),
        "deals": deals[:limit],
    }


@tool("Grocery store reviews")
async def market_reviews(vendor_code: VendorCode) -> dict[str, Any]:
    """Read a store's 30 most relevant customer comments, rating 0-5.

    Use as a quality check before recommending a store. The API returns the same 30
    comments whatever the page, so there is no paging.
    """
    body = await fetch(f"{MARKET_BASE}/mobile/v1/restaurant/vendor-comment", {"vendorCode": vendor_code}, common=MARKET_PARAMS)
    data = body["data"]
    return {
        "count": data.get("count"),
        "comments": [
            {"date": c.get("createdDate"), "rating": _rating(c.get("rating")), "text": c.get("commentText"), "feeling": c.get("feeling")}
            for c in data.get("comments") or []
        ],
    }


async def _search(query: str, lat: float, long: float, page: int, size: int) -> dict[str, Any]:
    body = await fetch(
        f"{MARKET_BASE}/mobile/v3/search",
        {"query": query, "lat": lat, "long": long, "superType": "[4]", "new_search": 1, "page": page, "size": size},
        common=MARKET_PARAMS,
    )
    return body["data"].get("vendor_product_variations") or {}


def _store(v: dict[str, Any]) -> dict[str, Any]:
    return {
        "code": v.get("code"),
        "title": v.get("title"),
        "rating": _rating(v.get("rating")),
        "open": v.get("is_open"),
        "delivery_fee": v.get("deliveryFee"),
        "min_order": v.get("minOrder"),
    }


def _product(p: dict[str, Any]) -> dict[str, Any]:
    price = p.get("price") or 0
    discount = p.get("discount") or 0
    return {
        "id": p.get("id"),
        "title": p.get("title"),
        "brand": p.get("brand"),
        "final_price": int(price - discount),
        "price": price,
        "discount_pct": round(100 * discount / price) if price else 0,
        "stock": p.get("stock"),
    }


def _rating(r: Any) -> float | None:
    """Store lists and comments rate 0-10 (0 = not rated yet); show 0-5 like the site."""
    return round(r / 2, 1) if r else None


async def _fetch_auth(url: str, params: dict[str, Any]) -> Any:
    """GET with the anonymous token, fetching it on first use and once more on 401."""
    global _token
    if _token is None:
        _token = await _new_token()
    try:
        return await fetch(url, params, common=MARKET_PARAMS, headers={"Authorization": f"Bearer {_token}"})
    except ApiError as e:
        if e.status != 401:
            raise
    _token = await _new_token()
    return await fetch(url, params, common=MARKET_PARAMS, headers={"Authorization": f"Bearer {_token}"})


async def _new_token() -> str:
    # Public client credentials shipped in the snapp.market PWA.
    body = await fetch(
        f"{MARKET_BASE}/oauth2/default/token",
        common=MARKET_PARAMS,
        method="POST",
        json={
            "data": {
                "device_uid": _device_uid,
                "client_id": "snappfood_pwa",
                "client_secret": "snappfood_pwa_secret",
                "grant_type": "client_credentials",
                "scope": "mobile_v2 mobile_v1 webview",
            }
        },
    )
    return body["data"]["access_token"]
