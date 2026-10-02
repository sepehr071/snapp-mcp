<!-- mcp-name: io.github.sepehr071/snapp-mcp -->

<div align="center">

# 🍕 snapp-mcp

**Let your AI agent shop around on Snappfood and SnappMarket.**<br>
Search dishes and groceries, compare real prices across hundreds of restaurants and stores,<br>
read menus and reviews, and catch today's flash deals, all from Claude, Cursor or Copilot.

[![PyPI](https://img.shields.io/pypi/v/snapp-mcp?color=2563eb)](https://pypi.org/project/snapp-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/snapp-mcp)](https://pypi.org/project/snapp-mcp/)
[![CI](https://github.com/sepehr071/snapp-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/sepehr071/snapp-mcp/actions/workflows/ci.yml)
[![MCP Registry](https://img.shields.io/badge/MCP_Registry-io.github.sepehr071%2Fsnapp--mcp-7c3aed)](https://registry.modelcontextprotocol.io/v0/servers?search=snapp-mcp)
[![License: MIT](https://img.shields.io/badge/license-MIT-16a34a)](https://github.com/sepehr071/snapp-mcp/blob/main/LICENSE)

[![Install in Cursor](https://cursor.com/deeplink/mcp-install-dark.svg)](https://cursor.com/en/install-mcp?name=snapp&config=eyJjb21tYW5kIjoidXZ4IiwiYXJncyI6WyJzbmFwcC1tY3AiXX0=)
[![Install in VS Code](https://img.shields.io/badge/VS_Code-Install_snapp--mcp-0098FF?style=flat-square&logo=visualstudiocode&logoColor=white)](https://vscode.dev/redirect/mcp/install?name=snapp&config=%7B%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22snapp-mcp%22%5D%7D)

[Quick start](#quick-start) · [What it can do](#what-it-can-do) · [Tools](#tools) · [FAQ](#faq) · [فارسی](#فارسی)

</div>

---

## Why

Snappfood shows you one restaurant at a time. Finding *the cheapest pizza that actually reaches your door*
means opening dozens of menus, adding packaging and delivery fees in your head, and checking each
restaurant's minimum order. An agent with `snapp-mcp` does that in seconds:

> **You:** Cheapest pizza delivered to Vanak Square right now, including delivery?
>
> **Agent:** *calls* `find_location(query="میدان ونک")` → `food_find_cheapest(query="پیتزا", lat=35.7577, long=51.4095)`
>
> | Total | Dish | Restaurant | Breakdown |
> |---:|---|---|---|
> | **296,400** | پیتزا سوسیس مینی + نوشابه | پیتزاتو (گاندی) | food 296,400 · free delivery |
> | **410,000** | پیتزا اسمارت وجی لاور | پیتزا دومینوز (جردن) | food 360,000 + delivery 50,000 |
> | **464,000** | پیتزا پپرونی اسلایسی | تله پیتزا (سعادت آباد) | food 315,000 + delivery 149,000 |
>
> The Pizzato combo is cheapest overall. Want me to check its minimum order and coupons with `food_order_costs`?

<sub>Real tool output from 2026-10-02; prices change all the time. Prices are in Toman.</sub>

## What it can do

- 🔎 **Search** dishes and grocery products by name near any address in Iran
- 💸 **Find the true cheapest option**: food price after discount + packaging + delivery, checked against live menus
- 🏪 **Browse** restaurants and stores with filters: free delivery, discount, coupon, rating, distance
- 📋 **Read** full menus, store catalogs, minimum orders, delivery fees, ETAs and coupons
- ⚡ **Catch deals**: FoodParty flash deals, meals for one, Gem hunts, Market Party
- ⭐ **Check quality** with customer reviews before recommending anything
- 🔒 **Read-only by design**: no login, no basket, no orders, no payment

## Quick start

You need [uv](https://docs.astral.sh/uv/getting-started/installation/) and **an Iranian IP address**
(Snappfood blocks most foreign IPs; see [FAQ](#faq)).

<details open>
<summary><b>Claude Code</b></summary>

```bash
claude mcp add snapp -- uvx snapp-mcp
```
</details>

<details>
<summary><b>Claude Desktop</b></summary>

Settings → Developer → Edit Config, then add:

```json
{
  "mcpServers": {
    "snapp": { "command": "uvx", "args": ["snapp-mcp"] }
  }
}
```
</details>

<details>
<summary><b>Cursor</b></summary>

Click **Install in Cursor** above, or add the Claude Desktop block to `~/.cursor/mcp.json`.
</details>

<details>
<summary><b>VS Code (Copilot agent mode)</b></summary>

Click **Install in VS Code** above, or add to `.vscode/mcp.json`:

```json
{
  "servers": {
    "snapp": { "type": "stdio", "command": "uvx", "args": ["snapp-mcp"] }
  }
}
```
</details>

<details>
<summary><b>Anything else</b></summary>

It's a standard stdio MCP server: run `uvx snapp-mcp`, or `pip install snapp-mcp` and run `snapp-mcp`.
</details>

Then just ask:

- "Which restaurants near Tajrish have free delivery and at least 4.5 stars?"
- "Show FoodParty deals with more than 40% off near me."
- "Where is low-fat milk cheapest near Jordan, Tehran? Include delivery."
- <span dir="rtl">ارزان&zwnj;ترین کباب نزدیک میدان آزادی شیراز با هزینه ارسال؟</span>

## How it works

```mermaid
flowchart LR
    A[AI agent: Claude, Cursor, Copilot] -->|MCP over stdio| S[snapp-mcp on your machine]
    S -->|HTTPS from your IP| F[snappfood.ir restaurants]
    S -->|HTTPS from your IP| M[snapp.market groceries]
```

`snapp-mcp` runs locally and calls the same public endpoints the Snappfood and SnappMarket web apps use.
There's no hosted server in between, no API key, and nothing about you is sent anywhere else.

## Tools

Every food and grocery tool takes `lat` / `long`, because menus, prices and delivery fees depend on the
delivery point. The agent gets them from `find_location` or `list_cities` first.

<details open>
<summary><b>📍 Location</b> (3)</summary>

| Tool | What it does |
|---|---|
| `find_location` | Address, landmark or street → coordinates |
| `reverse_geocode` | Coordinates → street / neighbourhood names |
| `list_cities` | Every served city with its center point |
</details>

<details open>
<summary><b>🍔 Snappfood: restaurants</b> (9)</summary>

| Tool | What it does |
|---|---|
| `food_search` | Search dishes by name; price sorts are re-checked against live menus |
| `food_find_cheapest` | Scan nearby menus + FoodParty for the lowest total price (food + packaging + delivery) |
| `food_restaurants` | Restaurants with filters (free delivery, discount, coupon) and sorting |
| `food_menu` | One restaurant's full menu with prices, packaging fees and availability |
| `food_order_costs` | Delivery fee, ETA, minimum order and coupons of one restaurant |
| `food_reviews` | Customer reviews, with what they ordered and the restaurant's reply |
| `food_party_deals` | FoodParty flash deals still in stock, with the deal window |
| `food_meal_for_one` | Single-person meals up to 299k Toman with free delivery |
| `food_discounted_vendors` | Restaurants running discounts now, plus live Gem deals |
</details>

<details open>
<summary><b>🛒 SnappMarket: groceries</b> (9)</summary>

| Tool | What it does |
|---|---|
| `market_search` | Search a product across stores, grouped by store |
| `market_find_cheapest` | Cheapest in-stock offers for a product, one flat list |
| `market_stores` | Stores delivering to a point, by delivery fee or rating |
| `market_store_info` | Delivery fee, minimum order, opening hours and coupons of a store |
| `market_store_products` | Search inside a store, or browse it by category |
| `market_categories` | Product categories and their ids |
| `market_product` | One product's details and price in a store |
| `market_party_deals` | Market Party flash deals, biggest discount first |
| `market_reviews` | Customer comments on a store |
</details>

All tools are annotated `readOnlyHint: true` and return compact structured JSON, so they don't flood the agent's context.

## Good to know

- **Prices are in Toman** (1 Toman = 10 Rial). Ratings are normalized to **0–5**, like the apps; `null` means not rated yet.
- **True cost of a food order** = price − discount + packaging + delivery − coupon, and the basket must reach the restaurant's minimum order. `food_find_cheapest` and `food_order_costs` give the agent every piece of that.
- **Persian queries match best** (`پیتزا`, `کباب`, `شیر`). Name filters treat Arabic ي/ك and half-space vs space as equal.

## FAQ

<details>
<summary><b>I get "blocked the request (HTTP 403)"</b></summary>

Snappfood's firewall only accepts Iranian IP addresses. Run the server on a machine in Iran with the VPN off.
If you must use a VPN, set `SNAPP_MCP_PROXY` to an HTTP proxy that exits in Iran. Normal system proxy variables
are ignored on purpose, because a foreign VPN exit would get blocked. SnappMarket is less strict.
</details>

<details>
<summary><b>Can it place an order for me?</b></summary>

No, and that's deliberate. It has no login and never touches the basket, order or payment endpoints.
The agent finds the best option; you tap order in the app.
</details>

<details>
<summary><b>FoodParty or Gem results are empty</b></summary>

Those deals only run in time windows. `active: false` or `gem: null` means no window is live right now.
</details>

<details>
<summary><b>Claude Desktop says <code>uvx</code> is not found</b></summary>

Use the full path to `uvx` (`where uvx` on Windows, `which uvx` on macOS/Linux) as `command`.
</details>

<details>
<summary><b>How do I debug what the agent sees?</b></summary>

```bash
npx @modelcontextprotocol/inspector uvx snapp-mcp
```
</details>

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `SNAPP_MCP_PROXY` | unset | HTTP proxy for every request, e.g. `http://user:pass@host:port` |

## فارسی

<div dir="rtl">

**snapp-mcp** به دستیار هوش مصنوعی شما (Claude، Cursor، Copilot و ...) اجازه می&zwnj;دهد در اسنپ&zwnj;فود و اسنپ&zwnj;مارکت جستجو کند،
قیمت واقعی غذا و کالا را بین صدها رستوران و فروشگاه مقایسه کند، منو و نظرات را بخواند و تخفیف&zwnj;های فعال (فودپارتی، جم، مارکت&zwnj;پارتی) را پیدا کند.

- فقط خواندنی است: وارد حساب نمی&zwnj;شود، سبد خرید نمی&zwnj;سازد و سفارش ثبت نمی&zwnj;کند.
- قیمت نهایی را با بسته&zwnj;بندی، هزینه ارسال و حداقل سفارش حساب می&zwnj;کند.
- روی سیستم خود شما اجرا می&zwnj;شود و به هیچ سرور واسطی داده نمی&zwnj;فرستد.

**نصب در Claude Code:**

</div>

```bash
claude mcp add snapp -- uvx snapp-mcp
```

<div dir="rtl">

بعد بپرسید: «ارزان&zwnj;ترین پیتزا با هزینه ارسال نزدیک میدان ونک کجاست؟»

**نکته:** سرور باید روی سیستمی با IP ایران و بدون VPN اجرا شود، چون اسنپ&zwnj;فود درخواست&zwnj;های خارج از ایران را مسدود می&zwnj;کند.

</div>

## Development

```bash
git clone https://github.com/sepehr071/snapp-mcp && cd snapp-mcp
uv sync
uv run pytest            # offline, against recorded responses
uv run pytest -m live    # real APIs (needs an Iranian IP)
uv run ruff check .
```

Tools live in `src/snapp_mcp/food.py`, `market.py` and `location.py`; each is a typed async function with a docstring
that tells the agent when to use it. Issues and PRs are welcome, especially new tools and fixes for API changes.

Releases: bump the version in `pyproject.toml` and `server.json`, then push a `v*` tag. GitHub Actions tests,
publishes to PyPI and the [MCP Registry](https://registry.modelcontextprotocol.io), and creates the GitHub Release.

## Disclaimer

Unofficial and not affiliated with or endorsed by Snapp. It uses the public endpoints of the snappfood.ir and
snapp.market web apps, which can change without notice. Please keep request rates reasonable.

## License

[MIT](https://github.com/sepehr071/snapp-mcp/blob/main/LICENSE)
