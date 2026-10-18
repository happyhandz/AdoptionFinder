"""King County (WA) Regional Animal Services adoptable dogs, via the county's public Socrata
open-data feed (no key needed; PetHarbor links/photos). Shelter is in Kent."""
import re

import httpx

from .. import config, geo, normalize
from .base import Source

URL = "https://data.kingcounty.gov/resource/ytc8-tcih.json"
_ZIP = re.compile(r"\b([A-Z]{2})\s+(\d{5})\b")
_SIZE = re.compile(r"(small|medium|large|extra[- ]large)[\"']?[- ](?:breed|dog)\b", re.I)


def parse(rec):
    if (rec.get("animal_type") or "").lower() != "dog":
        return None
    breed = [b.strip() for b in (rec.get("animal_breed") or "").split("/") if b.strip()]
    mixed = any(b.lower() == "mix" for b in breed)
    breed = [b for b in breed if b.lower() != "mix"]
    memo_html = rec.get("memo") or ""
    memo = normalize.strip_html(memo_html.replace("</p>", " "))
    months = normalize.parse_age_months(rec.get("age"))
    sex = (rec.get("animal_gender") or "").lower()
    sex = "male" if "male" in sex and "female" not in sex else "female" if "female" in sex else None
    loc = rec.get("current_location") or ""
    zm = _ZIP.search(loc)
    size = _SIZE.search(memo)
    return {
        "source": "kingcounty",
        "source_id": rec.get("animal_id") or rec.get("impound_no"),
        "url": (rec.get("link") or {}).get("url"),
        "name": (rec.get("animal_name") or "").strip().title(),
        "breed_primary": breed[0] if breed else "Mixed Breed",
        "breed_secondary": breed[1] if len(breed) > 1 else None,
        "mixed": 1 if mixed or len(breed) > 1 else 0,
        "color": rec.get("animal_color"),
        "age_group": normalize.age_group_from_months(months),
        "age_text": (rec.get("age") or "").title() or None,
        "age_months": months,
        "sex": sex,
        "size": normalize.norm_size(size.group(1)) if size else None,
        "description": memo,
        "photos": [(rec.get("image") or {}).get("url")] if (rec.get("image") or {}).get("url") else [],
        "city": None,  # filled from the ZIP lookup (street addresses make parsing it unreliable)
        "state": zm.group(1) if zm else "WA",
        "zip": zm.group(2) if zm else None,
        "lat": None,
        "lon": None,
        "org": "King County Regional Animal Services",
        "transport": 0,
        "listed_at": normalize.to_epoch(rec.get("date")),
    }


class KingCounty(Source):
    name = "kingcounty"

    def fetch(self, region, db=None):
        r = httpx.get(URL, timeout=30, headers={"User-Agent": config.USER_AGENT},
                      params={"$where": "record_type='ADOPTABLE' AND animal_type='Dog'", "$limit": 1000})
        r.raise_for_status()
        out = []
        for rec in r.json():
            l = parse(rec)
            if not l or not l["source_id"]:
                continue
            g = geo.geocode(db, l["zip"]) if (db and l["zip"]) else None
            if g:
                l["lat"], l["lon"] = g["lat"], g["lon"]
                l["city"] = g["label"].split(",")[0]
                if not geo.in_region(region, g):
                    continue
            out.append(l)
        return out
