"""MCP server entry point: registers every read-only Snappfood and SnappMarket tool."""

import logging

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from . import __version__, food, location, market  # noqa: F401  (imports register the tools)
from .registry import TOOLS

INSTRUCTIONS = """\
Unofficial, read-only access to Snappfood (restaurant delivery, snappfood.ir) and SnappMarket
(grocery delivery, snapp.market) in Iran. Nothing here can log in, add to a basket or order.

Workflow:
1. Get coordinates: find_location (address/landmark) or list_cities (city center). All food_* and
   market_* tools need lat/long because menus, prices and delivery depend on the delivery point.
2. Food: food_search / food_find_cheapest for dishes, food_restaurants for restaurants,
   food_menu for one restaurant, food_order_costs before recommending (delivery fee, minimum
   order, coupons), deals via food_party_deals / food_meal_for_one / food_discounted_vendors.
3. Groceries: market_search / market_find_cheapest, market_stores, market_store_info,
   market_store_products, market_party_deals.

Conventions: prices are Toman (1 Toman = 10 Rial). Ratings are 0-5. Vendor codes look like
'947evd'. Persian queries match best ('پیتزا', 'شیر'). True cost of a food order =
price - discount + packaging + delivery fee - coupon, and the basket must reach the minimum order.
"""

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True)

mcp = MCPServer(
    "snapp-mcp",
    title="Snappfood & SnappMarket",
    instructions=INSTRUCTIONS,
    version=__version__,
    website_url="https://github.com/sepehr071/snapp-mcp",
)

for fn, title in TOOLS:
    mcp.add_tool(fn, title=title, annotations=READ_ONLY)


def main() -> None:
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one INFO line per request floods client logs
    mcp.run()


if __name__ == "__main__":
    main()
