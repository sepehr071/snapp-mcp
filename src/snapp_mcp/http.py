"""Shared async HTTP client for the Snappfood and SnappMarket APIs.

Every tool goes through `fetch`, which adds the PWA client params, caps concurrency
and turns HTTP failures into `ToolError` messages the model can act on.
"""

from __future__ import annotations

import asyncio
import copy
import json as json_lib
import os
import random
import re
import time
from typing import Any

import httpx
from curl_cffi.const import CurlOpt
from httpx_curl_cffi import AsyncCurlTransport
from mcp.server.mcpserver.exceptions import ToolError

FOOD_BASE = "https://snappfood.ir"
FOOD_APIGW = "https://apigw.snappfood.ir"
MARKET_BASE = "https://svc.snapp.market"

FOOD_PARAMS = {"optionalClient": "PWA", "client": "PWA", "deviceType": "PWA", "appVersion": "6.0.0"}
MARKET_PARAMS = {"client": "PWA", "deviceType": "PWA", "appVersion": "1.403.1"}

HEADERS = {
    # User-Agent, sec-ch-ua and TLS come from the impersonated Chrome profile (see _get_client).
    "Accept": "application/json",
    "Accept-Language": "fa-IR,fa;q=0.9,en;q=0.8",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "priority": "u=1, i",
}

MAX_CONCURRENCY = 2
JITTER = (0.15, 0.6)  # seconds before each uncached call
MAX_RETRIES = 2
MAX_RETRY_WAIT = 10  # cap for Retry-After and backoff, seconds
CACHE_TTL = 60  # seconds; menus and prices must stay fresh
CACHE_MAX = 64
RETRY_DELAY = 0.5  # seconds, doubled per retry
MAX_BODY = 5_000_000  # bytes; larger responses are refused
HOSTS = {"snappfood.ir", "apigw.snappfood.ir", "svc.snapp.market"}
# The web app each host serves: its Origin/Referer, and where a browser opens to pass a bot check.
SITES = {
    "snappfood.ir": "https://snappfood.ir",
    "apigw.snappfood.ir": "https://snappfood.ir",
    "svc.snapp.market": "https://snapp.market",
}

_transport: httpx.AsyncBaseTransport | None = None
_client: httpx.AsyncClient | None = None
_limit: asyncio.Semaphore | None = None
_auth_lock: asyncio.Lock | None = None
_proxy_index = 0
_retired: set[asyncio.Task] = set()
_browser_lock: asyncio.Lock | None = None
_cleared: tuple[httpx.AsyncClient, str, float] | None = None
_cache: dict[tuple, tuple[float, Any]] = {}


class _XhrTransport(AsyncCurlTransport):
    """The Chrome profile sends navigation-only headers; a None value makes curl drop them (API calls are XHR)."""

    def _create_request_params(self, req: httpx.Request) -> dict[str, Any]:
        p = super()._create_request_params(req)
        p["headers"] = [*p["headers"], (b"upgrade-insecure-requests", None), (b"sec-fetch-user", None)]
        return p


class ApiError(ToolError):
    """A failed upstream call. `status` is the HTTP status, or None for network errors."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """Swap the transport (tests use httpx.MockTransport). Drops the current client."""
    global _transport, _client, _limit, _auth_lock, _proxy_index, _browser_lock, _cleared
    _transport, _client, _limit, _auth_lock, _proxy_index, _browser_lock, _cleared = (
        transport,
        None,
        None,
        None,
        0,
        None,
        None,
    )
    _cache.clear()


def _proxies() -> list[str]:
    """SNAPP_MCP_PROXY: one proxy URL, or a comma-separated list used one at a time."""
    return [p.strip() for p in os.environ.get("SNAPP_MCP_PROXY", "").split(",") if p.strip()]


# Plain firewall pages are not challenges: only a page with a bot-check marker is worth a browser.
_CHALLENGE = re.compile(r"just a moment|captcha|challenge|arvan|attention required|enable javascript", re.I)


def _is_challenge(r: httpx.Response, content: bytes) -> bool:
    """A bot-check page: 403/429/503 with an HTML body that carries a known challenge marker."""
    text = content[:5000].decode("utf-8", "replace")
    return (
        r.status_code in (403, 429, 503)
        and "html" in r.headers.get("content-type", "") + text[:20].lower()
        and bool(_CHALLENGE.search(text))
    )


def _next_proxy(client: httpx.AsyncClient) -> None:
    """Sticky pool: move to the next proxy after a block or connect error; the next call rebuilds the client.

    Only the client that failed may rotate: a slower request that failed on an old client must not skip a good proxy.
    The old client is closed late because other requests may still be using it.
    """
    global _client, _limit, _auth_lock, _proxy_index
    if len(_proxies()) < 2 or client is not _client:
        return
    _client, _limit, _auth_lock, _proxy_index = None, None, None, _proxy_index + 1
    t = asyncio.create_task(_close_later(client))
    _retired.add(t)
    t.add_done_callback(_retired.discard)


async def _close_later(client: httpx.AsyncClient) -> None:
    await asyncio.sleep(30)  # longer than the 20 s request timeout
    await client.aclose()


def _get_client() -> tuple[httpx.AsyncClient, asyncio.Semaphore]:
    global _client, _limit, _auth_lock
    if _client is None:
        # Snappfood's firewall blocks most non-Iranian exits, so ignore system proxy
        # settings unless the user opts in with SNAPP_MCP_PROXY. curl_cffi presents Chrome's TLS/HTTP2
        # fingerprint and default headers; cookies stay with httpx. The proxy goes to the transport
        # (a client-level proxy would be ignored once a transport is given).
        proxies = _proxies()
        _client = httpx.AsyncClient(
            transport=_transport
            or _XhrTransport(
                impersonate="chrome",
                default_headers=True,
                curl_options={
                    CurlOpt.NOPROXY: "*"
                },  # httpx-curl-cffi drops its own NOPROXY unless this dict is non-empty
                proxy=proxies[_proxy_index % len(proxies)] if proxies else None,
                max_connections=2 * MAX_CONCURRENCY,
            ),
            headers=HEADERS,
            timeout=httpx.Timeout(20, connect=5),
            limits=httpx.Limits(max_connections=2 * MAX_CONCURRENCY),
            follow_redirects=False,
            trust_env=False,
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


def _frontend_headers(host: str, method: str) -> dict[str, str]:
    """What the web app's own fetch sends: its Origin/Referer and sec-fetch-site for this API host."""
    site = SITES[host]
    same_origin = host == httpx.URL(site).host
    h = {"Referer": f"{site}/", "sec-fetch-site": "same-origin" if same_origin else "same-site"}
    if not same_origin or method != "GET":  # browsers leave Origin off same-origin GETs
        h["Origin"] = site
    return h


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
    A GET is retried up to MAX_RETRIES times on timeout, network error, 5xx or 429, with jittered exponential
    backoff or after Retry-After when sent. 403 and POST are never retried.
    """
    query = {**(common or {}), **(params or {})}
    host = httpx.URL(url).host
    if host not in HOSTS:
        raise ApiError(f"Refusing to call unexpected host {host}.")
    key = (url, tuple(sorted(query.items())), (headers or {}).get("Authorization"))
    if method == "GET" and (hit := _cache.get(key)) and time.monotonic() - hit[0] < CACHE_TTL:
        return copy.deepcopy(hit[1])
    headers = {**_frontend_headers(host, method), **(headers or {})}
    for attempt in range(MAX_RETRIES + 1):
        last = attempt == MAX_RETRIES or method != "GET"
        wait = RETRY_DELAY * 2**attempt * random.uniform(1, 1.5)
        try:
            r, content = await _request(method, url, query, headers, json, host)
        except ApiError as e:
            if last or e.status is not None:
                raise
            await asyncio.sleep(min(wait, MAX_RETRY_WAIT))
            continue
        if (r.status_code == 429 or r.status_code >= 500) and not last:
            await asyncio.sleep(min(_retry_after(r) or wait, MAX_RETRY_WAIT))
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


async def _send(client, limit, method, url, query, headers, json, host) -> tuple[httpx.Response, bytes]:
    try:
        async with limit:
            await asyncio.sleep(random.uniform(*JITTER))
            async with client.stream(method, url, params=query, headers=headers, json=json) as r:
                return r, await _read_capped(r, host)
    except httpx.TimeoutException as e:
        raise ApiError(f"{host} did not answer in time. Try again in a moment.") from e
    except httpx.RequestError as e:
        if isinstance(e, httpx.ConnectError | httpx.ProxyError):
            _next_proxy(client)
        raise ApiError(
            f"Could not reach {host} ({type(e).__name__}). Check the internet connection; "
            "Snappfood only serves Iranian IP addresses."
        ) from e


async def _request(method, url, query, headers, json, host) -> tuple[httpx.Response, bytes]:
    client, limit = _get_client()
    r, content = await _send(client, limit, method, url, query, headers, json, host)
    if _is_challenge(r, content):
        _next_proxy(client)
        client, limit = _get_client()
        await _clear_challenge(client, SITES[host])
        r, content = await _send(
            client, limit, method, url, query, headers, json, host
        )  # one retry; a second block is final
        if _is_challenge(r, content):
            _next_proxy(client)
    elif r.status_code in (403, 429):
        _next_proxy(client)  # blocked without a challenge page: try the next proxy on the following call
    return r, content


async def _browser_cookies(site: str, proxy: str | None) -> list[dict[str, Any]]:
    """Open the site's home page in headless patchright, wait for the challenge to clear, return the cookies."""
    try:
        from patchright.async_api import async_playwright
    except ImportError as e:
        raise ApiError(
            "Snappfood/SnappMarket served a bot-check page. Install the browser extra (`pip install snapp-mcp[browser]`, "
            "or `uvx --with patchright==1.63.0 snapp-mcp`) and run `patchright install chromium`.",
            403,
        ) from e
    u = httpx.URL(proxy) if proxy else None
    opts = u and {"server": f"{u.scheme}://{u.host}:{u.port}", "username": u.username, "password": u.password}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, proxy=opts)
        try:
            page = await browser.new_page()
            await page.goto(site, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_function(
                "!/just a moment|access denied|attention required|arvancloud/i.test(document.title)", timeout=30_000
            )
            return await browser.contexts[0].cookies()
        finally:
            await browser.close()


async def _clear_challenge(client: httpx.AsyncClient, site: str) -> None:
    """Rung 2: let a real browser pass the bot check once and share its cookies with the httpx jar."""
    global _browser_lock, _cleared
    _browser_lock = _browser_lock or asyncio.Lock()
    async with _browser_lock:  # one browser at a time; callers that waited reuse the cookies just fetched
        if _cleared and _cleared[:2] == (client, site) and time.monotonic() - _cleared[2] < 30:
            return
        proxies = _proxies()
        try:
            cookies = await _browser_cookies(site, proxies[_proxy_index % len(proxies)] if proxies else None)
        except ApiError:
            raise
        except Exception as e:
            raise ApiError(f"The bot check at {site} was not cleared by the browser ({type(e).__name__}).", 403) from e
        for c in cookies:
            client.cookies.set(c["name"], c["value"], domain=c["domain"], path=c["path"])
        _cleared = (client, site, time.monotonic())


async def _read_capped(r: httpx.Response, host: str) -> bytes:
    """Read the body, refusing anything over MAX_BODY so one huge answer cannot flood memory or context."""
    buf = bytearray()
    async for chunk in r.aiter_bytes():
        buf += chunk
        if len(buf) > MAX_BODY:
            raise ApiError(f"{host} sent a response over {MAX_BODY // 1_000_000} MB; refused.", r.status_code)
    return bytes(buf)


def _retry_after(r: httpx.Response) -> float | None:
    """Seconds from a numeric Retry-After on a 429/503, else None (HTTP-date form is ignored)."""
    v = r.headers.get("retry-after", "")
    return float(v) if r.status_code in (429, 503) and v.replace(".", "", 1).isdigit() else None


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
