"""Adopt-a-Pet source.

There is no public API and /dog-adoption/search is disallowed by robots.txt, so this
uses only what the site publishes for crawlers:
  1. the sitemap (pets_N.xml.gz) - every pet URL, slug = <id>-<city>-<state>-<breed...>
  2. individual /pet/<id> pages, which embed the full record as Next.js flight data.
Candidates are narrowed by the city in the slug, so only nearby dogs are ever fetched.
"""
import gzip
import json
import logging
import math
import re
import time
import xml.etree.ElementTree as ET

import httpx

from .. import config, geo, normalize
from .base import Source

log = logging.getLogger("adoptapet")

SITE = "https://www.adoptapet.com"
SITEMAP_INDEX = f"{SITE}/sitemaps/sitemapindex.xml"
DETAIL_DELAY = 4.0        # seconds between page fetches (their firewall challenges us at ~1/s)
COOLDOWN = 30 * 60        # after a firewall challenge, leave them alone this long
MAX_DETAILS_PER_CYCLE = 250
VERIFY_PER_CLEANUP = 450  # dogs whose own page is opened during a cleanup (~30 min at DETAIL_DELAY); the rest wait for the next one
VERIFIED_KEY = "adoptapet_verified"

# state slug -> (abbr, min_lat, max_lat, min_lon, max_lon)  (rough bounding boxes)
STATES = {
    "alabama": ("AL", 30.2, 35.0, -88.5, -84.9), "alaska": ("AK", 51.0, 71.5, -180, -129),
    "arizona": ("AZ", 31.3, 37.0, -114.8, -109.0), "arkansas": ("AR", 33.0, 36.5, -94.6, -89.6),
    "california": ("CA", 32.5, 42.0, -124.5, -114.1), "colorado": ("CO", 37.0, 41.0, -109.1, -102.0),
    "connecticut": ("CT", 41.0, 42.1, -73.7, -71.8), "delaware": ("DE", 38.4, 39.8, -75.8, -75.0),
    "district-of-columbia": ("DC", 38.8, 39.0, -77.1, -76.9), "florida": ("FL", 24.5, 31.0, -87.6, -80.0),
    "georgia": ("GA", 30.4, 35.0, -85.6, -80.8), "hawaii": ("HI", 18.9, 22.3, -160.3, -154.8),
    "idaho": ("ID", 42.0, 49.0, -117.2, -111.0), "illinois": ("IL", 37.0, 42.5, -91.5, -87.5),
    "indiana": ("IN", 37.8, 41.8, -88.1, -84.8), "iowa": ("IA", 40.4, 43.5, -96.6, -90.1),
    "kansas": ("KS", 37.0, 40.0, -102.1, -94.6), "kentucky": ("KY", 36.5, 39.1, -89.6, -81.9),
    "louisiana": ("LA", 28.9, 33.0, -94.0, -89.0), "maine": ("ME", 43.0, 47.5, -71.1, -66.9),
    "maryland": ("MD", 37.9, 39.7, -79.5, -75.0), "massachusetts": ("MA", 41.2, 42.9, -73.5, -69.9),
    "michigan": ("MI", 41.7, 48.3, -90.4, -82.4), "minnesota": ("MN", 43.5, 49.4, -97.2, -89.5),
    "mississippi": ("MS", 30.2, 35.0, -91.7, -88.1), "missouri": ("MO", 36.0, 40.6, -95.8, -89.1),
    "montana": ("MT", 44.4, 49.0, -116.1, -104.0), "nebraska": ("NE", 40.0, 43.0, -104.1, -95.3),
    "nevada": ("NV", 35.0, 42.0, -120.0, -114.0), "new-hampshire": ("NH", 42.7, 45.3, -72.6, -70.6),
    "new-jersey": ("NJ", 38.9, 41.4, -75.6, -73.9), "new-mexico": ("NM", 31.3, 37.0, -109.1, -103.0),
    "new-york": ("NY", 40.5, 45.0, -79.8, -71.9), "north-carolina": ("NC", 33.8, 36.6, -84.3, -75.5),
    "north-dakota": ("ND", 45.9, 49.0, -104.1, -96.6), "ohio": ("OH", 38.4, 42.0, -84.8, -80.5),
    "oklahoma": ("OK", 33.6, 37.0, -103.0, -94.4), "oregon": ("OR", 42.0, 46.3, -124.6, -116.5),
    "pennsylvania": ("PA", 39.7, 42.3, -80.5, -74.7), "rhode-island": ("RI", 41.1, 42.0, -71.9, -71.1),
    "south-carolina": ("SC", 32.0, 35.2, -83.4, -78.5), "south-dakota": ("SD", 42.5, 45.9, -104.1, -96.4),
    "tennessee": ("TN", 35.0, 36.7, -90.3, -81.6), "texas": ("TX", 25.8, 36.5, -106.7, -93.5),
    "utah": ("UT", 37.0, 42.0, -114.1, -109.0), "vermont": ("VT", 42.7, 45.0, -73.4, -71.5),
    "virginia": ("VA", 36.5, 39.5, -83.7, -75.2), "washington": ("WA", 45.5, 49.0, -124.8, -116.9),
    "west-virginia": ("WV", 37.2, 40.6, -82.6, -77.7), "wisconsin": ("WI", 42.5, 47.1, -92.9, -86.8),
    "wyoming": ("WY", 41.0, 45.0, -111.1, -104.1),
}
_STATE_RE = re.compile(r"-(" + "|".join(sorted(STATES, key=len, reverse=True)) + r")(?=-)")
_NOT_DOG = {
    "kitten", "cat", "domestic", "siamese", "tabby", "persian", "ragdoll", "bengal", "rabbit", "bunny",
    "horse", "pony", "hamster", "ferret", "bird", "parakeet", "chicken", "pig", "goat", "guinea",
    "rat", "mouse", "turtle", "snake", "lizard", "hedgehog", "sheep", "donkey", "chinchilla", "gerbil",
}
_PET_URL = re.compile(r"/pet/(\d+)-(.+)$")


def parse_slug(url):
    """'.../pet/123-federal-way-washington-husky-mix' -> (id, 'federal way', 'WA', breed_slug, url) or None."""
    m = _PET_URL.search(url)
    if not m:
        return None
    pid, rest = m.groups()
    sm = None
    for cand in _STATE_RE.finditer("-" + rest + "-"):
        if cand.start() > 0:  # need a non-empty city before the state
            sm = cand
            break
    if not sm:
        return None
    padded = "-" + rest + "-"
    city = padded[1:sm.start()].replace("-", " ")
    tail = padded[sm.end() + 1:-1]
    if not city or (set(tail.split("-")) & _NOT_DOG):
        return None
    return pid, city, STATES[sm.group(1)][0], tail, url


def candidate_states(lat, lon, radius):
    dlat = radius / 69.0
    dlon = radius / (69.0 * max(math.cos(math.radians(lat)), 0.2))
    return {v[0] for v in STATES.values()
            if v[1] - dlat <= lat <= v[2] + dlat and v[3] - dlon <= lon <= v[4] + dlon}


def is_challenge(r):
    """AWS WAF answers rate-limited clients with HTTP 202 (empty) and x-amzn-waf-action: challenge."""
    return r.status_code in (202, 403, 429) or bool(r.headers.get("x-amzn-waf-action"))


def parse_detail(html):
    """Extract the PetDetails object from Next.js flight data. Returns dict or None."""
    chunks = []
    for m in re.finditer(r'self\.__next_f\.push\(\[1,(".*?")\]\)</script>', html, re.S):
        try:
            chunks.append(json.loads(m.group(1)))
        except ValueError:
            pass
    blob = "".join(chunks)
    i = blob.find('"pet":{"__typename":"PetDetails"')
    if i < 0:
        return None
    pet, _ = json.JSONDecoder().raw_decode(blob[i + 6:])

    def resolve(v):  # "$39" -> text row "39:T<len>,<text>"
        if isinstance(v, str) and re.fullmatch(r"\$[0-9a-f]+", v):
            m = re.search(r"(?:^|\n)" + re.escape(v[1:]) + r":T[0-9a-f]+,(.*?)(?=\n[0-9a-f]+:|\Z)", blob, re.S)
            return m.group(1) if m else ""
        return v

    pet["petStory"] = resolve(pet.get("petStory"))
    return pet


def listing_from_detail(pet, slug_breed=""):
    if pet.get("petSpeciesId") != 1 or pet.get("petState") != "available":
        return None
    attrs = {a["label"]: a["content"] for a in pet.get("petAttributes", [])}
    age_text = attrs.get("Age", "")
    months = normalize.parse_age_months(age_text)
    group = normalize.age_group_from_label(pet.get("petAge")) or normalize.age_group_from_months(months)
    if months is None and age_text:
        age_text = age_text.split(",")[0]
    breeds = [b["name"] for b in pet.get("breedNamesWithSlug", [])]
    addr = pet.get("awoAddress") or {}
    story = normalize.strip_html(pet.get("petStory"))
    photos = [
        f"https://media.adoptapet.com/image/upload/d_Fallback-Photo_Dog-v3.png/c_fit,h_600,dpr_1/f_auto,q_auto/{p['sourcePhotoId']}"
        for p in sorted(pet.get("petPhotos", []), key=lambda p: p.get("order", 0))
    ] or ([pet["petThumbnailUrl"]] if pet.get("petThumbnailUrl") else [])
    posted = normalize.to_epoch((pet.get("postedTimestamp") or "").replace(" ", "T") + "+00:00") \
        if pet.get("postedTimestamp") else None
    return {
        "source": "adoptapet",
        "source_id": str(pet["petId"]),
        "url": (pet.get("petSocialShareData") or {}).get("sharedUrl") or f"{SITE}/pet/{pet['petId']}-{pet.get('slug', '')}",
        "name": pet.get("petName"),
        "breed_primary": breeds[0] if breeds else pet.get("petBreed"),
        "breed_secondary": breeds[1] if len(breeds) > 1 else None,
        "mixed": 1 if (slug_breed.endswith("mix") or len(breeds) > 1 or pet.get("isPureBred") is False) else 0,
        "color": attrs.get("Color"),
        "age_group": group,
        "age_text": age_text,
        "age_months": months,
        "sex": (attrs.get("Sex") or "").lower() or None,
        "size": normalize.norm_size(attrs.get("Size")),
        "description": story,
        "photos": photos,
        "city": addr.get("city"),
        "state": addr.get("state"),
        "zip": (addr.get("postalCode") or "")[:5] or None,
        "lat": None,
        "lon": None,
        "org": pet.get("awoName"),
        "transport": normalize.detect_transport(story, pet.get("awoAdoptionProcess"), pet.get("awoName")),
        "listed_at": posted,
    }


class AdoptAPet(Source):
    name = "adoptapet"

    def __init__(self):
        self._index_marks = {}   # sitemap url -> lastmod we last parsed
        self._entries = {}       # sitemap url -> [(id, city, ST, tail, url)]
        self._cooldown_until = 0.0

    # -- sitemap ---------------------------------------------------------
    def _client(self):
        return httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": config.USER_AGENT})

    def _refresh_sitemaps(self, client):
        idx = ET.fromstring(client.get(SITEMAP_INDEX).raise_for_status().content)
        ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        for sm in idx.findall("s:sitemap", ns):
            loc = sm.findtext("s:loc", namespaces=ns) or ""
            mark = sm.findtext("s:lastmod", namespaces=ns)
            if "/pets_" not in loc or self._index_marks.get(loc) == mark:
                continue
            raw = client.get(loc).raise_for_status().content
            xml = gzip.decompress(raw) if loc.endswith(".gz") else raw
            root = ET.fromstring(xml)
            entries = []
            for u in root.iter("{http://www.sitemaps.org/schemas/sitemap/0.9}loc"):
                p = parse_slug(u.text or "")
                if p:
                    entries.append(p)
            self._entries[loc] = entries
            self._index_marks[loc] = mark
            log.info("sitemap %s: %d dog-ish entries", loc.rsplit("/", 1)[-1], len(entries))

    # -- main ------------------------------------------------------------
    def fetch_for_cleanup(self, region, db=None):
        """Cleanup check: refresh the dogs we already know that are still in the sitemap, without fetching new pages."""
        return self.fetch(region, db, discover=False)

    def _verify(self, client, db, known):
        """The sitemap keeps listing pets long after they are adopted or taken down, so a dog still being in it proves
        nothing. Open the dog's own page: a state other than "available" (or a 404) means it is gone for good.
        Opens at most VERIFY_PER_CLEANUP pages per cleanup, never-checked dogs first and then the longest ago checked
        (closest first within each), so repeated cleanups work through everything. Returns refreshed listings."""
        try:
            done = json.loads(db.get_setting(VERIFIED_KEY) or "{}")
        except ValueError:
            done = {}
        order = sorted(known, key=lambda c: done.get(c[1], 0))      # stable: ties stay closest-first
        out, gone, checked = [], [], 0
        for _, pid, tail, url in order[:VERIFY_PER_CLEANUP]:
            time.sleep(DETAIL_DELAY)
            try:
                r = client.get(url)
            except httpx.HTTPError as e:
                log.warning("adoptapet verify %s: %s", pid, e)
                continue
            if is_challenge(r):
                self._cooldown_until = time.time() + COOLDOWN
                log.warning("adoptapet: rate-limited while verifying after %d pages; stopping", checked)
                break
            checked += 1
            if r.status_code == 404:
                gone.append(pid)
                continue
            if r.status_code != 200:
                continue
            pet = parse_detail(r.text)
            if pet is None:                                       # unreadable page: keep the dog, check again later
                continue
            done[pid] = time.time()
            lst = listing_from_detail(pet, tail)
            if not lst:
                gone.append(pid)
                continue
            g = geo.geocode(db, lst["zip"]) if lst["zip"] else None
            if g:
                lst["lat"], lst["lon"] = g["lat"], g["lon"]
            out.append(lst)
        self.retired = getattr(self, "retired", 0) + len(set(gone))
        db.retire(self.name, gone)                               # not "gone": a later sitemap touch must not bring it back
        for pid in gone:
            done.pop(pid, None)
        if gone:
            log.info("adoptapet: %d dogs verified as no longer available", len(gone))
        live = {c[1] for c in known}
        db.set_setting(VERIFIED_KEY, json.dumps({k: v for k, v in done.items() if k in live}))
        return out

    def fetch(self, region, db=None, discover=True):
        if time.time() < self._cooldown_until:
            mins = int((self._cooldown_until - time.time()) / 60) + 1
            raise RuntimeError(f"Adopt-a-Pet asked us to slow down; pausing {mins} more min")
        found = []
        with self._client() as client:
            self._refresh_sitemaps(client)
            states = ({region["state"]} if region.get("statewide")
                      else candidate_states(region["lat"], region["lon"], region["radius"]))
            city_dist = {}   # (city, ST) -> miles from the search centre, or None if outside/unknown

            def distance(city, st):
                if region.get("statewide"):
                    return 0.0 if st == region["state"] else None
                key = (city, st)
                if key not in city_dist:
                    q = f"{city}, {st}"
                    cached = db.geo_get(q.lower())
                    g = geo.geocode(db, q)
                    if cached is None:
                        time.sleep(0.15)  # be nice to the geocoder on real lookups
                    ok = bool(g) and geo.in_region(region, g)
                    city_dist[key] = geo.haversine(region["lat"], region["lon"], g["lat"], g["lon"]) if ok else None
                return city_dist[key]

            candidates = []
            for entries in self._entries.values():
                for pid, city, st, tail, url in entries:
                    if st in states:
                        d = distance(city, st)
                        if d is not None:
                            candidates.append((d, pid, tail, url))
            candidates.sort()  # closest dogs first, so the first cycles cover your area
            log.info("adoptapet: %d nearby candidates", len(candidates))

            known = db.known_ids(self.name)
            db.touch(self.name, [c[1] for c in candidates if c[1] in known])
            if not discover:
                held = {r["source_id"] for r in db.q("SELECT source_id FROM listings WHERE source=? AND status='active'", (self.name,))}
                found.extend(self._verify(client, db, [c for c in candidates if c[1] in held]))
            fresh = [c for c in candidates if c[1] not in known] if discover else []
            errors = 0
            for n, (_, pid, tail, url) in enumerate(fresh):
                if n >= MAX_DETAILS_PER_CYCLE:
                    log.info("adoptapet: %d more details deferred to next cycle", len(fresh) - n)
                    break
                time.sleep(DETAIL_DELAY)
                try:
                    r = client.get(url)
                except httpx.HTTPError as e:
                    errors += 1
                    log.warning("adoptapet %s: %s", pid, e)
                    if errors >= 5 and not found:
                        raise
                    continue
                if is_challenge(r):
                    # Their firewall wants us to slow down. Stop, remember, and never treat this as "not a dog".
                    self._cooldown_until = time.time() + COOLDOWN
                    log.warning("adoptapet: rate-limited after %d pages (HTTP %s); backing off %d min",
                                n, r.status_code, COOLDOWN // 60)
                    if not found:
                        raise RuntimeError(f"rate limited by Adopt-a-Pet's firewall; retrying in {COOLDOWN // 60} min")
                    break
                if r.status_code == 404:
                    db.skip(self.name, pid)
                    continue
                if r.status_code != 200:
                    errors += 1
                    log.warning("adoptapet %s: HTTP %s", pid, r.status_code)
                    if errors >= 5 and not found:
                        raise RuntimeError(f"Adopt-a-Pet returned HTTP {r.status_code}")
                    continue
                pet = parse_detail(r.text)
                if pet is None:   # page layout changed or partial page: don't blacklist the dog
                    errors += 1
                    log.warning("adoptapet %s: could not parse page", pid)
                    if errors >= 5 and not found:
                        raise RuntimeError("could not parse Adopt-a-Pet pages; has the site changed?")
                    continue
                lst = listing_from_detail(pet, tail)
                if not lst:
                    db.skip(self.name, pid)   # genuinely not an available dog (cat, adopted...)
                    continue
                g = geo.geocode(db, lst["zip"]) if lst["zip"] else None
                if g:
                    lst["lat"], lst["lon"] = g["lat"], g["lon"]
                found.append(lst)
        return found
