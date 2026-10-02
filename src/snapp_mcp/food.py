"""Snappfood tools (restaurants, dishes, deals)."""

from __future__ import annotations

import asyncio
import json
from typing import Annotated, Any, Literal

from pydantic import Field

from .http import FOOD_APIGW, FOOD_BASE, FOOD_PARAMS, ApiError, fetch
from .location import Lat, Long
from .registry import tool

VendorCode = Annotated[str, Field(pattern=r"^[A-Za-z0-9]{3,12}$", description="Snappfood vendor code, e.g. '947evd' (from food_search / food_restaurants).")]
Query = Annotated[str, Field(min_length=2, description="Dish or food word, Persian works best, e.g. 'پیتزا', 'کباب'.")]

PAGE_SIZE = 20
FOOD_PARTY_LIST = 23  # main FoodParty deal list; 56 is the protein one


@tool("Search dishes")
async def food_search(
    query: Query,
    lat: Lat,
    long: Long,
    discounted_only: Annotated[bool, Field(description="Only dishes with a discount.")] = False,
    sort: Annotated[Literal["relevance", "cheapest", "biggest_discount"], Field(description="Order of the results.")] = "relevance",
    include_protein: Annotated[bool, Field(description="Also search protein stores (butchers, poultry, eggs), which are hidden by default.")] = False,
    limit: Annotated[int, Field(ge=1, le=50, description="Max dishes to return.")] = 20,
) -> dict[str, Any]:
    """Search dishes by name near a delivery point, with price, discount and restaurant.

    Use for "find X near me" or "discounted X". With sort=relevance it is one fast call
    on the search index; cheapest / biggest_discount also drop side items (sauces,
    drinks, extras) and confirm prices against the live menus. For the cheapest X across
    many restaurant menus use food_find_cheapest.
    """
    # Sorting a short relevance page would miss cheaper matches, so fetch a wider pool first.
    size = limit if sort == "relevance" else 100
    params: dict[str, Any] = {"lat": lat, "long": long, "query": query, "page": 0, "page_size": size}
    if discounted_only:
        params["filters"] = '["has_discount"]'
    calls = [fetch(f"{FOOD_BASE}/search/api/v5/search", params, common=FOOD_PARAMS)]
    if include_protein:
        # Protein stores only show up with superType [11], and [1,11] drops them again.
        calls.append(fetch(f"{FOOD_BASE}/search/api/v5/search", {**params, "superType": "[11]"}, common=FOOD_PARAMS))
    bodies = await asyncio.gather(*calls)

    dishes, seen = [], set()
    for body in bodies:
        for it in body["data"]["product_variations"]["items"]:
            if it["id"] in seen:
                continue
            seen.add(it["id"])
            v = it.get("vendor") or {}
            dishes.append({
                "id": it["id"],
                "title": it["title"],
                "price": it["price"],
                "discount_pct": it.get("discountRatio") or 0,
                "final_price": it["priceAfterDiscount"],
                "rating": _stars(it.get("normalized_rating")),
                "vendor_code": v.get("code"),
                "vendor": v.get("title"),
                "delivery_fee": _delivery_fee(v),
                "min_order": v.get("minOrder"),
                "open": v.get("is_open"),
                "sponsored": bool(it.get("is_ads")),
            })
    total = sum(b["data"]["product_variations"]["total"] for b in bodies)
    if sort == "relevance":
        return {"total": total, "dishes": dishes[:limit]}

    key = (lambda d: d["final_price"]) if sort == "cheapest" else (lambda d: -d["discount_pct"])
    dishes = sorted((d for d in dishes if not _is_side(query, d["title"])), key=key)
    # The search index can be stale (seen: a 90% discount the menu no longer had, items
    # already removed from the menu), so re-read the top candidates from live menus.
    # Capped: each distinct vendor costs one menu download.
    dishes = sorted(await _live_prices(dishes[: min(limit * 2, 30)], {"lat": lat, "long": long}, query), key=key)
    if discounted_only:
        dishes = [d for d in dishes if d["discount_pct"] > 0]
    return {"total": total, "dishes": dishes[:limit]}


@tool("Find cheapest dish")
async def food_find_cheapest(
    query: Query,
    lat: Lat,
    long: Long,
    max_restaurants: Annotated[int, Field(ge=1, le=60, description="How many open restaurants' menus to scan (more = slower, more complete).")] = 30,
    limit: Annotated[int, Field(ge=1, le=50, description="Max dishes to return.")] = 20,
) -> dict[str, Any]:
    """Find the cheapest way to get a dish: scans menus of nearby open restaurants plus FoodParty deals.

    Use when the user wants the lowest total price for X. Ranks by total =
    food price after discount + packaging + delivery fee; check min_order, since a cheap
    dish may not reach the restaurant's minimum basket. Slower than food_search.
    """
    loc, q = {"lat": lat, "long": long}, query.strip()
    open_vendors = [v for v in await _vendor_list(loc) if v.get("is_open") and v.get("deliver")]
    # Restaurants named after the dish first, so a small scan still finds them.
    vendors = sorted(open_vendors, key=lambda v: not _has(q, v["title"]))[:max_restaurants]

    party = asyncio.ensure_future(_party(loc))
    menus, skipped = await _live_menus([v["code"] for v in vendors], loc)
    offers = [o for v in vendors if v["code"] in menus for o in _menu_offers(v, menus[v["code"]])]
    try:
        party_body = await party
    except ApiError:
        party_body, skipped = None, skipped + 1
    # FoodParty lists closed vendors too; keep deals that can be ordered now.
    open_codes = {v["code"] for v in open_vendors}
    offers += [o for o in _party_offers(party_body) if o["vendor_code"] in open_codes]

    # Same dish can be in a menu and in FoodParty: keep the cheaper one.
    best: dict[int, dict[str, Any]] = {}
    for o in offers:
        o["total"] = o["food_price"] + o["packaging"] + o["delivery_fee"]
        category = o.pop("category", "")
        if (
            _has(q, o["title"])
            and not _is_side(q, o["title"], category)
            and o["food_price"] > 0
            and (o["id"] not in best or o["total"] < best[o["id"]]["total"])
        ):
            best[o["id"]] = o
    # ponytail: price-only ranking, no per-portion normalization
    dishes = sorted(best.values(), key=lambda o: o["total"])
    return {"restaurants_scanned": len(vendors), "failed_calls": skipped, "dishes": dishes[:limit]}


@tool("List restaurants")
async def food_restaurants(
    lat: Lat,
    long: Long,
    query: Annotated[str | None, Field(description="Filter by restaurant name, e.g. 'پیتزا' or a brand name.")] = None,
    free_delivery: Annotated[bool, Field(description="Only restaurants with free delivery.")] = False,
    has_discount: Annotated[bool, Field(description="Only restaurants running a discount.")] = False,
    has_coupon: Annotated[bool, Field(description="Only restaurants offering a coupon.")] = False,
    sort: Annotated[
        Literal["default", "max_rate", "nearest", "least_expensive", "most_expensive", "top_performance"],
        Field(description="Order; least_expensive/most_expensive rank by price class, not delivery fee."),
    ] = "default",
    page: Annotated[int, Field(ge=0, le=50, description="Zero-based page of 20 restaurants.")] = 0,
    open_only: Annotated[bool, Field(description="Hide restaurants that are closed or don't deliver here.")] = True,
) -> dict[str, Any]:
    """List restaurants delivering to a point, with rating, delivery fee, ETA and coupon info.

    Use to browse or rank restaurants (best rated, free delivery, with discount) or to
    find a restaurant's vendor code by name. No dish prices: use food_menu for those.
    """
    filters: dict[str, list[str]] = {"filters": []}
    if free_delivery:
        filters["filters"].append("delivery_fee_until_0")
    if has_discount:
        filters["filters"].append("has_discount")
    if has_coupon:
        filters["filters"].append("has_coupon")
    if sort != "default":
        filters["sortings"] = [sort]
    loc = {"lat": lat, "long": long}
    # The API ignores `query`, so a name filter fetches every vendor and filters here.
    if query:
        vendors = [v for v in await _vendor_list(loc, filters) if _has(query, v["title"])]
    else:
        vendors = await _vendor_list(loc, filters, page=page, size=PAGE_SIZE)
    if open_only:
        vendors = [v for v in vendors if v.get("is_open") and v.get("deliver")]
    if query:  # page after filtering, so a page is never short while matches remain
        vendors = vendors[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
    return {
        "page": page,
        "restaurants": [
            {
                "code": v["code"],
                "title": v["title"],
                "rating": _stars(v.get("rating")),
                "reviews": v.get("commentCount"),
                "open": v.get("is_open"),
                "delivers": v.get("deliver"),
                "delivery_fee": _delivery_fee(v),
                "eta_min": v.get("eta"),
                "discount_pct": v.get("discountValueForView") or 0,
                "best_coupon": v.get("best_coupon") or None,
            }
            for v in vendors
        ],
    }


@tool("Restaurant menu")
async def food_menu(
    vendor_code: VendorCode,
    lat: Lat,
    long: Long,
    query: Annotated[str | None, Field(description="Only items whose title contains this text.")] = None,
    limit: Annotated[int, Field(ge=1, le=300, description="Max items to return.")] = 100,
) -> dict[str, Any]:
    """Get one restaurant's menu: every item with price, discount, packaging fee and availability.

    Use after picking a restaurant (vendor code from food_search / food_restaurants).
    final_price = price - discount; packaging is added per item at checkout.
    """
    data = (await _menu(vendor_code, {"lat": lat, "long": long}))["data"]
    v = data["vendor"]
    items = _menu_items(data)
    if query:
        items = [p for p in items if _has(query, p["title"])]
    return {
        "vendor": {
            "code": v.get("vendorCode") or vendor_code,
            "title": v.get("title"),
            "rating": _stars(v.get("rating")),
            "reviews": v.get("commentCount"),
            "open": v.get("isOpenNow"),
            "min_order": v.get("minOrder"),
            "delivery_fee": _delivery_fee(v),
        },
        "total_items": len(items),
        "items": [
            {
                "id": p["id"],
                "title": p["title"],
                "category": p["categoryTitle"],
                "price": p["price"],
                "discount_pct": p["discountRatio"],
                "final_price": p["price"] - p["discount"],
                "packaging": p["containerPrice"],
                "available": p["available"],
            }
            for p in items[:limit]
        ],
    }


@tool("Order costs")
async def food_order_costs(vendor_code: VendorCode, lat: Lat, long: Long) -> dict[str, Any]:
    """Get the extra costs and conditions of ordering from one restaurant.

    Use before recommending a restaurant: delivery fee and ETA here, minimum order,
    and coupons (each with the basket total it needs and whether it is first-order only).
    Per-item packaging fees are in food_menu.
    """
    loc = {"lat": lat, "long": long}
    state, rules, rewards = await asyncio.gather(
        fetch(f"{FOOD_BASE}/mobile/v2/restaurant/details/state", {**loc, "vendorCode": vendor_code}, common=FOOD_PARAMS),
        fetch(f"{FOOD_BASE}/customer/order/v1/vendor/{vendor_code}/min-basket-rules", loc, common=FOOD_PARAMS),
        fetch(f"{FOOD_APIGW}/menu-read-model/vendor-rewards/{vendor_code}", loc, common=FOOD_PARAMS),
        return_exceptions=True,
    )
    parts = {"delivery": state, "min_order": rules, "coupons": rewards}
    failed = {name: r for name, r in parts.items() if isinstance(r, BaseException)}
    for r in failed.values():
        if not isinstance(r, ApiError):
            raise r
    if len(failed) == len(parts):
        raise failed["delivery"]

    out: dict[str, Any] = {"vendor_code": vendor_code}
    if not isinstance(state, BaseException):
        d = state["data"]
        delivery = d.get("delivery") or {}
        out["delivery"] = {
            "fee": delivery.get("fee"),
            "discount": delivery.get("discount"),
            "eta_min": delivery.get("eta"),
            # Inferred: closed vendors return a preorder schedule (state ONCLRP-...) or a reopening time.
            "open": d.get("preorder") is None and d.get("reopeningTime") is None,
            "state": d.get("state"),
        }
    if not isinstance(rules, BaseException):
        out["min_order"] = next((r["value"] for r in rules["data"] if r["conditionType"] == "DEFAULT"), None)
    if not isinstance(rewards, BaseException):
        out["coupons"], out["other_rewards"] = [], []
        for r in rewards["data"]:
            c = r.get("coupon_data")
            if not c:
                out["other_rewards"].append({"type": r.get("type"), "data": r.get(f"{r.get('type')}_data")})
                continue
            packet = c.get("reward_packet") or {}
            out["coupons"].append({
                "title": c.get("title"),
                "reward": c.get("reward"),
                "delivery_discount_pct": (packet.get("deliveryDiscount") or {}).get("value"),
                "basket_discount": packet.get("basketDiscount"),
                "free_items": [p.get("title") for p in packet.get("extraProducts") or []],
                "min_basket": int(c["minimum_basket_price"]) if c.get("minimum_basket_price") else None,
                "only_order_number": c.get("activation_order_number") or None,
                "condition": c.get("condition_message"),
            })
    if failed:
        out["errors"] = [f"{name}: {r}" for name, r in failed.items()]
    return out


@tool("Restaurant reviews")
async def food_reviews(
    vendor_code: VendorCode,
    page: Annotated[int, Field(ge=0, le=100, description="Zero-based page of 10 reviews, newest first.")] = 0,
) -> dict[str, Any]:
    """Read recent customer reviews of a restaurant (rating, text, what they ordered, vendor reply).

    Use to judge quality, delays or packaging before recommending a restaurant.
    """
    data = (await fetch(f"{FOOD_BASE}/mobile/v1/restaurant/vendor-comment", {"vendorCode": vendor_code, "page": page}, common=FOOD_PARAMS))["data"]
    return {
        "total": data["count"] if (data.get("count") or 0) > 0 else None,
        "page": page,
        "reviews": [
            {
                "date": c.get("createdDate"),
                "rating": _stars(c.get("rate")),
                "text": c.get("commentText") or None,
                "ordered": [f["title"] for f in c.get("foods") or []],
                "reply": (c.get("replies") or [{}])[0].get("commentText"),
            }
            for c in data["comments"]
        ],
    }


@tool("FoodParty deals")
async def food_party_deals(
    lat: Lat,
    long: Long,
    query: Annotated[str | None, Field(description="Only deals whose dish title contains this text.")] = None,
    sort: Annotated[Literal["cheapest", "biggest_discount"], Field(description="Order of the deals.")] = "biggest_discount",
    limit: Annotated[int, Field(ge=1, le=100, description="Max deals to return.")] = 30,
) -> dict[str, Any]:
    """List today's FoodParty flash deals (deep discounts, limited stock, time window) still in stock.

    Use for "what's on a big discount right now". Deals run only inside the window
    (starts/ends); outside it the list is empty.
    """
    body = await _party({"lat": lat, "long": long})
    if body is None:
        return {"active": False, "deals": []}
    data = body["data"]
    deals = [
        {
            "id": p["id"],
            "title": p["productVariationTitle"],
            "price": p["price"],
            "discount_pct": p.get("discountRatio") or 0,
            "final_price": p["priceAfterDiscount"],
            "packaging": p.get("containerPrice") or 0,
            "remaining": p["remaining"],
            "max_per_order": p.get("capacityPerOrder"),
            "vendor_code": p["vendorCode"],
            "vendor": p.get("vendorTitle"),
            "rating": _stars(p.get("rating")),
            "delivery_fee": _delivery_fee(p),
            "min_order": p.get("minOrder"),
        }
        for p in data["products"]
        if p.get("remaining") and (not query or _has(query, p["productVariationTitle"]))
    ]
    deals.sort(key=(lambda d: d["final_price"]) if sort == "cheapest" else (lambda d: -d["discount_pct"]))
    return {
        "active": True,
        "starts": data.get("firstActivePeriodStartRFC"),
        "ends": data.get("firstActivePeriodEndRFC"),
        "now": data.get("currentTimeRFC"),
        "in_stock": len(deals),
        "deals": deals[:limit],
    }


@tool("Meals for one")
async def food_meal_for_one(
    lat: Lat,
    long: Long,
    max_price: Annotated[int | None, Field(ge=1, description="Only meals at or under this final price, Toman.")] = None,
    limit: Annotated[int, Field(ge=1, le=100, description="Max meals to return.")] = 30,
) -> dict[str, Any]:
    """List single-person meals (at most 299k Toman, free delivery), cheapest first.

    Use for "cheap meal for one". Check min_order: a cheap meal may not reach the
    restaurant's minimum basket on its own.
    """
    data = (await fetch(f"{FOOD_BASE}/search/api/v1/meal-for-one/product-list", {"lat": lat, "long": long, "page": 0, "page_size": 100}, common=FOOD_PARAMS))["data"]
    meals = []
    for row in data["finalResult"]:
        m = row["data"]
        if m.get("no_stock") or (max_price is not None and m["priceAfterDiscount"] > max_price):
            continue
        v = m.get("vendor") or {}
        meals.append({
            "id": m["id"],
            "title": m["title"],
            "price": m["price"],
            "discount_pct": m.get("discountRatio") or 0,
            "final_price": m["priceAfterDiscount"],
            "rating": _stars(m.get("normalized_rating")),
            "vendor_code": v.get("vendorCode"),
            "vendor": v.get("title"),
            "open": v.get("is_open"),
            "delivery_fee": _delivery_fee(v),
            "min_order": v.get("minOrder"),
            "eta_min": v.get("eta"),
        })
    meals.sort(key=lambda m: m["final_price"])
    return {"rule": data.get("description"), "meals": meals[:limit]}


@tool("Discounted restaurants")
async def food_discounted_vendors(lat: Lat, long: Long) -> dict[str, Any]:
    """List restaurants running a discount now, plus the short "Gem" hunt discount if one is live.

    Use for "which restaurants have offers". Vendor-level only; open food_menu for prices.
    """
    loc = {"lat": lat, "long": long, "page": 0, "page_size": 100}
    off, session = await asyncio.gather(
        fetch(f"{FOOD_BASE}/search/api/v1/user/off-box", loc, common=FOOD_PARAMS),
        fetch(f"{FOOD_BASE}/search/api/v1/user/gem-session", {"lat": lat, "long": long}, common=FOOD_PARAMS),
        return_exceptions=True,
    )
    if isinstance(off, BaseException):
        raise off
    out: dict[str, Any] = {
        "discounted": [
            {
                "code": v["code"],
                "title": v["title"],
                "promotion": v.get("promotion_text"),
                "open": v.get("is_open"),
                "delivery_fee": _delivery_fee(v),
                "eta_min": v.get("eta"),
            }
            for v in off["data"]["result"]
        ],
        "gem": None,
    }
    # Gem is an extra; if its calls fail, still return the discounted list.
    try:
        if isinstance(session, BaseException):
            raise session
        if not (session.get("data") or {}).get("can_show"):
            return out
        gem = (await fetch(f"{FOOD_BASE}/search/api/v1/user/gem-vendor-list", loc, common=FOOD_PARAMS))["data"]
    except ApiError as e:
        out["errors"] = [f"gem: {e}"]
        return out
    out["gem"] = {
        "ends": gem.get("expire_date"),
        "vendors": [
            {
                "code": v["vendorCode"],
                "title": v["title"],
                "discount": next((b["text"] for b in v.get("badge_list") or [] if b.get("type") == "gem"), None),
                "open": v.get("isOpen"),
                "rating": _stars(v.get("rating")),
                "delivery_fee": _delivery_fee(v),
                "min_order": v.get("minOrder"),
            }
            for v in gem["result"]
        ],
    }
    return out


async def _vendor_list(loc: dict[str, Any], filters: dict[str, Any] | None = None, page: int = 0, size: int = 500) -> list[dict[str, Any]]:
    """Restaurants delivering around `loc`; size 500 returns them all in one call."""
    body = await fetch(
        f"{FOOD_BASE}/search/api/v1/desktop/vendors-list",
        {**loc, "page": page, "page_size": size, "sp_alias": "restaurant", "superType": "[1]", "filters": json.dumps(filters or {}), "query": ""},
        common=FOOD_PARAMS,
    )
    return [x["data"] for x in body["data"]["finalResult"] if x.get("type") == "VENDOR"]


async def _menu(vendor_code: str, loc: dict[str, Any]) -> Any:
    return await fetch(f"{FOOD_BASE}/mobile/v2/restaurant/details/dynamic", {**loc, "vendorCode": vendor_code, "locale": "fa"}, common=FOOD_PARAMS)


def _menu_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Unique menu products. Virtual categories (id < 0, e.g. popular) repeat real ones, so read them last."""
    items, seen = [], set()
    for menu in sorted(data["menus"], key=lambda m: (m.get("categoryId") or 0) < 0):
        for p in menu["products"]:
            if p["id"] in seen:
                continue
            seen.add(p["id"])
            items.append({
                **p,
                "categoryTitle": p.get("categoryTitle") or menu.get("category"),
                "discount": p.get("discount") or 0,
                "discountRatio": p.get("discountRatio") or 0,
                "containerPrice": p.get("containerPrice") or 0,
                "available": not p.get("disabledUntil") and p.get("stock") != 0,
            })
    return items


async def _live_menus(codes: list[str], loc: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Live menu data by vendor code, plus how many menu calls failed (those vendors are skipped)."""
    results = await asyncio.gather(*(_menu(c, loc) for c in codes), return_exceptions=True)
    menus, failed = {}, 0
    for code, r in zip(codes, results, strict=True):
        if isinstance(r, ApiError):
            failed += 1
        elif isinstance(r, BaseException):
            raise r
        else:
            menus[code] = r["data"]
    return menus, failed


def _menu_offers(vendor: dict[str, Any], data: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "id": p["id"],
            "title": p["title"],
            "food_price": p["price"] - p["discount"],
            "packaging": p["containerPrice"],
            "delivery_fee": _delivery_fee(vendor) or 0,
            "discount_pct": p["discountRatio"],
            "vendor_code": vendor["code"],
            "vendor": vendor["title"],
            "rating": _stars(vendor.get("rating")),
            "min_order": data["vendor"].get("minOrder"),
            "source": "menu",
            "category": p["categoryTitle"],
        }
        for p in _menu_items(data)
        if p["available"]
    ]


def _party_offers(body: Any) -> list[dict[str, Any]]:
    return [
        {
            "id": p["id"],
            "title": p["productVariationTitle"],
            "food_price": p["priceAfterDiscount"],
            "packaging": p.get("containerPrice") or 0,
            "delivery_fee": _delivery_fee(p) or 0,
            "discount_pct": p.get("discountRatio") or 0,
            "vendor_code": p["vendorCode"],
            "vendor": p.get("vendorTitle"),
            "rating": _stars(p.get("rating")),
            "min_order": p.get("minOrder"),
            "source": "food_party",
        }
        for p in (body["data"]["products"] if body else [])
        if p.get("remaining")
    ]


async def _party(loc: dict[str, Any]) -> Any:
    """FoodParty list, or None outside the deal window (the API answers success:false)."""
    body = await fetch(
        f"{FOOD_BASE}/search/api/v4/food-party",
        {**loc, "deal_project_list_id": FOOD_PARTY_LIST, "page": 0, "page_size": 500},
        common=FOOD_PARAMS,
    )
    return body if body.get("success") is not False and body.get("data") else None


async def _live_prices(dishes: list[dict[str, Any]], loc: dict[str, Any], query: str) -> list[dict[str, Any]]:
    """Replace search-index prices with live menu prices; drop dishes gone, unavailable or filed as sides."""
    menus, _ = await _live_menus(list({d["vendor_code"] for d in dishes}), loc)
    live = {p["id"]: p for data in menus.values() for p in _menu_items(data)}
    out = []
    for d in dishes:
        p = live.get(d["id"])
        if p and p["available"] and not _is_side(query, p["title"], p["categoryTitle"]):
            out.append({**d, "price": p["price"], "discount_pct": p["discountRatio"], "final_price": p["price"] - p["discount"], "packaging": p["containerPrice"]})
    return out


# Words marking a side item rather than a dish, in a menu category or at the start of a title.
# ponytail: keyword heuristic, misses unusual category names; a per-vendor category model if it matters.
SIDE_CATEGORIES = ("سس", "نوشیدنی", "دسر", "پیش غذا", "مخلفات", "افزودنی", "اضافه", "دورچین")
SIDE_TITLES = ("سس", "خمیر", "نوشابه", "دوغ")

# Arabic yeh/kaf and the zero-width non-joiner vary between typed queries and menu titles.
_NORMALIZE = str.maketrans({"ي": "ی", "ك": "ک", "\u200c": " "})


def _norm(text: str) -> str:
    return " ".join(text.translate(_NORMALIZE).split())


def _has(query: str, text: str) -> bool:
    """Substring match that ignores Arabic/Persian letter variants and ZWNJ vs space."""
    return _norm(query) in _norm(text)


def _is_side(query: str, title: str, category: str = "") -> bool:
    """True for sauces, drinks, extras etc. that only mention the dish, unless the user asked for them."""
    query, title, category = _norm(query), _norm(title), _norm(category or "")
    return any(w in category and w not in query for w in SIDE_CATEGORIES) or any(
        title.startswith(w) and w not in query for w in SIDE_TITLES
    )


def _delivery_fee(v: dict[str, Any]) -> int | None:
    """Delivery fee in Toman, after the vendor's delivery discount when one applies. FoodParty sends strings."""
    fee = v.get("deliveryFeeAfterDiscount") if v.get("isDeliveryFeeHasDiscount") else v.get("deliveryFee", v.get("delivery_fee"))
    return None if fee is None or int(fee) < 0 else int(fee)


def _stars(rating10: float | None) -> float | None:
    """0-10 API rating to the 0-5 stars the site shows; None when unrated."""
    return round(rating10 / 2, 1) if rating10 else None
