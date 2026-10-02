"""Shared async HTTP client for the Snappfood and SnappMarket APIs.

Every tool goes through `fetch`, which adds the PWA client params, caps concurrency
and turns HTTP failures into `ToolError` messages the model can act on.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx
from mcp.server.mcpserver.exceptions import ToolError

FOOD_BASE = "https://snappfood.ir"
FOOD_APIGW = "https://apigw.snappfood.ir"
MARKET_BASE = "https://svc.snapp.market"

FOOD_PARAMS = {"optionalClient": "PWA", "client": "PWA", "deviceType": "PWA", "appVersion": "6.0.0"}
MARKET_PARAMS = {"client": "PWA", "deviceType": "PWA", "appVersion": "1.403.1"}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0",
    "Accept": "application/json",
}

MAX_CONCURRENCY = 4

_transport: httpx.AsyncBaseTransport | None = None
_client: httpx.AsyncClient | None = None
_limit: asyncio.Semaphore | None = None


class ApiError(ToolError):
    """A failed upstream call. `status` is the HTTP status, or None for network errors."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """Swap the transport (tests use httpx.MockTransport). Drops the current client."""
    global _transport, _client, _limit
    _transport, _client, _limit = transport, None, None


def _get_client() -> tuple[httpx.AsyncClient, asyncio.Semaphore]:
    global _client, _limit
    if _client is None:
        # Snappfood's firewall blocks most non-Iranian exits, so ignore system proxy
        # settings unless the user opts in with SNAPP_MCP_PROXY.
        _client = httpx.AsyncClient(
            transport=_transport,
            headers=HEADERS,
            timeout=20,
            follow_redirects=True,
            trust_env=False,
            proxy=os.environ.get("SNAPP_MCP_PROXY") or None,
        )
        _limit = asyncio.Semaphore(MAX_CONCURRENCY)
    assert _limit is not None
    return _client, _limit


async def fetch(
    url: str,
    params: dict[str, Any] | None = None,
    *,
    common: dict[str, str] | None = None,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    json: Any = None,
) -> Any:
    """Call an endpoint and return the parsed JSON body.

    `common` is merged under `params` (pass FOOD_PARAMS or MARKET_PARAMS, or None
    for endpoints that reject extra query params).
    """
    client, limit = _get_client()
    query = {**(common or {}), **(params or {})}
    host = httpx.URL(url).host
    try:
        async with limit:
            r = await client.request(method, url, params=query, headers=headers, json=json)
    except httpx.TimeoutException as e:
        raise ApiError(f"{host} did not answer in time. Try again in a moment.") from e
    except httpx.RequestError as e:
        raise ApiError(
            f"Could not reach {host} ({type(e).__name__}). Check the internet connection; "
            "Snappfood only serves Iranian IP addresses."
        ) from e

    if r.status_code >= 400:
        raise ApiError(_status_message(r, host), r.status_code)
    try:
        body = r.json()
    except ValueError as e:
        raise ApiError(f"{host} returned a non-JSON response (HTTP {r.status_code}).", r.status_code) from e
    if isinstance(body, dict) and body.get("status") is False:
        raise ApiError(f"{host} rejected the request: {_error_text(body)}", r.status_code)
    return body


def _status_message(r: httpx.Response, host: str) -> str:
    code = r.status_code
    if code == 403:
        return (
            f"{host} blocked the request (HTTP 403). Snappfood's firewall only allows Iranian IP addresses: "
            "turn off VPN/proxy, or set SNAPP_MCP_PROXY to an Iranian proxy."
        )
    if code == 404:
        return f"Not found on {host} (HTTP 404). Check the vendor code / product id."
    if code == 429:
        return f"{host} is rate limiting requests (HTTP 429). Wait a minute before retrying."
    if code >= 500:
        return f"{host} had a server error (HTTP {code}). Try again later."
    try:
        detail = _error_text(r.json())
    except ValueError:
        detail = r.text[:200]
    return f"{host} rejected the request (HTTP {code}): {detail}"


def _error_text(body: Any) -> str:
    if isinstance(body, dict):
        err = body.get("error") or body.get("message") or body.get("errors") or body
        if isinstance(err, dict):
            err = err.get("message") or err.get("msg") or err
        return str(err)[:300]
    return str(body)[:300]
