"""Filtering/grouping of stored listings into dog cards."""
import re
import time

from . import config, geo, normalize

SORTS = {"newest", "closest", "youngest"}


def _terms(s):
    return [t.strip().lower() for t in (s or "").split(",") if t.strip()]


def resolve_origin(db, params):
    loc = params.get("location") or config.DEFAULT_LOCATION
    return geo.geocode(db, loc)


def breed_token(s):
    """A breed as one underscore-joined word: "Chihuahua, Short Coat" -> "chihuahua_short_coat".
    Spaces, commas and punctuation never split a breed, so its own commas can't break the filter."""
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")


def split_breed_terms(s):
    """"lab, -chihuahua, -pit bull" -> (["lab"], ["chihuahua", "pit bull"]). A leading "-" excludes."""
    inc, exc = [], []
    for t in _terms(s):
        neg = t.startswith("-")
        t = breed_token(t)
        if t:
            (exc if neg else inc).append(t)
    return inc, exc


def search(db, params, counts=None, breed_options=None):
    """params: location, radius, breed, color, age (csv of groups), sex, size,
    max_days, include_undated, undated_last, hide_sources ("|"-separated source names to hide),
    transport (include|only|exclude), q, sort.
    If a dict is passed as `counts` it is filled with {source: number of cards that include it} for the
    results of all the other filters (i.e. before hide_sources), so the UI can show what each source adds.
    The breed filter takes include terms and, with a leading "-", exclude terms ("lab, -chihuahua").
    If a dict is passed as `breed_options` it is filled with {breed name: dogs} for listings that pass every
    filter except breed, to feed the breed box's suggestions."""
    origin = resolve_origin(db, params)
    radius = float(params.get("radius") or config.DEFAULT_RADIUS)
    breeds, not_breeds = split_breed_terms(params.get("breed"))
    colors = _terms(params.get("color"))
    ages = set(_terms(params.get("age")))
    sex, size = (params.get("sex") or "").lower(), (params.get("size") or "").lower()
    max_days = float(params["max_days"]) if params.get("max_days") else None
    include_undated = str(params.get("include_undated")) in ("1", "true", "True")
    undated_last = str(params.get("undated_last")) in ("1", "true", "True")
    tmode = params.get("transport") or "include"
    kw = (params.get("q") or "").lower().strip()
    # "Whole state": ignore the radius and keep everything in the origin's state
    state = (geo.state_of(origin) or "").upper() if origin else ""
    statewide = str(params.get("statewide")) in ("1", "true", "True") and bool(state)
    now = time.time()
    first_ok = {s["source"]: s.get("first_ok") for s in db.statuses()}

    groups, vetoed = {}, set()
    for l in db.active_listings():
        dist = None
        lat, lon = l["lat"], l["lon"]
        if lat is None and l["zip"]:
            g = geo.geocode(db, str(l["zip"])[:5])
            if g:
                lat, lon = g["lat"], g["lon"]
        if origin and lat is not None:
            dist = geo.haversine(origin["lat"], origin["lon"], lat, lon)
        l["distance"] = dist
        if statewide:
            out_of_area = (l["state"] or "").upper() != state
        else:
            out_of_area = dist is not None and dist > radius
        if out_of_area and not (l["transport"] and tmode != "exclude"):
            continue
        if tmode == "only" and not l["transport"]:
            continue
        if tmode == "exclude" and l["transport"]:
            continue
        hay_breed = f"{l['breed_primary'] or ''} {l['breed_secondary'] or ''}".lower()
        hay_tokens = f"{breed_token(l['breed_primary'])}|{breed_token(l['breed_secondary'])}"
        if colors and not any(c in (l["color"] or "").lower() for c in colors):
            continue
        if ages and l["age_group"] not in ages:
            continue
        if sex and l["sex"] != sex:
            continue
        if size and l["size"] != size:
            continue
        if kw and kw not in f"{l['name']} {hay_breed} {l['description'] or ''} {l['org'] or ''}".lower():
            continue
        if breed_options is not None:
            for b in {l["breed_primary"], l["breed_secondary"]} - {None, ""}:
                breed_options[b] = breed_options.get(b, 0) + 1
        if breeds and not any(b in hay_tokens for b in breeds):
            continue
        if not_breeds and any(b in hay_tokens for b in not_breeds):
            vetoed.add(l["canonical_id"] or l["id"])      # the same dog listed elsewhere must not bring the card back
            continue
        # An undated dog first seen during its source's very first poll could have been listed weeks ago:
        # we only know "it was there when we started looking", not that it is new.
        l["initial_load"] = (not l["listed_at"]) and first_ok.get(l["source"]) is not None             and l["first_seen"] <= first_ok[l["source"]]
        groups.setdefault(l["canonical_id"] or l["id"], []).append(l)

    cards = [_card(members, now) for gid, members in groups.items() if gid not in vetoed]
    if max_days is not None:
        # "Listed within N days" is only meaningful for dogs whose source gave a date. Dogs with no source
        # date are shown only when asked for (we'd just be guessing from when we first saw them).
        cards = [c for c in cards
                 if (c["date_known"] and c["days_listed"] <= max_days) or (not c["date_known"] and include_undated)]
    if counts is not None:
        for c in cards:
            for src in {x["source"] for x in c["sources"]}:
                counts[src] = counts.get(src, 0) + 1
    hidden = {s for s in (params.get("hide_sources") or "").split("|") if s}
    if hidden:
        # a card merged from several sources stays visible while at least one of its sources is shown
        cards = [c for c in cards if any(x["source"] not in hidden for x in c["sources"])]
    sort = params.get("sort") if params.get("sort") in SORTS else "newest"
    if sort == "closest":
        cards.sort(key=lambda c: (c["distance"] is None, c["distance"] or 0))
    elif sort == "youngest":
        cards.sort(key=lambda c: (c["age_months"] is None, c["age_months"] or 0))
    else:
        # Newest first. Dogs with no listing date from their source (listed_at = when we first saw them) form
        # their own block: on top by default, or after all dated dogs when "undated last" is on.
        cards.sort(key=lambda c: ((not c["date_known"]) == undated_last, -c["listed_at"]))
    return cards


def _card(members, now):
    members.sort(key=lambda m: (m["distance"] is None, m["distance"] or 0))
    m = members[0]
    photos = []
    for x in members:
        for p in normalize.real_photos(x["photos"]):
            if p not in photos:
                photos.append(p)
    real_dates = [x["listed_at"] for x in members if x["listed_at"]]
    listed_at = min(real_dates) if real_dates else min(x["first_seen"] for x in members)
    breed = m["breed_primary"] or "Unknown breed"
    if m["breed_secondary"]:
        breed += f" / {m['breed_secondary']}"
    elif m["mixed"] and "mix" not in breed.lower():
        breed += " mix"
    return {
        "id": m["id"],
        "name": m["name"],
        "breed": breed,
        "age_text": m["age_text"],
        "age_group": m["age_group"],
        "age_months": m["age_months"],
        "sex": m["sex"],
        "size": m["size"],
        "color": m["color"],
        "photos": photos,
        "city": m["city"],
        "state": m["state"],
        "org": m["org"],
        "description": m["description"],
        "distance": None if m["distance"] is None else round(m["distance"]),
        "transport": bool(any(x["transport"] for x in members)),
        "days_listed": round(max(now - listed_at, 0) / 86400, 1),
        "listed_at": listed_at,
        "date_known": bool(real_dates),
        "age_unknown": not real_dates and all(x["initial_load"] for x in members),
        "first_seen": min(x["first_seen"] for x in members),
        "sources": [{"source": x["source"], "url": x["url"], "listing_id": x["id"], "org": x["org"]} for x in members],
    }
