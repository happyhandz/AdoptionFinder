import re
from datetime import datetime, timezone

TRANSPORT_RE = re.compile(r"\btransport(?:ed|ation|s)?\b|\bwill (?:ship|deliver)\b|\bout[- ]of[- ]state\b", re.I)
_AGE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(year|yr|month|mo|week|wk|y|m|w)s?\b", re.I)

AGE_GROUPS = ["puppy", "young", "adult", "senior"]


def parse_age_months(text):
    """'1 Year 2 Months' -> 14.0 ; '8 weeks' -> ~1.8 ; unknown -> None"""
    if not text:
        return None
    total, found = 0.0, False
    for num, unit in _AGE_RE.findall(text):
        n, u = float(num), unit.lower()
        found = True
        if u.startswith(("year", "yr", "y")):
            total += n * 12
        elif u.startswith(("month", "mo", "m")):
            total += n
        else:
            total += n * 12 / 52
    return round(total, 1) if found else None


def age_group_from_months(m):
    if m is None:
        return None
    if m < 12:
        return "puppy"
    if m < 36:
        return "young"
    if m < 96:
        return "adult"
    return "senior"


def age_group_from_label(label):
    s = (label or "").lower()
    return {"baby": "puppy", "puppy": "puppy", "young": "young", "adult": "adult", "senior": "senior"}.get(s)


def detect_transport(*texts):
    return 1 if any(t and TRANSPORT_RE.search(t) for t in texts) else 0


def to_epoch(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()
    except ValueError:
        return None


def strip_html(text):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text or "")).strip()


def norm_size(s):
    s = (s or "").lower()
    for key in ("small", "medium", "large"):
        if key in s:
            return key
    if "x-large" in s or "extra" in s or "giant" in s:
        return "xlarge"
    return None


_PLACEHOLDER = re.compile(r"photo[-_ ]?not[-_ ]?available|no[-_ ]?photo|placeholder|coming[-_ ]?soon", re.I)


def real_photos(photos):
    """Drop 'photo not available' stand-in images that some platforms return instead of a blank."""
    return [p for p in (photos or []) if p and not _PLACEHOLDER.search(p)]
