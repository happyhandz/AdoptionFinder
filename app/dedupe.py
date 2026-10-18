"""Cross-source duplicate detection.

The same dog is often listed by one rescue on several sites (Shelterluv + Adopt-a-Pet + the rescue's
own page). We merge those into one card by scoring every pair of listings from DIFFERENT sources
that share a name, adding up evidence:

    organization name similar   +3 / +1 / -2
    sex                         +2 same, veto if they conflict
    age                         +2 close, -3 far apart
    breed words                 +2 / +1 overlap, -2 if clearly different
    size, colour                +1 each when they agree
    distance                    +1 close, -1 far (when both have coordinates)
    photo (borderline pairs)    perceptual hash of the first photo: +5 near-identical, -1 clearly different

A pair merges at >= MERGE. Pairs scoring in the grey zone get their photos compared (downloaded once,
hash cached in the DB). Merging is greedy by score, never puts two listings from the same source in one
group, and honours "not the same dog" splits the user made in the UI. Groups are recomputed from scratch
on every run, so a merge is never permanent: change the data or add a split and the card separates.
"""
import io
import logging
import re
from collections import defaultdict

import httpx

from . import config, geo, normalize

log = logging.getLogger("dedupe")

MERGE = 6.0
GREY = 3.5          # below this a photo can't rescue the pair
MAX_PHOTO_FETCHES = 60

BREED_STOP = {"mix", "mixed", "breed", "unknown", "type", "dog", "small", "medium", "large", "xlarge",
              "x", "and", "the", "of", "domestic", "general", "other", "crossbreed", "cross", "pure"}
ORG_STOP = {"the", "of", "and", "inc", "llc", "animal", "animals", "rescue", "humane", "society", "shelter",
            "dog", "dogs", "pet", "pets", "adoption", "adoptions", "foster", "transport", "wa", "washington",
            "county", "services", "service", "for", "a", "in", "at", "program", "center", "network"}


# ---- normalisation -----------------------------------------------------------------

def clean_name(n):
    n = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", (n or "").lower())
    n = re.split(r"\s+-\s+|\s*[,|]\s*", n)[0]
    return re.sub(r"[^a-z ]", "", n).strip()


def name_match(a, b):
    if not a or not b:
        return False
    if a == b:
        return True
    ta, tb = set(a.split()), set(b.split())
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return len(" ".join(short)) >= 3 and short <= long_ and len(short) < len(long_)


def _tokens(text, stop):
    return {t for t in re.findall(r"[a-z]+", (text or "").lower()) if len(t) > 1 and t not in stop}


def _jaccard(a, b):
    return len(a & b) / len(a | b) if a and b else None


def _org_tokens(l):
    return _tokens(l.get("org"), ORG_STOP)


def _breed_tokens(l):
    return _tokens(f"{l.get('breed_primary') or ''} {l.get('breed_secondary') or ''}", BREED_STOP)


# ---- scoring -------------------------------------------------------------------------

def score_pair(a, b):
    """Return (score, vetoed). Higher = more likely the same dog."""
    s = 0.0
    oa, ob = _org_tokens(a), _org_tokens(b)
    j = _jaccard(oa, ob)
    if j is not None:
        s += 3 if j >= 0.6 else 1 if j >= 0.3 else -2
    if a.get("sex") and b.get("sex"):
        if a["sex"] != b["sex"]:
            return -99.0, True
        s += 2
    ma, mb = a.get("age_months"), b.get("age_months")
    if ma is not None and mb is not None:
        tol = max(3.0, 0.2 * max(ma, mb))
        diff = abs(ma - mb)
        s += 2 if diff <= tol else (0 if diff <= 2 * tol else -3)
    elif a.get("age_group") and b.get("age_group") and a["age_group"] != b["age_group"]:
        s -= 1
    ba, bb = _breed_tokens(a), _breed_tokens(b)
    jb = _jaccard(ba, bb)
    if jb is not None:
        s += 2 if jb >= 0.5 else 1 if ba & bb else -2
    if a.get("size") and b.get("size") and a["size"] == b["size"]:
        s += 1
    ca, cb = _tokens(a.get("color"), set()), _tokens(b.get("color"), set())
    if ca & cb:
        s += 1
    if None not in (a.get("lat"), a.get("lon"), b.get("lat"), b.get("lon")):
        d = geo.haversine(a["lat"], a["lon"], b["lat"], b["lon"])
        s += 1 if d <= 25 else (-1 if d > 100 else 0)
    return s, False


# ---- photo hashing --------------------------------------------------------------------

def dhash_bytes(data):
    from PIL import Image
    im = Image.open(io.BytesIO(data)).convert("L").resize((9, 8))
    px = im.tobytes()  # 72 grey bytes
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (px[row * 9 + col] > px[row * 9 + col + 1])
    return f"{bits:016x}"


def hamming(h1, h2):
    return bin(int(h1, 16) ^ int(h2, 16)).count("1")


def photo_hash(db, listing, budget):
    """Cached perceptual hash of the first photo; budget = [remaining_fetches]. '' means unavailable."""
    if listing.get("photo_hash") is not None:
        return listing["photo_hash"] or None
    photos = normalize.real_photos(listing.get("photos"))
    if not photos or budget[0] <= 0:
        return None
    budget[0] -= 1
    h = ""
    try:
        r = httpx.get(photos[0], timeout=15, follow_redirects=True,
                      headers={"User-Agent": config.USER_AGENT})
        if r.status_code == 200 and len(r.content) < 5_000_000:
            h = dhash_bytes(r.content)
    except Exception as e:
        log.info("photo hash failed for %s: %s", listing.get("id"), e)
    db.set_photo_hash(listing["id"], h)
    listing["photo_hash"] = h
    return h or None


def photo_bonus(h1, h2):
    if not h1 or not h2:
        return 0.0
    d = hamming(h1, h2)
    return 5.0 if d <= 8 else 3.0 if d <= 12 else (-1.0 if d >= 28 else 0.0)


# ---- clustering ------------------------------------------------------------------------

class _Groups:
    def __init__(self, rows, splits):
        self.parent = {r["id"]: r["id"] for r in rows}
        self.members = {r["id"]: {r["id"]} for r in rows}
        self.sources = {r["id"]: {r["source"]} for r in rows}
        self.splits = splits

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def can_union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        if self.sources[ra] & self.sources[rb]:
            return False  # two listings from one source are different dogs
        return not any((min(x, y), max(x, y)) in self.splits for x in self.members[ra] for y in self.members[rb])

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        self.parent[rb] = ra
        self.members[ra] |= self.members.pop(rb)
        self.sources[ra] |= self.sources.pop(rb)


def candidate_pairs(rows):
    by_token = defaultdict(list)
    names = {}
    for r in rows:
        n = clean_name(r["name"])
        names[r["id"]] = n
        for t in set(n.split()):
            if len(t) >= 3:
                by_token[t].append(r)
    seen, out = set(), []
    for group in by_token.values():
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                if a["source"] == b["source"]:
                    continue
                key = (min(a["id"], b["id"]), max(a["id"], b["id"]))
                if key in seen:
                    continue
                seen.add(key)
                if name_match(names[a["id"]], names[b["id"]]):
                    out.append((a, b))
    return out


def run(db, fetch_photos=True):
    """Recompute duplicate groups for all active listings and store canonical ids. Returns #merged groups."""
    rows = db.active_listings()
    splits = db.splits()
    budget = [MAX_PHOTO_FETCHES if fetch_photos else 0]
    scored = []
    for a, b in candidate_pairs(rows):
        s, veto = score_pair(a, b)
        if veto:
            continue
        if s < MERGE and s >= GREY:
            s += photo_bonus(photo_hash(db, a, budget), photo_hash(db, b, budget))
        elif s >= MERGE:
            # confirm strong matches only if photos are already cached (never blocks on downloads)
            if a.get("photo_hash") and b.get("photo_hash"):
                s += photo_bonus(a["photo_hash"], b["photo_hash"])
        if s >= MERGE:
            scored.append((s, a["id"], b["id"]))
    groups = _Groups(rows, splits)
    for s, x, y in sorted(scored, reverse=True):
        if groups.can_union(x, y):
            groups.union(x, y)
    mapping = {}
    for r in rows:
        mapping[r["id"]] = min(groups.members[groups.find(r["id"])])
    current = {r["id"]: r["canonical_id"] for r in rows}
    changed = {i: c for i, c in mapping.items() if current.get(i) != c}
    if changed:
        db.set_canonicals(changed)
    merged = len({c for i, c in mapping.items() if c != i})
    if changed:
        log.info("dedupe: %d listings regrouped, %d merged groups", len(changed), merged)
    return merged
