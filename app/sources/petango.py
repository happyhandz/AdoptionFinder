"""Petango adoptable-pet widgets.

Many shelters embed Petango's public "adoptable animals" widget on their own site. The widget is a plain page at
ws.petango.com whose address (with a public access key) is right in the shelter's own page, and it lists every
adoptable animal with name, photo, breed, age and sex. One parser serves them all: add a line to ORGS.

Find a shelter's widget address: open its adoption page, view the source, and look for
`https://ws.petango.com/webservices/adoptablesearch/wsAdoptableAnimals.aspx?...authkey=...` (or ...Animals2.aspx).
"""
import re
import time
from urllib.parse import parse_qs, urlparse

import httpx
from bs4 import BeautifulSoup

from .. import config, geo
from . import webscrape
from .base import Source

NAME = "petango"
BASE = "https://ws.petango.com/webservices/adoptablesearch/"
DELAY = 1.0

# (organization, widget address, ZIP for distance, extra widget words)
ORGS = [
    ("Greenhill Humane Society (Eugene)",
     BASE + "wsAdoptableAnimals.aspx?species=dog&sex=A&agegroup=All&onhold=A&orderby=Name&colnum=2&detailsInPopup=Yes"
            "&authkey=x092s378c561r2ixd72d6lr55q5q41zjqv07054h69t8pcw5l4", "97402"),
    ("Wenatchee Valley Humane Society",
     BASE + "wsAdoptableAnimals2.aspx?species=Dog&gender=A&agegroup=All&location=&site=&onhold=A&orderby=ID&colnum=5&css="
            "&authkey=1e776dp1at34dt3br553ykgeln5fjemy53lcjtoeo6fpdiwqmm", "98801"),
    ("Better Together Animal Alliance (Ponderay)",
     BASE + "wsAdoptableAnimals2.aspx?species=All&gender=A&agegroup=All&location=&site=&onhold=A&orderby=ID&colnum=5&css="
            "&authkey=68fsa3xkwvajqxht174y27xpvt0sgdavc46n72w0d7hbtskhyd", "83852"),
]

_COAT = re.compile(r",?\s*(?:short|medium|long|wire|smooth|rough|curly|double|hairless)\s*(?:coat|hair)\b", re.I)
_PAREN = re.compile(r"\([^)]*\)")


def detail_url(widget_url, animal_id):
    """The shelter widget's own page for one animal (public)."""
    q = parse_qs(urlparse(widget_url).query)
    page = "wsAdoptableAnimalDetails2.aspx" if "Animals2" in widget_url else "wsAdoptableAnimalDetails.aspx"
    return f"{BASE}{page}?id={animal_id}&css=&authkey={q.get('authkey', [''])[0]}"


def _text(item, cls):
    el = item.select_one(f".{cls}")
    return el.get_text(" ", strip=True) if el else ""


def parse(html, org, zip_, widget_url):
    """Widget HTML -> normalized dog listings. Cats and other species are dropped."""
    out = []
    site = {"name": NAME, "org": org, "location": {"zip": zip_}, "breed_split": False, "fields": {}}
    for item in BeautifulSoup(html, "lxml").select(".list-item"):
        aid = _text(item, "list-animal-id")
        if not aid or _text(item, "list-animal-species").lower() != "dog":
            continue
        breed = _text(item, "list-animal-breed")
        primary, _, secondary = breed.partition("/")
        size_text = primary                       # "Mixed Breed, Large (over 44 lbs fully grown)": size rides along in the breed
        primary = _COAT.sub("", _PAREN.sub("", primary))
        primary = re.sub(r",?\s*(?:x-?large|extra large|large|medium|small|giant)\s*$", "", primary.strip(), flags=re.I).strip(" ,")
        img = item.select_one("img.list-animal-photo")
        place = item.select_one(".hidden")
        raw = {
            "id": aid,
            "name": _text(item, "list-animal-name").strip('"'),
            "url": detail_url(widget_url, aid),
            "photo": [img["src"]] if img and img.get("src") else [],
            "breed": primary or "Mixed Breed",
            "breed2": secondary.strip() or None,
            "age": _text(item, "list-animal-age"),
            "sex": _text(item, "list-animal-sexSN"),
            "size": size_text,
            "description": f"Location: {place.get_text(strip=True)}" if place and place.get_text(strip=True) else None,
            "species": "dog",
        }
        lst = webscrape.to_listing(site, raw, widget_url)
        if lst:
            out.append(lst)
    return out


def _get(url):
    headers = {"User-Agent": config.USER_AGENT}
    r = httpx.get(url, timeout=30, follow_redirects=True, headers=headers)
    if r.status_code in (403, 429):
        from curl_cffi import requests as cffi
        r = cffi.get(url, impersonate="chrome", timeout=30)
    r.raise_for_status()
    return r.text


class Petango(Source):
    name = NAME

    def fetch(self, region, db=None):
        out, failures = [], 0
        for org, url, zip_ in ORGS:
            g = geo.geocode(db, zip_) if db else None
            if not g or not geo.in_region(region, g):
                continue
            time.sleep(DELAY)
            try:
                dogs = parse(_get(url), org, zip_, url)
            except Exception:
                failures += 1
                if failures >= len(ORGS) and not out:
                    raise
                continue
            for d in dogs:
                d["lat"], d["lon"] = g["lat"], g["lon"]
                d["city"] = d["city"] or g["label"].split(",")[0]
                d["state"] = geo.state_of(g) or d["state"]
            out.extend(dogs)
        return out
