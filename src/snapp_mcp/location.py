"""Location tools shared by Snappfood and SnappMarket: place name to coordinates and back."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field

from .http import FOOD_BASE, FOOD_PARAMS, ApiError, fetch
from .registry import tool

Lat = Annotated[float, Field(ge=24, le=40, description="Latitude of the delivery point (Iran: ~25 to ~40).")]
Long = Annotated[float, Field(ge=44, le=64, description="Longitude of the delivery point (Iran: ~44 to ~63).")]

# Tehran center, used to bias place search when no point is given.
TEHRAN = (35.6892, 51.3890)


@tool("Find location")
async def find_location(
    query: Annotated[str, Field(min_length=2, description="Place, street, landmark or neighbourhood, Persian works best, e.g. 'میدان ونک'.")],
    near_lat: Annotated[float | None, Field(description="Bias results toward this latitude (e.g. a city center from list_cities).")] = None,
    near_long: Annotated[float | None, Field(description="Bias results toward this longitude.")] = None,
    limit: Annotated[int, Field(ge=1, le=20, description="Max places to return.")] = 5,
) -> dict[str, Any]:
    """Turn a place name or address into latitude/longitude.

    Use first when the user gives an address instead of coordinates; every food_* and
    market_* tool needs lat/long. Results are biased toward near_lat/near_long
    (default Tehran), so pass a city center for other cities.
    """
    lat, lon = (near_lat, near_long) if near_lat is not None and near_long is not None else TEHRAN
    # The map endpoints reject the common client params and use `lon`, not `long`.
    body = await fetch(f"{FOOD_BASE}/map/address/place", {"place": query, "lat": lat, "lon": lon})
    places = _unwrap_map(body)
    return {
        "places": [
            {
                "name": p.get("name"),
                "description": p.get("description"),
                "lat": float(p["location"]["latitude"]),
                "long": float(p["location"]["longitude"]),
                "type": p.get("type"),
                "distance_m": p.get("distance"),
            }
            for p in places[:limit]
            if p.get("location")
        ]
    }


@tool("Describe coordinates")
async def reverse_geocode(lat: Lat, long: Long) -> dict[str, Any]:
    """Describe coordinates in words (point of interest, street, neighbourhood).

    Use to confirm a delivery point with the user before searching.
    """
    body = await fetch(f"{FOOD_BASE}/map/address/reverse", {"lat": lat, "lon": long})
    return {"labels": [{"name": x.get("name"), "type": x.get("type")} for x in _unwrap_map(body)]}


@tool("List cities")
async def list_cities(
    query: Annotated[str | None, Field(description="Optional filter on the Persian title or English code, e.g. 'شیراز' or 'Shiraz'.")] = None,
) -> dict[str, Any]:
    """List cities Snappfood serves, with id, names and center coordinates.

    Use the center coordinates as a rough delivery point when the user only names a city.
    """
    body = await fetch(f"{FOOD_BASE}/mobile/v2/area/cities", common=FOOD_PARAMS)
    cities = body["data"]["cities"]
    if query:
        q = query.strip().lower()
        cities = [c for c in cities if q in c["title"] or q in c["code"].lower()]
    return {
        "cities": [
            {"id": c["id"], "code": c["code"], "title": c["title"], "lat": c["latitude"], "long": c["longitude"]}
            for c in sorted(cities, key=lambda c: c.get("rank") or 0)
        ]
    }


def _unwrap_map(body: Any) -> list[dict[str, Any]]:
    """Map endpoints wrap the payload in a one-item array and report errors as status:false."""
    item = body[0] if isinstance(body, list) and body else body
    if not isinstance(item, dict) or item.get("status") is False:
        raise ApiError(f"Map service rejected the request: {item}")
    return item.get("data") or []
