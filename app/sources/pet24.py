"""24Pet Connect shelters (the same PetPoint family as Petango).

A shelter's public page is `https://24petconnect.com/<code>?at=DOG`. It lists 30 animals at a time with name, photo,
breed, age, sex and the date the animal arrived; `&index=N` starts the list N animals in (an offset: 0, 30, 60...). Add a shelter to ORGS with its code
(visible in the link on its own adoption page).
"""
import re
import time
from datetime import datetime, timezone

import httpx
from bs4 import BeautifulSoup

from .. import config, geo, normalize
from . import webscrape
from .base import Source

NAME = "24petconnect"
DELAY = 1.5
PER_PAGE = 30
MAX_PAGES = 20

# (organization, 24Pet Connect code, ZIP for distance)
ORGS = [
    ("Tri-Cities Animal Shelter (Pasco)", "PP6628", "99301"),
]


def _line(res, name):
    el = res.select_one(f".line_{name} .text_{name}")
    return el.get_text(" ", strip=True) if el else ""


def _arrived(text):
    m = re.match(r"(\d{4})[./-](\d{2})[./-](\d{2})", text or "")
    if not m:
        return None
    return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc).timestamp()


def total_animals(html):
    m = re.search(r"Animals:\s*\d+\s*-\s*\d+\s*of\s*(\d+)", html)
    return int(m.group(1)) if m else None


def parse(html, org, zip_, code):
    """One results page -> normalized dog listings."""
    out = []
    site = {"name": NAME, "org": org, "location": {"zip": zip_}, "breed_split": False, "fields": {}}
    page_url = f"https://24petconnect.com/{code}?at=DOG"
    for res in BeautifulSoup(html, "lxml").select("div.gridResult"):
        m = re.match(r"Result_(\d+)", res.get("id") or "")
        if not m or _line(res, "Animaltype").lower() not in ("dog", ""):
            continue
        name = re.sub(r"\s*\(\d+\)\s*$", "", _line(res, "Name"))
        img = res.find("img")
        breed = _line(res, "Breed")
        raw = {"id": m.group(1), "name": name, "url": page_url, "photo": [img["src"]] if img and img.get("src") else [],
               "breed": breed, "age": _line(res, "Age"), "sex": _line(res, "Gender"), "species": "dog",
               "org": _line(res, "Locatedat") or org}
        lst = webscrape.to_listing(site, raw, page_url)
        if lst:
            lst["listed_at"] = _arrived(_line(res, "Broughttotheshelter"))
            out.append(lst)
    return out


class Pet24(Source):
    name = NAME

    def fetch(self, region, db=None):
        out, failures = [], 0
        headers = {"User-Agent": config.USER_AGENT}
        for org, code, zip_ in ORGS:
            g = geo.geocode(db, zip_) if db else None
            if not g or not geo.in_region(region, g):
                continue
            found, seen = [], set()
            try:
                for page in range(1, MAX_PAGES + 1):
                    time.sleep(DELAY)
                    r = httpx.get(f"https://24petconnect.com/{code}", params={"at": "DOG", "index": (page - 1) * PER_PAGE},
                                  timeout=30, follow_redirects=True, headers=headers)
                    r.raise_for_status()
                    dogs = parse(r.text, org, zip_, code)
                    fresh = [d for d in dogs if d["source_id"] not in seen]
                    if not fresh:
                        break
                    seen.update(d["source_id"] for d in fresh)
                    found.extend(fresh)
                    total = total_animals(r.text)
                    if (total is not None and page * PER_PAGE >= total) or len(dogs) < PER_PAGE:
                        break
            except Exception:
                failures += 1
                if failures >= len(ORGS) and not out:
                    raise
                continue
            for d in found:
                d["lat"], d["lon"] = g["lat"], g["lon"]
                d["city"] = d["city"] or g["label"].split(",")[0]
                d["state"] = geo.state_of(g) or d["state"]
            out.extend(found)
        return out
