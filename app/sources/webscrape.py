"""Generic, config-driven website scraper.

Describe a site in a JSON file under sites/ and it becomes a Source; no Python needed per site.
Requests go through curl_cffi, which reproduces a real browser's TLS/HTTP2 fingerprint. Plain
`requests`/`httpx` get 403 from some bot filters purely because of that fingerprint.

Politeness built in (this is for one person's listing checks, not bulk crawling):
  * robots.txt is honoured whenever it can be read
  * a minimum delay between requests to the same host (default 2 s), capped page counts
  * a block page / 403 / 429 raises BlockedError. It is reported in the UI as degraded and is
    NOT retried harder. Nothing here rotates proxies, solves captchas or drives a browser.

Site definition (see sites/README.md for the full reference and examples):
{
  "name": "PAWS",  "org": "PAWS Lynnwood",  "location": {"zip": "98087"},
  "urls": ["https://example.org/dogs/?page={page}"],  "pagination": {"max_pages": 5},
  "mode": "css" | "jsonld" | "embedded_json" | "json",
  "items": "<css selector | dotted path>",  "require": ["id", "photo"],
  "fields": {"name": "h3", "photo": "img@src", "url": "a@href", "breed": ".breed", ...}
}
"""
import hashlib
import json
import logging
import re
import threading
import time
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

from bs4 import BeautifulSoup
from curl_cffi import requests as cffi

from .. import config, geo, normalize
from .base import Source

log = logging.getLogger("webscrape")

BLOCK_MARKERS = ("access denied", "temporarily unavailable", "captcha", "verify you are human",
                 "unusual traffic", "request blocked")
DEFAULT_DELAY = 2.0
ROBOTS_TTL = 24 * 3600


class BlockedError(RuntimeError):
    """The site refused us (403/429/block page). We stop rather than escalate."""


class Fetcher:
    """Shared, rate-limited, robots-aware HTTP client using a browser TLS fingerprint."""

    def __init__(self, impersonate="chrome", delay=DEFAULT_DELAY, timeout=30):
        self.impersonate, self.delay, self.timeout = impersonate, delay, timeout
        self._session = None
        self._last = {}       # host -> time of last request
        self._robots = {}     # host -> (fetched_at, RobotFileParser | None)
        self._lock = threading.Lock()

    @property
    def session(self):
        if self._session is None:
            self._session = cffi.Session(impersonate=self.impersonate)
        return self._session

    def _wait(self, host, delay):
        """Reserve the next request slot for this host; sleep outside the lock so other hosts aren't held up."""
        with self._lock:
            now = time.time()
            slot = max(now, self._last.get(host, 0))
            self._last[host] = slot + delay
        if slot > now:
            time.sleep(slot - now)

    def allowed(self, url, delay=None):
        p = urlparse(url)
        host = f"{p.scheme}://{p.netloc}"
        cached = self._robots.get(host)
        if not cached or time.time() - cached[0] > ROBOTS_TTL:
            rp = None
            try:
                self._wait(p.netloc, delay or self.delay)
                r = self.session.get(host + "/robots.txt", timeout=self.timeout)
                if r.status_code == 200 and "<html" not in r.text[:500].lower():
                    rp = RobotFileParser()
                    rp.parse(r.text.splitlines())
            except Exception as e:  # unreadable robots.txt: proceed, but stay polite
                log.info("robots.txt unreadable for %s: %s", host, e)
            cached = self._robots[host] = (time.time(), rp)
        return cached[1].can_fetch("*", url) if cached[1] else True

    def get(self, url, *, delay=None, headers=None, respect_robots=True, retries=1):
        if respect_robots and not self.allowed(url, delay):
            raise PermissionError(f"robots.txt disallows {url}")
        host = urlparse(url).netloc
        for attempt in range(retries + 1):
            self._wait(host, delay or self.delay)
            try:
                r = self.session.get(url, timeout=self.timeout, headers=headers or {})
            except Exception as e:
                if attempt == retries:
                    raise
                time.sleep(3 * (attempt + 1))
                continue
            if r.status_code in (403, 429) or (r.status_code == 200 and _looks_blocked(r.text)):
                raise BlockedError(f"HTTP {r.status_code} / block page from {host}")
            if r.status_code >= 500 and attempt < retries:
                time.sleep(3 * (attempt + 1))
                continue
            r.raise_for_status()
            return r
        raise RuntimeError("unreachable")


def _looks_blocked(text):
    """Block pages often come back as HTTP 200 with a telltale <title>."""
    m = re.search(r"<title[^>]*>([^<]*)", text[:6000], re.I)
    title = (m.group(1) if m else "").lower()
    return any(marker in title for marker in BLOCK_MARKERS)


# ---- extraction helpers ---------------------------------------------------------

def dig(obj, path):
    """Dotted path into nested dict/list ('a.b.0.c'). Missing -> None."""
    cur = obj
    for part in str(path).split("."):
        if part == "":
            continue
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.lstrip("-").isdigit():
            try:
                cur = cur[int(part)]
            except IndexError:
                return None
        else:
            return None
        if cur is None:
            return None
    return cur


def _spec(f):
    return {"sel": f} if isinstance(f, str) else dict(f)


def _css_value(node, spec, base):
    sel, attr = spec.get("sel") or spec.get("path"), spec.get("attr")
    if isinstance(sel, str) and "@" in sel and not attr:
        sel, attr = sel.rsplit("@", 1)
    targets = [node] if sel in (None, "", ":scope", ":self") else node.select(sel)
    if not targets:
        return None
    vals = []
    for t in (targets if spec.get("all") else targets[:1]):
        v = t.get(attr) if attr else t.get_text(" ", strip=True)
        if isinstance(v, list):
            v = " ".join(v)
        if v and attr in ("href", "src", "data-src", "srcset", "poster"):
            v = urljoin(base, v.split(",")[0].split(" ")[0] if attr == "srcset" else v)
        if v:
            vals.append(v.strip())
    if not vals:
        return None
    return vals if spec.get("all") else vals[0]


def _json_value(item, spec):
    if "template" in spec:
        return spec["template"].format_map(_Safe(item if isinstance(item, dict) else {}))
    v = dig(item, spec.get("path") or spec.get("sel"))
    key = spec.get("key")
    if isinstance(v, list):
        v = [(dig(x, key) if key else x) for x in v]
        v = [x for x in v if x]
        if not spec.get("all") and "join" not in spec:
            return v
    if isinstance(v, (dict, list)) and "join" in spec:
        v = spec["join"].join(str(x) for x in (v if isinstance(v, list) else v.values()))
    elif isinstance(v, list) and "join" in spec:
        v = spec["join"].join(str(x) for x in v)
    return v


class _Safe(dict):
    def __missing__(self, key):
        return ""


def _post(v, spec):
    if isinstance(v, str):
        v = re.sub(r"\s+", " ", v).strip()
        if spec.get("regex"):
            m = re.search(spec["regex"], v)
            v = (m.group(1) if m and m.groups() else m.group(0)) if m else None
        if v is not None and spec.get("map"):
            v = spec["map"].get(v, spec["map"].get(v.lower(), v))
    if isinstance(v, (str, int, float)) and v != "" and (spec.get("prefix") or spec.get("suffix")):
        v = f"{spec.get('prefix', '')}{v}{spec.get('suffix', '')}"
    if v in (None, "", []) and "default" in spec:
        return spec["default"]
    return v


def extract_items(site, page_text, page_url):
    """Return a list of raw field dicts for one fetched page."""
    mode = site.get("mode", "css")
    fields = {k: _spec(v) for k, v in site["fields"].items()}
    raw = []
    if mode == "css":
        soup = BeautifulSoup(page_text, "lxml")
        for node in soup.select(site["items"]):
            raw.append({k: _post(_css_value(node, s, page_url), s) for k, s in fields.items()})
        return raw
    if mode == "jsonld":
        objs = []
        for m in re.finditer(r'<script[^>]*ld\+json[^>]*>(.*?)</script>', page_text, re.S | re.I):
            try:
                objs.append(json.loads(m.group(1)))
            except ValueError:
                pass
        data = objs
        items = []
        want = site.get("items")  # optional @type filter e.g. "Product|Animal"
        stack = list(data)
        while stack:
            o = stack.pop(0)
            if isinstance(o, list):
                stack.extend(o)
            elif isinstance(o, dict):
                t = o.get("@type")
                t = " ".join(t) if isinstance(t, list) else (t or "")
                if (not want and "ItemList" not in t and t) or (want and re.search(want, t)):
                    items.append(o)
                for v in o.values():
                    if isinstance(v, (dict, list)):
                        stack.append(v)
        data = items
    elif mode == "embedded_json":
        m = re.search(site["blob_regex"], page_text, re.S)
        if not m:
            return []
        data = dig(json.loads(m.group(1)), site["items"])
    elif mode == "json":
        data = dig(json.loads(page_text), site["items"]) if site.get("items") else json.loads(page_text)
    else:
        raise ValueError(f"unknown mode {mode!r}")
    for it in data or []:
        raw.append({k: _post(_json_value(it, s), s) for k, s in fields.items()})
    return raw


# ---- normalization ---------------------------------------------------------------

_SEX = re.compile(r"\b(female|male|girl|boy)\b", re.I)


def to_listing(site, raw, page_url):
    name = (raw.get("name") or "").strip() if isinstance(raw.get("name"), str) else None
    url = raw.get("url")
    if isinstance(url, str):
        url = urljoin(page_url, url)
    sid = raw.get("id")
    if not sid:
        basis = url or f"{name}|{raw.get('breed')}|{raw.get('age')}"
        sid = hashlib.sha1(basis.encode()).hexdigest()[:14]
    if not name and not url:
        return None
    if any(raw.get(k) in (None, "", []) for k in site.get("require", [])):
        return None  # e.g. promo/ad cards that share the dog-card markup
    species = raw.get("species")
    if species and "dog" not in str(species).lower() and "canine" not in str(species).lower():
        return None
    age = raw.get("age")
    age_text = str(age).strip() if age not in (None, "") else None
    months = normalize.parse_age_months(age_text) if age_text else None
    group = normalize.age_group_from_label(age_text.split()[0].lower()) if age_text else None
    if age_text and not group:
        for label in ("puppy", "senior", "adult", "young", "baby"):
            if label in age_text.lower():
                group = normalize.age_group_from_label(label)
                break
    group = group or normalize.age_group_from_months(months)
    breed = str(raw.get("breed") or "").strip()
    breed = re.sub(r"\([^)]*\)", "", breed)  # "(up to 44 lbs fully grown)"
    size_in_breed = re.search(r",\s*(small|medium|large|x-?large|giant)\b", breed, re.I)  # "Mixed Breed, Large"
    breed = re.sub(r",\s*(small|medium|large|x-?large|giant)\b.*$", "", breed, flags=re.I)
    splitter = r"\s*[/,&]\s*|\s+and\s+" if site.get("breed_split", True) else r"$^"  # "Terrier, Pit Bull" is ONE breed on some platforms
    parts = [p.strip() for p in re.split(splitter, breed) if p.strip()]
    if raw.get("breed2"):
        parts.append(str(raw["breed2"]).strip())
    parts = [p for p in parts if p.lower() not in ("small", "medium", "large", "x-large", "xlarge", "giant")]
    mixed = any(p.lower() in ("mix", "mixed", "mixed breed") or p.lower().endswith(" mix") for p in parts)
    parts = [re.sub(r"\s+mix$", "", p, flags=re.I) or p for p in parts]  # "Labrador Retriever Mix" -> breed + mixed flag
    sm = _SEX.search(str(raw.get("sex") or ""))
    sex = {"girl": "female", "boy": "male"}.get(sm.group(1).lower(), sm.group(1).lower()) if sm else None
    photos = raw.get("photo") or raw.get("photos") or []
    photos = [photos] if isinstance(photos, str) else list(photos)
    photos = [urljoin(page_url, p) for p in photos if isinstance(p, str)]
    desc = raw.get("description")
    desc = normalize.strip_html(desc) if isinstance(desc, str) else None
    loc = site.get("location") or {}
    return {
        "source": site["name"], "source_id": str(sid), "url": url,
        "name": name or "Unnamed",
        "breed_primary": parts[0] if parts else "Unknown breed",
        "breed_secondary": parts[1] if len(parts) > 1 and parts[1].lower() not in ("mix", "mixed") else None,
        "mixed": 1 if mixed or len(parts) > 1 else 0,
        "color": raw.get("color"), "age_group": group, "age_text": age_text, "age_months": months,
        "sex": sex, "size": normalize.norm_size(str(raw.get("size") or "")) or (normalize.norm_size(size_in_breed.group(1)) if size_in_breed else None),
        "description": desc, "photos": photos,
        "city": raw.get("city") or loc.get("city"), "state": raw.get("state") or loc.get("state") or "WA",
        "zip": (str(raw.get("zip") or loc.get("zip") or "")[:5] or None),
        "lat": None, "lon": None,
        "org": site.get("org") or site["name"],
        "transport": 1 if site.get("transport") else normalize.detect_transport(desc),
        "listed_at": normalize.to_epoch(raw.get("listed")),
    }


# ---- the Source ------------------------------------------------------------------

_fetcher = Fetcher()


class WebSite(Source):
    def __init__(self, site, fetcher=None):
        for k in ("name", "urls", "fields"):
            if k not in site:
                raise ValueError(f"site definition missing {k!r}")
        self.site, self.name = site, site["name"]
        self.fetcher = fetcher or _fetcher

    def enabled(self):
        return self.site.get("enabled", True)

    def pages(self):
        pg = self.site.get("pagination") or {}
        for tpl in self.site["urls"]:
            if "{page}" in tpl:
                for n in range(pg.get("start", 1), pg.get("start", 1) + min(pg.get("max_pages", 3), 10)):
                    yield tpl.format(page=n)
            else:
                yield tpl

    def scrape(self):
        """All listings from the site, without geography filtering."""
        out, seen_first = [], set()
        delay = self.site.get("delay_seconds", DEFAULT_DELAY)
        headers = self.site.get("headers")
        for url in self.pages():
            r = self.fetcher.get(url, delay=delay, headers=headers,
                                 respect_robots=self.site.get("respect_robots", True))
            items = extract_items(self.site, r.text, url)
            listings = [l for l in (to_listing(self.site, i, url) for i in items) if l]
            ids = tuple(l["source_id"] for l in listings[:3])
            if not listings or ids in seen_first:
                break  # empty page, or pagination repeating itself
            seen_first.add(ids)
            out.extend(listings)
        return list({l["source_id"]: l for l in out}.values())

    def fetch(self, region, db=None):
        loc = self.site.get("location") or {}
        g = geo.geocode(db, loc["zip"]) if (db and loc.get("zip")) else None
        if g and not geo.in_region(region, g) and not self.site.get("transport"):
            return []  # fixed-location org outside the search area: don't even request it
        listings = self.scrape()
        for l in listings:
            zg = g or (geo.geocode(db, l["zip"]) if (db and l["zip"]) else None)
            if zg:
                l["lat"], l["lon"] = zg["lat"], zg["lon"]
                l["city"] = l["city"] or zg["label"].split(",")[0]
        return listings


def load_sites(directory=None):
    """One WebSite source per valid *.json in sites/ (a file may hold one object or a list)."""
    directory = directory or (config.ROOT / "sites")
    sources = []
    for path in sorted(directory.glob("*.json")) if directory.exists() else []:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for site in (data if isinstance(data, list) else [data]):
                sources.append(WebSite(site))
        except Exception as e:
            log.error("skipping bad site definition %s: %s", path.name, e)
    return sources
