"""Helper for building site definitions.

  python -m app.webtool probe <url>            what does this page look like to a scraper?
  python -m app.webtool run sites/foo.json     run a definition and print what it extracts
"""
import collections
import json
import re
import sys

from bs4 import BeautifulSoup

from .sources import webscrape as w


def probe(url):
    f = w.Fetcher()
    print(f"robots.txt allows: {f.allowed(url)}")
    try:
        r = f.get(url, respect_robots=False)
    except w.BlockedError as e:
        print("BLOCKED:", e)
        return
    t = r.text
    soup = BeautifulSoup(t, "lxml")
    print(f"HTTP {r.status_code}, {len(t)//1000} KB, title: {soup.title.get_text(strip=True) if soup.title else None!r}")
    ld = re.findall(r'<script[^>]*ld\+json[^>]*>(.*?)</script>', t, re.S | re.I)
    types = collections.Counter()
    for b in ld:
        try:
            for o in (lambda d: d if isinstance(d, list) else [d])(json.loads(b)):
                types[str(o.get("@type")) if isinstance(o, dict) else "?"] += 1
        except ValueError:
            pass
    print(f"JSON-LD blocks: {len(ld)} {dict(types)}")
    ids = re.findall(r'<script[^>]*\bid="([^"]+)"[^>]*type="application/json"|<script[^>]*type="application/json"[^>]*\bid="([^"]+)"', t)
    print("application/json script ids:", [a or b for a, b in ids][:8])
    print("window.* state vars:", sorted(set(re.findall(r'window\.(__[A-Za-z_]+__)\s*=', t)))[:8])
    print("next.js flight data:", "__next_f" in t)
    ifr = [i.get("src") for i in soup.find_all("iframe") if i.get("src")]
    print("iframes:", ifr[:5])
    vend = [k for k in ("shelterluv", "petpoint", "petango", "rescuegroups", "petstablished", "24petconnect") if k in t.lower()]
    print("known listing platforms mentioned:", vend)
    # repeated elements with images = likely dog cards
    groups = collections.Counter()
    for el in soup.find_all(True, class_=True):
        if el.find("img"):
            groups[el.name + "." + ".".join(el.get("class"))] += 1
    print("repeated elements containing <img> (likely cards):")
    for sel, n in groups.most_common(8):
        if n > 2:
            print(f"   {n:3d} x {sel}")


def run(path, limit=8):
    data = json.load(open(path, encoding="utf-8"))
    for site in (data if isinstance(data, list) else [data]):
        src = w.WebSite(site)
        print(f"== {src.name}")
        try:
            ls = src.scrape()
        except (w.BlockedError, PermissionError) as e:
            print("  ", type(e).__name__, e)
            continue
        print(f"  {len(ls)} dogs")
        for l in ls[:limit]:
            print("  -", {k: l[k] for k in ("name", "breed_primary", "age_text", "sex", "color", "size") if l[k]}, (l["photos"] or [""])[0][:70], "|", (l["url"] or "")[:70])


if __name__ == "__main__":
    cmd = sys.argv[1:2]
    if cmd == ["probe"] and len(sys.argv) > 2:
        probe(sys.argv[2])
    elif cmd == ["run"] and len(sys.argv) > 2:
        run(sys.argv[2])
    else:
        print(__doc__)
