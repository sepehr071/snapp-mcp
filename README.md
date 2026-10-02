# snapp-mcp

<!-- mcp-name: io.github.sepehr071/snapp-mcp -->

[![PyPI](https://img.shields.io/pypi/v/snapp-mcp)](https://pypi.org/project/snapp-mcp/)
[![CI](https://github.com/sepehr071/snapp-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/sepehr071/snapp-mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

An MCP server that lets AI agents use **Snappfood** (restaurant delivery) and **SnappMarket**
(grocery delivery) in Iran. Agents can search dishes and groceries, compare prices across
restaurants and stores, read menus and reviews, and find live deals.

It is **read-only**: it cannot log in, add to a basket or place an order.

> Unofficial. Not affiliated with or endorsed by Snapp. It uses the same public endpoints as
> the snappfood.ir and snapp.market web apps, which can change without notice.

## Example prompts

- "Cheapest pizza delivered to Vanak Square right now, including delivery fee."
- "Which restaurants near me in Shiraz have free delivery and a rating above 4?"
- "Show today's FoodParty deals with more than 40% off."
- "Where can I buy low-fat milk cheapest near Jordan, Tehran?"
- "Is restaurant 947evd's minimum order above 200,000 Toman? Any coupons?"

## Requirements

- [uv](https://docs.astral.sh/uv/getting-started/installation/) (provides `uvx`), or Python 3.10+ with pip.
- **An Iranian IP address.** Snappfood's firewall rejects most foreign IPs (HTTP 403). Run the server on
  a machine in Iran with VPN off, or point `SNAPP_MCP_PROXY` at a proxy in Iran. SnappMarket is less strict.

## Install

### Claude Code

```bash
claude mcp add snapp -- uvx snapp-mcp
```

### Claude Desktop

Add to `claude_desktop_config.json` (Settings → Developer → Edit Config):

```json
{
  "mcpServers": {
    "snapp": {
      "command": "uvx",
      "args": ["snapp-mcp"]
    }
  }
}
```

### Cursor

Add the same block to `~/.cursor/mcp.json` (global) or `.cursor/mcp.json` (project).

### VS Code (Copilot agent mode)

Add to `.vscode/mcp.json`:

```json
{
  "servers": {
    "snapp": {
      "type": "stdio",
      "command": "uvx",
      "args": ["snapp-mcp"]
    }
  }
}
```

### Any other MCP client

Run `uvx snapp-mcp` (or `pip install snapp-mcp` then `snapp-mcp`) as a stdio server.

## Tools

Every food and grocery tool takes `lat` / `long`: menus, prices and delivery fees depend on the delivery point.
Get them with `find_location` or `list_cities` first.

### Location

| Tool | What it does |
|---|---|
| `find_location` | Place name or address to coordinates |
| `reverse_geocode` | Coordinates to street / neighbourhood names |
| `list_cities` | Served cities with center coordinates |

### Snappfood (restaurants)

| Tool | What it does |
|---|---|
| `food_search` | Search dishes by name with price, discount and restaurant (one fast call) |
| `food_find_cheapest` | Scan nearby menus and FoodParty for the lowest total price (food + packaging + delivery) |
| `food_restaurants` | List restaurants with filters (free delivery, discount, coupon) and sorting |
| `food_menu` | One restaurant's full menu with prices, packaging fees and availability |
| `food_order_costs` | Delivery fee, ETA, minimum order and coupons of one restaurant |
| `food_reviews` | Customer reviews of a restaurant |
| `food_party_deals` | FoodParty flash deals still in stock, with the deal window |
| `food_meal_for_one` | Single-person meals (≤ 299k Toman, free delivery) |
| `food_discounted_vendors` | Restaurants running discounts now, plus live "Gem" deals |

### SnappMarket (groceries)

| Tool | What it does |
|---|---|
| `market_search` | Search a product across stores, grouped by store |
| `market_find_cheapest` | Cheapest in-stock offers for a product, one flat list |
| `market_stores` | Stores delivering to a point, by delivery fee or rating |
| `market_store_info` | Delivery fee, minimum order, opening hours and coupons of a store |
| `market_store_products` | Search a store, or browse it by category |
| `market_categories` | Product categories and their ids |
| `market_product` | One product's details and price in a store |
| `market_party_deals` | Market party flash deals, biggest discount first |
| `market_reviews` | Customer comments of a store |

All tools are annotated `readOnlyHint: true`. Results are compact JSON (structured content), so they do not use up the agent's context.

## Conventions

- Prices are in **Toman** (1 Toman = 10 Rial).
- Ratings are normalized to **0-5**, like the apps show. `null` means not rated yet.
- Persian queries match best: `پیتزا`, `کباب`, `شیر`.
- True cost of a food order: `price - discount + packaging + delivery fee - coupon`. The basket must also reach the restaurant's minimum order.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `SNAPP_MCP_PROXY` | unset | HTTP proxy URL for all requests, e.g. `http://user:pass@host:port`. System proxy variables are ignored on purpose, because a foreign VPN exit gets blocked. |

## Troubleshooting

- **"blocked the request (HTTP 403)"**: your IP is outside Iran. Turn off the VPN, or set `SNAPP_MCP_PROXY` to a proxy in Iran.
- **`uvx` not found in Claude Desktop**: use the full path to `uvx` as `command`.
- **Empty FoodParty / Gem results**: these deals only run in time windows. `active: false` means no window is live.
- Debug with the MCP Inspector: `npx @modelcontextprotocol/inspector uvx snapp-mcp`.

## Development

```bash
git clone https://github.com/sepehr071/snapp-mcp && cd snapp-mcp
uv sync
uv run pytest            # offline tests against recorded responses
uv run pytest -m live    # hits the real APIs (needs an Iranian IP)
uv run ruff check .
```

Releases: bump the version in `pyproject.toml` and `server.json`, then push a `v*` tag.
GitHub Actions publishes to PyPI (trusted publishing) and to the [MCP Registry](https://registry.modelcontextprotocol.io).

## License

MIT
