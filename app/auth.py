"""Access control.

Requests from this PC itself (loopback) are always trusted. For everything else there are two modes:

* REQUIRE_TOKEN=1 (the default): the caller must present the API token, in any one of
    - an `Authorization: Bearer <token>` header   (the API)
    - a `?token=<token>` query parameter           (a pairing link)
    - the `af_token` cookie                        (set after a good token)
  Use this when the app is reachable beyond your own network (Tailscale, a tunnel).

* REQUIRE_TOKEN=0 ("home network" mode): devices on a private network (home Wi-Fi/LAN addresses such
  as 192.168.x.x or 10.x.x.x, and Tailscale's 100.64.x.x) may use the app with nothing to enter.
  A request from a PUBLIC address still needs the token, so an accidental router port-forward does not
  open the app to the internet. Anyone who joins your Wi-Fi can use the app in this mode.

Requests relayed by a local proxy (`tailscale serve`, a tunnel) arrive from 127.0.0.1 but carry
X-Forwarded-* headers; those are never treated as local or as a private-network device.
"""
import hmac
import ipaddress

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse

from . import config

LOOPBACK = {"127.0.0.1", "::1", "localhost"}
COOKIE = "af_token"
PUBLIC_GET_PATHS = {"/apk", "/apk/version"}  # the app installer itself: contains no token or address, so anyone on the network may fetch it
MIN_TOKEN_LEN = 20
_PROXY_HEADERS = ("x-forwarded-for", "x-forwarded-host", "forwarded", "x-real-ip")
# Exactly the ranges that can only be a device on a local network: home LAN (RFC 1918), link-local, IPv6
# unique-local/link-local, and Tailscale. (ipaddress.is_private also counts reserved test ranges: too loose.)
_HOME_NETWORKS = [ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "100.64.0.0/10", "fc00::/7", "fe80::/10")]


def _relayed(request: Request) -> bool:
    return any(h in request.headers for h in _PROXY_HEADERS)


def is_local(request: Request) -> bool:
    host = request.client.host if request.client else ""
    return host in LOOPBACK and not _relayed(request)


def is_private_network(request: Request) -> bool:
    """A device on a private network: LAN (RFC 1918), link-local, or Tailscale. Never a relayed request."""
    if _relayed(request):
        return False
    try:
        ip = ipaddress.ip_address(request.client.host if request.client else "")
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return any(ip in net for net in _HOME_NETWORKS)


def _same(supplied: str) -> bool:
    return bool(config.API_TOKEN) and bool(supplied) and hmac.compare_digest(supplied, config.API_TOKEN)


def token_from(request: Request):
    """Returns (token, how) where how is 'header', 'query', 'cookie' or None."""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer ") and _same(auth[7:]):
        return auth[7:], "header"
    q = request.query_params.get("token", "")
    if _same(q):
        return q, "query"
    c = request.cookies.get(COOKIE, "")
    if _same(c):
        return c, "cookie"
    return None, None


async def auth_middleware(request: Request, call_next):
    if is_local(request) or (request.method == "GET" and request.url.path in PUBLIC_GET_PATHS):
        return await call_next(request)
    if not config.REQUIRE_TOKEN and is_private_network(request):
        return await call_next(request)
    token, how = token_from(request)
    if not token:
        if "text/html" in request.headers.get("accept", ""):
            return HTMLResponse(
                "<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'>"
                "<body style='font:16px system-ui;padding:24px'><h2>Access denied</h2>"
                "<p>This is a private app. Open it from the Adoption Finder phone app on your home network, "
                "or use the pairing link from the PC (http://localhost:8000/pair).</p></body>", status_code=401)
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    response = await call_next(request)
    if how in ("query", "header") and request.cookies.get(COOKIE) != token:
        # remember the login so the page's own script/style/stream requests (which can't send headers) work
        response.set_cookie(COOKIE, token, max_age=365 * 86400, httponly=True, samesite="lax", path="/")
    return response
