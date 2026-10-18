"""Oregon Humane Society (Portland and Salem campuses), the largest shelter in the Northwest.

oregonhumane.org/adopt/ lists every adoptable pet in the page itself: one `pet-card` per animal carrying its data
(species, age in months, sex, weight, campus, breed codes) as attributes. Breeds come as numeric codes that match the
page's own breed filter list, so the names are looked up there. Codes the filter list doesn't contain (they appear on
puppies and large mixes) are shown as "Mixed Breed".
"""
import re

import httpx
from bs4 import BeautifulSoup

from .. import config, geo
from . import webscrape
from .base import Source

NAME = "oregonhumane"
URL = "https://www.oregonhumane.org/adopt/?type=dogs"
CAMPUSES = {"Portland": ("97211", "Oregon Humane Society (Portland)"), "Salem": ("97301", "Oregon Humane Society (Salem)")}


def size_from_weight(lbs):
    """Same bands Shelterluv uses: small up to 25 lb, medium 25-59, large 60+."""
    try:
        w = float(lbs)
    except (TypeError, ValueError):
        return None
    return "small" if w < 25 else "medium" if w < 60 else "large"


def _age_text(card):
    """"9w", "1y 6m": the card's visible age. (data-ohssb-age is whole months and rounds a 9-week puppy down to 0.)"""
    li = card.select_one(".pet-card__details li")
    text = li.get_text(strip=True) if li else ""
    if re.match(r"^\d+\s*[ymw]", text):
        return text
    months = card.get("data-ohssb-age") or ""
    return f"{months} months" if months.isdigit() else ""


def parse(html):
    """Page HTML -> normalized dog listings (no coordinates yet)."""
    soup = BeautifulSoup(html, "lxml")
    breeds = {o["value"]: o.get_text(strip=True) for o in soup.find_all("option") if (o.get("value") or "").isdigit()}
    out = []
    for card in soup.select("div.pet-card"):
        if card.get("data-ohssb-type") != "dog":
            continue
        zip_, org = CAMPUSES.get(card.get("data-ohssb-shelter"), CAMPUSES["Portland"])
        ids = [i for i in (card.get("data-ohssb-breed") or "").split("|") if i]
        names = [breeds.get(i) for i in ids]
        known = [n for n in names if n]
        mix = "Mix" in known or any(n is None for n in names) or len(known) > 1
        known = [n for n in known if n != "Mix"]
        img = card.find("img")
        name_el = card.select_one(".pet-card__name")
        site = {"name": NAME, "org": org, "location": {"zip": zip_}, "state": "OR", "breed_split": False, "fields": {}}
        raw = {
            "id": card.get("data-ohssb-id"),
            "name": name_el.get_text(strip=True) if name_el else (card.get("data-ohssb-name") or "").title(),
            "url": card.get("data-url"),
            "photo": [img["src"]] if img and img.get("src") and "placeholder" not in img["src"] else [],
            "breed": (known[0] if known else "Mixed Breed"),
            "breed2": known[1] if len(known) > 1 else None,
            "age": _age_text(card),
            "sex": card.get("data-ohssb-sex"),
            "size": size_from_weight(card.get("data-ohssb-weight")) or "",
            "species": "dog",
        }
        lst = webscrape.to_listing(site, raw, URL)
        if lst:
            lst["mixed"] = 1 if mix else lst["mixed"]
            lst["state"] = "OR"
            out.append(lst)
    return out


class OregonHumane(Source):
    name = NAME

    def fetch(self, region, db=None):
        geos = {z: geo.geocode(db, z) for z, _ in CAMPUSES.values()} if db else {}
        if not any(g and geo.in_region(region, g) for g in geos.values()):
            return []                                          # neither campus is in the search area: don't even ask
        r = httpx.get(URL, timeout=40, follow_redirects=True, headers={"User-Agent": config.USER_AGENT})
        if r.status_code in (403, 429):
            from curl_cffi import requests as cffi
            r = cffi.get(URL, impersonate="chrome", timeout=40)
        r.raise_for_status()
        out = []
        for d in parse(r.text):
            g = geos.get(d["zip"])
            if not g or not geo.in_region(region, g):
                continue
            d["lat"], d["lon"], d["city"] = g["lat"], g["lon"], g["label"].split(",")[0]
            out.append(d)
        return out
