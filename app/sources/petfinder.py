"""Petfinder: manual import, not polling.

petfinder.com refuses scripted access (HTTP 403 / "temporarily unavailable", even with a browser TLS
fingerprint) and its API was retired in Dec 2025. We don't try to defeat that. Instead the person
browses Petfinder normally in their own browser, where it works, and sends the page they are looking
at to this app (a bookmarklet, or by uploading a saved copy). This module turns that page's HTML into
listings. Nothing here makes a request to petfinder.com.

parse_page() tries, in order:
  1. embedded JSON that looks like Petfinder animal objects (name + breeds + photos...)
  2. the page's dog cards: anchors to /dog/<name>-<id>/..., with the text and image around them
Imported dogs stay active for EXPIRE_DAYS after they were last seen in an import.
"""
import json
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .. import normalize
from . import webscrape
from .base import Source

SOURCE = "petfinder"
EXPIRE_DAYS = 14
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_DOG_HREF = re.compile(r"/dog/[^/?#]*?-(" + _UUID + r"|\d{5,})(?:/|$|\?)")
_SEP = r"[\u2022\u00b7\ufffd|]"  # bullet between "Puppy" and "Male" (arrives as U+FFFD if the charset is mangled)
_INFO_LINE = re.compile(r"^(Baby|Puppy|Young|Adult|Senior)\s*" + _SEP + r"?\s*(Male|Female|Unknown)?\s*$", re.I)
_LOCATION = re.compile(r"^[A-Z][A-Za-z.'\- ]+,\s*[A-Z]{2}$")
_AGE_WORDS = re.compile(r"\b(baby|puppy|young|adult|senior)\b", re.I)
_SEX_WORDS = re.compile(r"\b(male|female)\b", re.I)
_SIZE_WORDS = re.compile(r"\b(small|medium|large|extra large|x-?large)\b", re.I)
_NOISE = re.compile(r"miles? away|^\d+\s*(mi|miles)$|^(new|featured|favorite|save|share|adopt|view|see more)\b|"
                    r"^(more|details|learn more)$", re.I)
_SITE = {"name": SOURCE, "org": "Petfinder listing", "fields": {}}


class Petfinder(Source):
    name = SOURCE
    manual = True  # never polled; fed by imports

    def enabled(self):
        return True

    def fetch(self, region, db=None):  # pragma: no cover - the poller skips manual sources
        return []


# ---- 1. embedded JSON -----------------------------------------------------------------

def _json_blobs(html):
    for m in re.finditer(r"<script[^>]*type=\"application/(?:ld\+)?json\"[^>]*>(.*?)</script>", html, re.S | re.I):
        try:
            yield json.loads(m.group(1))
        except ValueError:
            pass
    for m in re.finditer(r"(?:__INITIAL_STATE__|__APOLLO_STATE__|__NEXT_DATA__|__PRELOADED_STATE__)\s*=\s*", html):
        try:
            obj, _ = json.JSONDecoder().raw_decode(html[m.end():])
            yield obj
        except ValueError:
            pass


def _walk(o):
    stack = [o]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            yield cur
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)


def _animal_from_json(d, page_url):
    """Map a Petfinder-API-v2-shaped object to raw fields for webscrape.to_listing, else None."""
    if not isinstance(d, dict) or not d.get("name") or not (d.get("breeds") or d.get("breed")):
        return None
    if str(d.get("species", "dog")).lower() not in ("dog", "dogs"):
        return None
    breeds = d.get("breeds") or {}
    if isinstance(breeds, dict):
        breed = " / ".join(b for b in (breeds.get("primary"), breeds.get("secondary")) if b)
        if breeds.get("mixed") and breed and "mix" not in breed.lower():
            breed += " Mix"
    else:
        breed = str(d.get("breed") or breeds)
    photos = []
    for p in d.get("photos") or []:
        photos.append(p.get("large") or p.get("medium") or p.get("full") if isinstance(p, dict) else p)
    if not photos and isinstance(d.get("primary_photo_cropped"), dict):
        photos = [d["primary_photo_cropped"].get("large") or d["primary_photo_cropped"].get("full")]
    addr = (d.get("contact") or {}).get("address") or {}
    colors = d.get("colors") or {}
    return {
        "id": str(d.get("id")) if d.get("id") else None,
        "name": str(d["name"]),
        "url": d.get("url"),
        "photo": [p for p in photos if p],
        "breed": breed,
        "age": d.get("age"),
        "sex": d.get("gender") or d.get("sex"),
        "size": d.get("size"),
        "color": colors.get("primary") if isinstance(colors, dict) else None,
        "description": d.get("description"),
        "city": addr.get("city"), "state": addr.get("state"), "zip": addr.get("postcode"),
        "species": d.get("species") or "dog",
        "listed": d.get("published_at"),
        "org": None,
    }


def _from_json(html, page_url):
    out = {}
    for blob in _json_blobs(html):
        for node in _walk(blob):
            raw = _animal_from_json(node, page_url)
            if raw:
                out[raw["id"] or raw["name"]] = raw
    return list(out.values())


# ---- 2a. Petfinder's own result cards (PFPetDetailCard) ----------------------------------

_FACT_KEYS = {"shelter": "org", "size": "size", "color": "color", "colour": "color", "coat length": "coat",
              "good with": "good_with", "house-trained?": "house_trained", "house-trained": "house_trained",
              "age": "age_fact", "gender": "sex_fact", "breed": "breed_fact"}


def _petfinder_cards(html, page_url):
    soup = BeautifulSoup(html, "lxml")
    boxes = soup.select('div[class*="PFPetDetailCard_flipWrapper"]')
    out, seen = [], set()
    for box in boxes:
        a = box.find("a", href=_DOG_HREF)
        if not a:
            continue
        pid = _DOG_HREF.search(a["href"]).group(1)
        if pid in seen:
            continue
        seen.add(pid)
        lines = [re.sub(r"\s+", " ", t).strip() for t in box.stripped_strings]
        lines = [t for t in lines if t]
        img = box.find("img")
        name_el = box.find(class_=re.compile(r"tw-font-extrabold"))
        name = name_el.get_text(" ", strip=True) if name_el else ""
        if not name and img and img.get("alt"):
            name = img["alt"].split(", ")[0]
        if not name and a.get("aria-label"):
            name = re.sub(r"^View (.*?)'s profile$", r"\1", a["aria-label"])
        age = sex = breed = None
        for i, line in enumerate(lines):
            m = _INFO_LINE.match(line)
            if m:
                age, sex = m.group(1), m.group(2)
                nxt = lines[i + 1] if i + 1 < len(lines) else ""
                breed = nxt if nxt and not re.match(r"^(view profile|fast facts)", nxt, re.I) else None
                break
        facts = {}
        for i, line in enumerate(lines[:-1]):
            key = _FACT_KEYS.get(line.lower())
            if key and key not in facts and not _FACT_KEYS.get(lines[i + 1].lower()):
                facts[key] = lines[i + 1]
        href = urljoin(page_url, a["href"])
        m = re.search(r"/dog/[^/]+/([a-z]{2})/([^/]+)/([^/?#]+)", href)
        state, city, org_slug = (m.group(1).upper(), m.group(2).replace("-", " ").title(), m.group(3)) if m else (None, None, "")
        notes = []
        if facts.get("good_with"):
            notes.append(f"Good with: {facts['good_with']}.")
        if facts.get("house_trained"):
            notes.append(f"House-trained: {facts['house_trained']}.")
        out.append({
            "id": pid, "name": name, "url": href,
            "photo": [_img_url(img, page_url)] if img and _img_url(img, page_url) else [],
            "breed": breed or facts.get("breed_fact") or "", "age": age or facts.get("age_fact"),
            "sex": sex or facts.get("sex_fact"), "size": facts.get("size"), "color": facts.get("color"),
            "description": " ".join(notes) or None,
            "city": city, "state": state, "zip": None, "species": "dog", "listed": None,
            "org": facts.get("org") or re.sub(r"-[a-z]{2}\d+$", "", org_slug).replace("-", " ").title() or None,
        })
    return [o for o in out if o["name"]]


# ---- 2. dog cards ---------------------------------------------------------------------

def _card_container(a):
    """Climb from the dog link to the smallest ancestor that holds the card's image and text."""
    node = a
    for _ in range(6):
        if node.find("img") and len(list(node.stripped_strings)) >= 3:
            return node
        node = node.parent
        if node is None:
            break
    return a.parent or a


def _img_url(img, base):
    for attr in ("src", "data-src", "data-lazy-src"):
        v = img.get(attr)
        if v and not v.startswith("data:"):
            return urljoin(base, v)
    ss = img.get("srcset") or img.get("data-srcset")
    return urljoin(base, ss.split(",")[0].split()[0]) if ss else None


def _from_cards(html, page_url):
    soup = BeautifulSoup(html, "lxml")
    seen, out = set(), []
    for a in soup.find_all("a", href=_DOG_HREF):
        pid = _DOG_HREF.search(a["href"]).group(1)
        if pid in seen:
            continue
        seen.add(pid)
        box = _card_container(a)
        lines = [s for s in box.stripped_strings if not _NOISE.search(s) and len(s) < 120]
        heading = box.find(re.compile(r"^h[1-6]$"))
        name = (heading.get_text(" ", strip=True) if heading else (lines[0] if lines else "")) or a.get("title") or ""
        name = re.sub(r"\s+", " ", name).strip()
        info = next((l for l in lines if _AGE_WORDS.search(l) or _SEX_WORDS.search(l)), "")
        location = next((l for l in lines if _LOCATION.match(l)), "")
        rest = [l for l in lines if l not in (name, info, location)]
        # the breed line sits between the name and the age/sex line on Petfinder cards
        breed = ""
        if info and info in lines:
            before = [l for l in lines[:lines.index(info)] if l != name and l != location]
            breed = before[-1] if before else ""
        if not breed and rest:
            breed = rest[0]
        city, state = ("", "")
        if location:
            city, state = [x.strip() for x in location.rsplit(",", 1)]
        img = box.find("img")
        age = (_AGE_WORDS.search(info) or [None])[0] if info else None
        sex = (_SEX_WORDS.search(info) or [None])[0] if info else None
        size = (_SIZE_WORDS.search(info) or [None])[0] if info else None
        href = urljoin(page_url, a["href"])
        m = re.search(r"/dog/[^/]+-\d+/([a-z]{2})/([^/]+)/([^/?#]+)", href)
        if m and not state:
            state, city = m.group(1).upper(), m.group(2).replace("-", " ").title()
        out.append({
            "id": pid, "name": name, "url": href, "photo": [_img_url(img, page_url)] if img and _img_url(img, page_url) else [],
            "breed": breed, "age": age, "sex": sex, "size": size, "color": None, "description": None,
            "city": city or None, "state": state or None, "zip": None, "species": "dog", "listed": None,
            "org": (m.group(3).rsplit("-", 1)[0].replace("-", " ").title() if m else None),
        })
    return [o for o in out if o["name"]]


# ---- public ---------------------------------------------------------------------------

def parse_page(html, page_url="https://www.petfinder.com/"):
    """HTML of a Petfinder search/results page -> list of normalized listing dicts (source='petfinder')."""
    raws = _from_json(html, page_url) or _petfinder_cards(html, page_url) or _from_cards(html, page_url)
    listings = []
    for raw in raws:
        lst = webscrape.to_listing(_SITE, raw, page_url)
        if not lst:
            continue
        if raw.get("org"):
            lst["org"] = raw["org"]
        lst["source"] = SOURCE
        lst["transport"] = lst["transport"] or normalize.detect_transport(lst.get("description"))
        listings.append(lst)
    return listings


def import_page(db, html, page_url="https://www.petfinder.com/"):
    """Parse and store. Returns (found, new)."""
    listings = parse_page(html, page_url)
    new = import_listings(db, listings)
    db.set_status(SOURCE, True, count=len(listings))
    return len(listings), new


def import_listings(db, listings):
    """Store parsed Petfinder listings (locating each by city). Returns how many were new to the app."""
    from .. import geo
    new = 0
    for lst in listings:
        if lst.get("zip"):
            g = geo.geocode(db, lst["zip"])
        elif lst.get("city") and lst.get("state"):
            g = geo.geocode(db, f"{lst['city']}, {lst['state']}")
        else:
            g = None
        if g:
            lst["lat"], lst["lon"] = g["lat"], g["lon"]
            lst["zip"] = lst["zip"] or g.get("zip")
        _, is_new = db.upsert_listing(lst)
        new += is_new
    return new
