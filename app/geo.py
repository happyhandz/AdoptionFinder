import math
import re

import httpx

from . import config

_ZIP_RE = re.compile(r"^\d{5}$")


def haversine(lat1, lon1, lat2, lon2):
    """Great-circle distance in miles."""
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def geocode(db, query):
    """Resolve '98031' or 'Kent, WA' to {lat, lon, zip, label}. Cached in SQLite.
    Uses zippopotam.us (free, no key). Returns None if not found."""
    query = (query or "").strip()
    if not query:
        return None
    key = query.lower()
    cached = db.geo_get(key)
    if cached:
        return cached if cached["lat"] is not None else None  # lat NULL = known-not-found
    try:
        if _ZIP_RE.match(query):
            url = f"https://api.zippopotam.us/us/{query}"
        else:
            m = re.match(r"^\s*(.+?)\s*,\s*([A-Za-z]{2})\s*$", query)
            if not m:
                return None
            city, st = m.groups()
            url = f"https://api.zippopotam.us/us/{st.lower()}/{city.lower().replace(' ', '%20')}"
        r = httpx.get(url, timeout=15, headers={"User-Agent": config.USER_AGENT})
        if r.status_code == 404:
            db.geo_put(key, None, None, None, None)
            return None
        if r.status_code != 200:
            return None
        data = r.json()
        place = data["places"][0]
        zip_ = data.get("post code") or place.get("post code")
        label = f"{place['place name']}, {place.get('state abbreviation', '')} {zip_}".strip()
        lat, lon = float(place["latitude"]), float(place["longitude"])
    except Exception:
        return None
    db.geo_put(key, lat, lon, zip_, label)
    return {"key": key, "lat": lat, "lon": lon, "zip": zip_, "label": label}


def state_of(g):
    """'Kent, WA 98031' -> 'WA' (from a geocode result)."""
    m = re.search(r",\s*([A-Z]{2})\b", (g or {}).get("label") or "")
    return m.group(1) if m else None


def in_region(region, g):
    """Is geocoded point g inside a polling region? Whole-state regions match on state, others on radius."""
    if region.get("statewide"):
        return state_of(g) == region["state"]
    return haversine(region["lat"], region["lon"], g["lat"], g["lon"]) <= region["radius"]
