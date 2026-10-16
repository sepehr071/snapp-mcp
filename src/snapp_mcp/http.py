"""Shared async HTTP client for the Snappfood and SnappMarket APIs.

Every tool goes through `fetch`, which adds the PWA client params, caps concurrency
and turns HTTP failures into `ToolError` messages the model can act on.
"""

from __future__ import annotations

import asyncio
import copy
import json as json_lib
import os
import time
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
CACHE_TTL = 60  # seconds; menus and prices must stay fresh
CACHE_MAX = 64
RETRY_STATUS = {502, 503, 504}
RETRY_DELAY = 0.5  # seconds, fixed
MAX_BODY = 5_000_000  # bytes; larger responses are refused
HOSTS = {"snappfood.ir", "apigw.snappfood.ir", "svc.snapp.market"}

_transport: httpx.AsyncBaseTransport | None = None
_client: httpx.AsyncClient | None = None
_limit: asyncio.Semaphore | None = None
_auth_lock: asyncio.Lock | None = None
_cache: dict[tuple, tuple[float, Any]] = {}


class ApiError(ToolError):
    """A failed upstream call. `status` is the HTTP status, or None for network errors."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """Swap the transport (tests use httpx.MockTransport). Drops the current client."""
    global _transport, _client, _limit, _auth_lock
    _transport, _client, _limit, _auth_lock = transport, None, None, None
    _cache.clear()


def _get_client() -> tuple[httpx.AsyncClient, asyncio.Semaphore]:
    global _client, _limit, _auth_lock
    if _client is None:
        # Snappfood's firewall blocks most non-Iranian exits, so ignore system proxy
        # settings unless the user opts in with SNAPP_MCP_PROXY.
        _client = httpx.AsyncClient(
            transport=_transport,
            headers=HEADERS,
            timeout=httpx.Timeout(20, connect=5),
            follow_redirects=False,
            trust_env=False,
            proxy=os.environ.get("SNAPP_MCP_PROXY") or None,
        )
        _limit = asyncio.Semaphore(MAX_CONCURRENCY)
        _auth_lock = asyncio.Lock()
    assert _limit is not None
    return _client, _limit


def auth_lock() -> asyncio.Lock:
    """Serializes the anonymous-token refresh so concurrent first calls fetch one token."""
    _get_client()
    assert _auth_lock is not None
    return _auth_lock


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
    if host not in HOSTS:
        raise ApiError(f"Refusing to call unexpected host {host}.")
    key = (url, tuple(sorted(query.items())), (headers or {}).get("Authorization"))
    if method == "GET" and (hit := _cache.get(key)) and time.monotonic() - hit[0] < CACHE_TTL:
        return copy.deepcopy(hit[1])
    for attempt in (0, 1):  # GETs retry once on timeout/502/503/504; POST never
        last = attempt == 1 or method != "GET"
        try:
            async with limit:
                async with client.stream(method, url, params=query, headers=headers, json=json) as r:
                    content = await _read_capped(r, host)
        except httpx.TimeoutException as e:
            if not last:
                await asyncio.sleep(RETRY_DELAY)
                continue
            raise ApiError(f"{host} did not answer in time. Try again in a moment.") from e
        except httpx.RequestError as e:
            raise ApiError(
                f"Could not reach {host} ({type(e).__name__}). Check the internet connection; "
                "Snappfood only serves Iranian IP addresses."
            ) from e
        if r.status_code in RETRY_STATUS and not last:
            await asyncio.sleep(RETRY_DELAY)
            continue
        break

    if r.status_code >= 400:
        raise ApiError(_status_message(r, content, host), r.status_code)
    try:
        body = json_lib.loads(content)
    except ValueError as e:
        raise ApiError(f"{host} returned a non-JSON response (HTTP {r.status_code}).", r.status_code) from e
    if isinstance(body, dict) and body.get("status") is False:
        raise ApiError(f"{host} rejected the request: {_error_text(body)}", r.status_code)
    if method == "GET":
        if len(_cache) >= CACHE_MAX:
            _cache.pop(next(iter(_cache)))
        _cache[key] = (time.monotonic(), copy.deepcopy(body))
    return body


async def _read_capped(r: httpx.Response, host: str) -> bytes:
    """Read the body, refusing anything over MAX_BODY so one huge answer cannot flood memory or context."""
    buf = bytearray()
    async for chunk in r.aiter_bytes():
        buf += chunk
        if len(buf) > MAX_BODY:
            raise ApiError(f"{host} sent a response over {MAX_BODY // 1_000_000} MB; refused.")
    return bytes(buf)


def _status_message(r: httpx.Response, content: bytes, host: str) -> str:
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
        detail = _error_text(json_lib.loads(content))
    except ValueError:
        detail = content[:200].decode("utf-8", "replace")
    return f"{host} rejected the request (HTTP {code}): {detail}"


def _error_text(body: Any) -> str:
    if isinstance(body, dict):
        err = body.get("error") or body.get("message") or body.get("errors") or body
        if isinstance(err, dict):
            err = err.get("message") or err.get("msg") or err
        return str(err)[:300]
    return str(body)[:300]
