"""Shelterluv-hosted rescues in Washington.

Shelterluv's own embed widget loads dogs from a JSON endpoint,
  GET https://www.shelterluv.com/api/v3/available-animals/<shelter id>?species=Dog
which needs no key (the same call every rescue's website widget makes). One scraper serves every
org below; add an org by finding `var GID = <number>` in the embed snippet on its adoption page.
The API has no street address, so each org carries a city/ZIP for distance filtering.
"""
import time

import httpx

from .. import config, geo, normalize
from .base import Source

API = "https://www.shelterluv.com/api/v3/available-animals/{gid}"
DELAY = 1.0

# gid, display name, ZIP used for distance, transport flag (dogs shipped in from out of state)
ORGS = [
    (100000719, "Humane Society for Tacoma & Pierce County", "98409", False),
    (11040, "Sunny Sky's Animal Rescue", "98338", False),
    (47562, "Little Dog Rescue", "98101", False),
    (43792, "Pacific Rim Rescue", "98101", False),
    (7151, "Main Street Mutt Rescue", "98248", False),
    (38785, "Lily's Pad Rescue", "98645", False),
    (100000930, "Rise Rescue Alliance", "98362", False),
    (23081, "The Pit Bull Pen", "99320", False),
    (10842, "Spokane Humane Society", "99201", False),
    (100003342, "Whitman County Humane Society", "99163", False),
    (100001743, "Colville Valley Animal Sanctuary", "99114", False),
    (22407, "Saving Grace Dog Rescue of San Antonio", "98101", True),
    # added 2026-10-02 (Oregon, Idaho and one more in Spokane)
    (35916, "SpokAnimal (Spokane)", "99202", False),
    (42542, "Dizzy Dog Animal Rescue (Portland transport stop)", "97201", True),
    (10355, "Senior Dog Rescue of Oregon (Corvallis)", "97330", False),
    (29277, "Oregon Pet Project (Newberg)", "97132", False),
    (17653, "Mountain Humane (Hailey, ID)", "83333", False),
    (38292, "PAWS of Teton Valley (Driggs, ID)", "83422", False),
    (16072, "Lake Lowell Animal Rescue (Nampa, ID)", "83651", False),
    (13962, "Priest River Animal Rescue (ID)", "83856", False),
]


def _months(birthday):
    try:
        return round((time.time() - float(birthday)) / (30.44 * 86400), 1)
    except (TypeError, ValueError):
        return None


def _age_text(months):
    if months is None:
        return None
    if months < 1:
        return "Under 1 month"
    if months < 24:
        return f"{int(months)} month{'s' if int(months) != 1 else ''}"
    return f"{int(months // 12)} years"


def parse(rec, org, zip_, transport):
    """One Shelterluv animal record -> normalized listing dict."""
    if (rec.get("species") or "").lower() != "dog" or rec.get("adoptable") == 0:
        return None
    months = _months(rec.get("birthday"))
    raw = rec.get("photos") or {}
    raw = raw.values() if isinstance(raw, dict) else raw  # the API returns a dict for some orgs, a list for others
    photos = sorted((p for p in raw if isinstance(p, dict)), key=lambda p: (not p.get("isCover"), p.get("order_column", 0)))
    breed1, breed2 = (rec.get("breed") or "").strip(), (rec.get("secondary_breed") or "").strip()
    if breed2.lower() in ("none", "n/a", "na", "unknown", "-") or breed2.lower() == breed1.lower():
        breed2 = ""
    mixed = "mix" in breed1.lower() or bool(breed2)
    colors = " / ".join(c for c in (rec.get("primary_color"), rec.get("secondary_color")) if c)
    notes = "; ".join(rec.get("attributes") or [])
    return {
        "source": "shelterluv",
        "source_id": str(rec["uniqueId"]),
        "url": rec.get("public_url") or f"https://www.shelterluv.com/embed/animal/{rec['uniqueId']}",
        "name": (rec.get("name") or "").strip(),
        "breed_primary": breed1 or "Unknown breed",
        "breed_secondary": breed2 or None,
        "mixed": 1 if mixed else 0,
        "color": colors or None,
        "age_group": normalize.age_group_from_months(months),
        "age_text": _age_text(months),
        "age_months": months,
        "sex": (rec.get("sex") or "").lower() or None,
        "size": normalize.norm_size(rec.get("weight_group")),
        "description": notes or None,
        "photos": [p["url"] for p in photos if p.get("url")],
        "city": None,
        "state": "WA",
        "zip": zip_,
        "lat": None,
        "lon": None,
        "org": org,
        "transport": 1 if transport else normalize.detect_transport(notes),
        "listed_at": float(rec["intake_date"]) if rec.get("intake_date") else None,
    }


class Shelterluv(Source):
    name = "shelterluv"

    def fetch(self, region, db=None):
        out = []
        headers = {"User-Agent": config.USER_AGENT, "Accept": "application/json", "X-Requested-With": "XMLHttpRequest"}
        with httpx.Client(timeout=30, headers=headers) as client:
            failures = 0
            for gid, org, zip_, transport in ORGS:
                g = geo.geocode(db, zip_)
                if not g or not geo.in_region(region, g):
                    continue  # org is outside the search area, don't even request it
                time.sleep(DELAY)
                try:
                    r = client.get(API.format(gid=gid), params={"species": "Dog"})
                    r.raise_for_status()
                    animals = r.json().get("animals", [])
                except (httpx.HTTPError, ValueError) as e:
                    failures += 1
                    if failures >= 3 and not out:
                        raise
                    continue
                for rec in animals:
                    l = parse(rec, org, zip_, transport)
                    if l:
                        l["lat"], l["lon"] = g["lat"], g["lon"]
                        l["city"] = g["label"].split(",")[0]
                        l["state"] = geo.state_of(g) or l["state"]
                        out.append(l)
        return out
