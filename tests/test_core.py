import time

from app import geo, normalize, search
from app.db import DB
from app.sources.rescuegroups import RescueGroups


def make(**kw):
    base = {"source": "t", "source_id": "1", "url": "u", "name": "Rex", "breed_primary": "Beagle",
            "breed_secondary": None, "mixed": 0, "color": "Brown", "age_group": "adult", "age_text": "4 years",
            "age_months": 48, "sex": "male", "size": "medium", "description": "", "photos": ["p.jpg"],
            "city": "Kent", "state": "WA", "zip": "98031", "lat": 47.38, "lon": -122.23, "org": "R",
            "transport": 0, "listed_at": time.time()}
    base.update(kw)
    return base


def test_age_parsing():
    assert normalize.parse_age_months("1 Year 2 Months") == 14
    assert normalize.age_group_from_months(6) == "puppy"
    assert normalize.age_group_from_months(200) == "senior"
    assert normalize.parse_age_months("unknown") is None


def test_transport_detection():
    assert normalize.detect_transport("Transport available to WA")
    assert not normalize.detect_transport("Loves walks")


def test_haversine():
    assert 2300 < geo.haversine(47.6, -122.3, 40.7, -74.0) < 2450


def test_upsert_and_remove_unseen():
    db = DB(":memory:")
    lid, new = db.upsert_listing(make())
    assert new
    _, new2 = db.upsert_listing(make())
    assert not new2
    assert db.count_active("t") == 1
    t = time.time() + 1
    assert db.count_seen_since("t", t) == 0
    assert db.remove_unseen("t", t) == 1                 # not seen since t -> gone (the cleanup check does this, once)
    assert db.active_listings() == [] and db.count_active("t") == 0
    db.upsert_listing(make())                            # it is listed again: back
    assert db.count_active("t") == 1


def test_search_filters(monkeypatch):
    db = DB(":memory:")
    monkeypatch.setattr(geo, "geocode", lambda d, q: {"lat": 47.38, "lon": -122.23, "zip": "98031", "label": "Kent"})
    db.upsert_listing(make(source_id="1"))
    db.upsert_listing(make(source_id="2", name="Far", lat=45.5, lon=-122.68))  # Portland ~145mi
    db.upsert_listing(make(source_id="3", name="Ship", lat=45.5, lon=-122.68, transport=1))
    db.upsert_listing(make(source_id="4", name="Pup", age_group="puppy", breed_primary="Labrador"))
    names = lambda p: sorted(c["name"] for c in search.search(db, p))
    assert names({"radius": 50}) == ["Pup", "Rex", "Ship"]  # Ship kept via transport
    assert names({"radius": 50, "transport": "exclude"}) == ["Pup", "Rex"]
    assert names({"radius": 200}) == ["Far", "Pup", "Rex", "Ship"]
    assert names({"radius": 200, "transport": "only"}) == ["Ship"]
    assert names({"radius": 50, "age": "puppy"}) == ["Pup"]
    assert names({"radius": 50, "breed": "lab"}) == ["Pup"]


def test_rescuegroups_parse():
    payload = {
        "data": [{"id": "9", "type": "animals", "attributes": {
            "name": "Bo", "breedPrimary": "Husky", "ageGroup": "Young", "ageString": "2 Years", "sex": "Male",
            "sizeGroup": "Large", "descriptionText": "Transport possible", "createdDate": "2025-01-02T03:04:05Z",
            "isBreedMixed": True, "url": "http://x"},
            "relationships": {"pictures": {"data": [{"type": "pictures", "id": "p1"}]},
                              "orgs": {"data": [{"type": "orgs", "id": "o1"}]},
                              "locations": {"data": [{"type": "locations", "id": "l1"}]}}}],
        "included": [
            {"type": "pictures", "id": "p1", "attributes": {"large": {"url": "http://img"}, "order": 1}},
            {"type": "orgs", "id": "o1", "attributes": {"name": "Rescue", "city": "Kent", "state": "WA"}},
            {"type": "locations", "id": "l1", "attributes": {"lat": 47.1, "lon": -122.1, "postalcode": "98031"}}]}
    [l] = RescueGroups._parse(payload)
    assert l["photos"] == ["http://img"] and l["org"] == "Rescue" and l["age_group"] == "young"
    assert l["transport"] == 1 and l["lat"] == 47.1 and l["size"] == "large" and l["sex"] == "male"


# ---- Adopt-a-Pet ----
from pathlib import Path

from app.sources import adoptapet


def test_adoptapet_slug_parsing():
    u = "https://www.adoptapet.com/pet/49458942-renton-washington-husky-mix"
    assert adoptapet.parse_slug(u)[:4] == ("49458942", "renton", "WA", "husky-mix")
    assert adoptapet.parse_slug("https://www.adoptapet.com/pet/1-new-york-new-york-pit-bull")[1:3] == ("new york", "NY")
    assert adoptapet.parse_slug("https://www.adoptapet.com/pet/2-federal-way-washington-kitten") is None
    assert adoptapet.parse_slug("https://www.adoptapet.com/pet/3-x-california-australian-cattle-dog-mix") is not None


def test_adoptapet_candidate_states():
    s = adoptapet.candidate_states(47.38, -122.23, 50)
    assert "WA" in s and "TX" not in s
    assert {"WA", "OR"} <= adoptapet.candidate_states(47.38, -122.23, 200)
    assert "ID" in adoptapet.candidate_states(47.38, -122.23, 300)


def test_adoptapet_detail_parse():
    html = (Path(__file__).parent / "fixtures" / "adoptapet_pet.html").read_text(encoding="utf-8", errors="replace")
    pet = adoptapet.parse_detail(html)
    l = adoptapet.listing_from_detail(pet, "german-shepherd-dog-mix")
    assert l["name"] == "Kira" and l["source_id"] == "47308141"
    assert l["breed_primary"] == "German Shepherd Dog" and l["mixed"] == 1
    assert l["sex"] == "female" and l["size"] == "large" and l["color"] == "Black"
    assert l["age_group"] == "young" and abs(l["age_months"] - 31) < 0.5
    assert l["org"] == "Northwest Working Breed Rescue" and l["zip"] == "98059" and l["state"] == "WA"
    assert len(l["photos"]) == 3 and l["photos"][0].endswith("/1288587973")
    assert "sweet" in l["description"] and l["listed_at"]


def test_db_skip_and_touch():
    db = DB(":memory:")
    db.skip("s", "9")
    assert "9" in db.known_ids("s")
    db.upsert_listing(make(source="s", source_id="1"))
    db.touch("s", ["1", "9"])
    assert len(db.active_listings()) == 1  # skip rows never become active


# ---- King County ----
from app.sources import kingcounty


def test_kingcounty_parse():
    rec = {"animal_id": "A752969", "animal_type": "Dog", "animal_name": "MR LUIS", "age": "4 YEARS",
           "animal_gender": "Neutered Male", "animal_breed": "Pit Bull / Mix", "animal_color": "White / Tan",
           "date": "2026-09-03T00:00:00.000", "link": {"url": "https://petharbor.com/pet.asp?uaid=KING.A752969"},
           "current_location": "King County Pet Adoption Center 21615 64TH AVE S KENT, WA 98032",
           "image": {"url": "https://petharbor.com/get_image.asp?ID=A752969"},
           "memo": "Age: 4 YEARS</p> Adoption Fee: $30</p>I am considered a medium breed dog.</p>"}
    l = kingcounty.parse(rec)
    assert l["name"] == "Mr Luis" and l["breed_primary"] == "Pit Bull" and l["mixed"] == 1
    assert l["breed_secondary"] is None and l["sex"] == "male" and l["size"] == "medium"
    assert l["zip"] == "98032" and l["age_group"] == "adult"
    assert kingcounty.parse({**rec, "animal_type": "Cat"}) is None
    assert kingcounty.parse({**rec, "animal_gender": "Spayed Female"})["sex"] == "female"


def test_settings_roundtrip():
    db = DB(":memory:")
    assert db.get_setting("poll_minutes", "30") == "30"
    db.set_setting("poll_minutes", 15)
    db.set_setting("poll_minutes", 20)
    assert db.get_setting("poll_minutes") == "20"


# ---- Shelterluv ----
from app.sources import shelterluv


def test_shelterluv_parse():
    rec = {"nid": 1, "name": "Matcha ", "uniqueId": "SHS-A-72684", "sex": "Female", "species": "Dog",
           "birthday": str(int(time.time() - 3 * 365 * 86400)), "breed": "Mixed Breed (Medium)", "secondary_breed": "",
           "primary_color": "Black", "secondary_color": "White", "weight_group": "Medium (25-59)",
           "attributes": ["Good with Dogs", "Housetrained"], "adoptable": 1, "intake_date": "1710451051",
           "public_url": "https://www.shelterluv.com/embed/animal/SHS-A-72684",
           "photos": {"0": {"url": "a.jpg", "isCover": False, "order_column": 1},
                      "1": {"url": "cover.jpg", "isCover": True, "order_column": 2}}}
    l = shelterluv.parse(rec, "Spokane HS", "99201", False)
    assert l["name"] == "Matcha" and l["sex"] == "female" and l["mixed"] == 1 and l["size"] == "medium"
    assert l["photos"] == ["cover.jpg", "a.jpg"] and l["color"] == "Black / White"
    assert l["age_group"] == "adult" and l["listed_at"] == 1710451051.0 and "Housetrained" in l["description"]
    assert shelterluv.parse({**rec, "species": "Cat"}, "x", "1", False) is None
    assert shelterluv.parse({**rec, "adoptable": 0}, "x", "1", False) is None
    assert shelterluv.parse(rec, "x", "1", True)["transport"] == 1


def test_shelterluv_photos_as_list():
    rec = {"uniqueId": "X-1", "name": "A", "species": "Dog", "adoptable": 1,
           "photos": [{"url": "b.jpg", "order_column": 2}, {"url": "a.jpg", "order_column": 1}]}
    assert shelterluv.parse(rec, "o", "99201", False)["photos"] == ["a.jpg", "b.jpg"]
    assert shelterluv.parse({**rec, "photos": []}, "o", "99201", False)["photos"] == []


# ---- generic web scraper ----
import json as _json

from app.sources import webscrape as ws


class FakeResp:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status


class FakeFetcher:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def get(self, url, **kw):
        self.calls.append(url)
        if url not in self.pages:
            return FakeResp("<html><body></body></html>")
        return FakeResp(self.pages[url])


def _paws():
    return _json.loads((Path(__file__).parents[1] / "sites" / "paws.json").read_text(encoding="utf-8"))


def test_webscrape_css_paws_fixture():
    site = _paws()
    html = (Path(__file__).parent / "fixtures" / "paws_dogs.html").read_text(encoding="utf-8")
    f = FakeFetcher({site["urls"][0]: html})
    dogs = ws.WebSite(site, f).scrape()
    assert [d["name"] for d in dogs][:3] == ["Babe", "Moo Cow", "Rex"]
    babe = dogs[0]
    assert babe["sex"] == "female" and babe["breed_primary"] == "Mixed Breed" and babe["breed_secondary"] is None
    assert babe["age_months"] == 24 and babe["size"] == "medium" and babe["photos"][0].endswith(".jpg")
    assert babe["source"] == "PAWS Lynnwood" and babe["source_id"] == "61686515"
    assert babe["url"].startswith("https://www.paws.org/animal")


def test_webscrape_json_modes():
    site = {"name": "J", "urls": ["http://x/api"], "mode": "json", "items": "data.dogs",
            "fields": {"id": "id", "name": "name", "breed": "breed", "age": "age",
                       "photo": {"path": "pics", "key": "url"}, "species": "type", "sex": "gender"}}
    body = _json.dumps({"data": {"dogs": [
        {"id": 7, "name": "Rex", "breed": "Lab / Mix", "age": "3 months", "pics": [{"url": "/a.jpg"}], "type": "Dog", "gender": "Boy"},
        {"id": 8, "name": "Tom", "breed": "Tabby", "type": "Cat"}]}})
    dogs = ws.WebSite(site, FakeFetcher({"http://x/api": body})).scrape()
    assert len(dogs) == 1  # cat filtered by species
    d = dogs[0]
    assert d["source_id"] == "7" and d["sex"] == "male" and d["age_group"] == "puppy" and d["mixed"] == 1
    assert d["photos"] == ["http://x/a.jpg"]
    emb = {"name": "E", "urls": ["http://x/"], "mode": "embedded_json", "blob_regex": r"__DATA__=(\{.*?\});",
           "items": "a.b", "fields": {"name": "n", "id": "i"}}
    page = '<script>window.__DATA__={"a":{"b":[{"n":"Zed","i":"9"}]}};</script>'
    assert ws.WebSite(emb, FakeFetcher({"http://x/": page})).scrape()[0]["name"] == "Zed"
    ld = {"name": "L", "urls": ["http://x/"], "mode": "jsonld", "items": "Animal|Product", "fields": {"name": "name", "photo": "image"}}
    page = '<script type="application/ld+json">{"@graph":[{"@type":"Product","name":"Pip","image":"http://i/p.jpg"},{"@type":"WebPage","name":"no"}]}</script>'
    got = ws.WebSite(ld, FakeFetcher({"http://x/": page})).scrape()
    assert [g["name"] for g in got] == ["Pip"]


def test_webscrape_pagination_stops():
    site = {"name": "P", "urls": ["http://x/?page={page}"], "pagination": {"max_pages": 5}, "mode": "css",
            "items": ".d", "fields": {"name": "b", "id": {"sel": ":scope", "attr": "data-id"}}}
    pages = {"http://x/?page=1": '<div class="d" data-id="1"><b>A</b></div>',
             "http://x/?page=2": '<div class="d" data-id="2"><b>B</b></div>'}
    f = FakeFetcher(pages)
    dogs = ws.WebSite(site, f).scrape()
    assert [d["name"] for d in dogs] == ["A", "B"]
    assert len(f.calls) == 3  # page 3 was empty, so it stopped there


def test_webscrape_block_detection_and_bad_defs(tmp_path):
    assert ws._looks_blocked("<html><title>This site is temporarily unavailable | X</title>")
    assert ws._looks_blocked("<html><head><title>Access Denied</title></head>")
    assert not ws._looks_blocked("<html><title>Dogs - PAWS</title>")
    (tmp_path / "ok.json").write_text(_json.dumps({"name": "A", "urls": ["http://x"], "fields": {}}))
    (tmp_path / "bad.json").write_text("{not json")
    (tmp_path / "missing.json").write_text(_json.dumps({"name": "B"}))
    assert [s.name for s in ws.load_sites(tmp_path)] == ["A"]


def test_fetcher_raises_blocked_and_honours_robots(monkeypatch):
    f = ws.Fetcher(delay=0)

    class S:
        def get(self, url, **kw):
            if url.endswith("/robots.txt"):
                return FakeResp("User-agent: *\nDisallow: /private/\n")
            return FakeResp("<title>Access Denied</title>", 403)

    f._session = S()
    try:
        f.get("http://h.test/dogs")
        assert False
    except ws.BlockedError:
        pass
    try:
        f.get("http://h.test/private/x")
        assert False
    except PermissionError:
        pass


def test_webscrape_breed_cleanup_and_suffix():
    site = {"name": "B", "urls": ["http://x/"], "mode": "css", "breed_split": False, "items": ".d",
            "fields": {"name": "b", "id": {"sel": ":scope", "attr": "data-id"}, "breed": ".br",
                       "age": {"sel": ".ag", "suffix": " months"}}}
    page = '<div class="d" data-id="1"><b>Toby</b><i class="br">Mixed Breed, Large</i><i class="ag">54</i></div>' \
           '<div class="d" data-id="2"><b>Fia</b><i class="br">Terrier, Pit Bull</i></div>'
    toby, fia = ws.WebSite(site, FakeFetcher({"http://x/": page})).scrape()
    assert toby["breed_primary"] == "Mixed Breed" and toby["size"] == "large" and toby["age_months"] == 54
    assert fia["breed_primary"] == "Terrier, Pit Bull" and fia["breed_secondary"] is None


# ---- dedupe ----
from app import dedupe


def _dup_db():
    db = DB(":memory:")
    sl = make(source="shelterluv", source_id="s1", name="MOLLY", breed_primary="Shih Tzu", sex="female", age_months=36,
              age_text="3 years", org="Sunny Sky's Animal Rescue", color="White / Black", size="small", lat=47.2, lon=-122.3)
    aap = make(source="adoptapet", source_id="a1", name="Molly ", breed_primary="Shih Tzu", sex="female", age_months=38,
               age_text="3 yrs 2 mos", org="Sunny Sky's Animal Rescue", color="White", size="small", lat=47.2, lon=-122.3)
    return db, db.upsert_listing(sl)[0], db.upsert_listing(aap)[0]


def _canon(db):
    return {r["id"]: r["canonical_id"] for r in db.q("SELECT id, canonical_id FROM listings")}


def test_dedupe_merges_same_dog_across_sources():
    db, a, b = _dup_db()
    dedupe.run(db, fetch_photos=False)
    c = _canon(db)
    assert c[a] == c[b] == min(a, b)


def test_dedupe_keeps_different_dogs_apart():
    db, a, b = _dup_db()
    # same name but different sex / org / age: clearly a different dog
    c = db.upsert_listing(make(source="petfinder", source_id="p1", name="Molly", breed_primary="Labrador", sex="male",
                               age_months=100, org="Totally Other Rescue", lat=47.2, lon=-122.3))[0]
    dedupe.run(db, fetch_photos=False)
    canon = _canon(db)
    assert canon[c] == c and canon[a] == canon[b] == min(a, b)


def test_dedupe_never_merges_two_listings_from_one_source():
    db = DB(":memory:")
    ids = [db.upsert_listing(make(source="x", source_id=str(i), name="Bella", sex="female", org="R"))[0] for i in range(2)]
    ids.append(db.upsert_listing(make(source="y", source_id="1", name="Bella", sex="female", org="R"))[0])
    dedupe.run(db, fetch_photos=False)
    canon = _canon(db)
    assert len({canon[i] for i in ids}) == 2  # the y-listing joins one x-listing only


def test_dedupe_split_is_honoured_and_reversible():
    db, a, b = _dup_db()
    dedupe.run(db, fetch_photos=False)
    db.add_split(a, b)
    dedupe.run(db, fetch_photos=False)
    canon = _canon(db)
    assert canon[a] == a and canon[b] == b


def test_dedupe_scoring_pieces():
    assert dedupe.clean_name("Luna (bonded pair with Hope)") == "luna"
    assert dedupe.name_match("kane", "a boy named kane")
    assert not dedupe.name_match("bella", "luna")
    a = make(sex="male"); b = make(sex="female")
    assert dedupe.score_pair(a, b)[1] is True  # sex conflict vetoes
    assert dedupe.hamming("0000000000000000", "00000000000000ff") == 8


def test_dedupe_photo_hash_breaks_ties(monkeypatch):
    from PIL import Image
    import io
    def png(seed):
        im = Image.new("RGB", (40, 40))
        im.putdata([((x * seed) % 256, (x * 3 * seed) % 256, 90) for x in range(1600)])
        b = io.BytesIO(); im.save(b, "PNG"); return b.getvalue()
    assert dedupe.hamming(dedupe.dhash_bytes(png(7)), dedupe.dhash_bytes(png(7))) == 0
    # borderline pair (name + sex + weak org match, no age/breed): the photo decides
    db = DB(":memory:")
    base = dict(name="Pip", sex="female", org="Happy Paws Rescue", breed_primary=None, age_months=None, age_group=None,
                color=None, size=None, lat=None, lon=None, photos=["http://p/1"])
    a = db.upsert_listing(make(source="x", source_id="1", **base))[0]
    b = db.upsert_listing(make(source="y", source_id="1", **base))[0]
    img = png(7)
    class R:
        status_code = 200; content = img
    monkeypatch.setattr(dedupe.httpx, "get", lambda *a, **k: R())
    dedupe.run(db)
    assert _canon(db)[b] == a
    assert all(r["photo_hash"] for r in db.q("SELECT photo_hash FROM listings"))


# ---- statewide + regions ----
from app import poller


def test_statewide_search_and_region(monkeypatch):
    db = DB(":memory:")
    monkeypatch.setattr(geo, "geocode", lambda d, q: {"lat": 47.38, "lon": -122.23, "zip": "98031", "label": "Kent, WA 98031"})
    db.upsert_listing(make(source_id="1", name="Near"))
    db.upsert_listing(make(source_id="2", name="Spokane", city="Spokane", lat=47.66, lon=-117.43, zip="99201"))
    db.upsert_listing(make(source_id="3", name="Portland", state="OR", lat=45.5, lon=-122.68))
    names = lambda p: sorted(c["name"] for c in search.search(db, p))
    assert names({"radius": 50}) == ["Near"]
    assert names({"statewide": "1"}) == ["Near", "Spokane"]  # Portland is out of state
    db.save_search("all WA", {"location": "98031", "statewide": "1"})
    regions = poller.watch_regions(db)
    st = [r for r in regions if r.get("statewide")]
    assert st and st[0]["state"] == "WA" and st[0]["radius"] >= 250
    assert geo.in_region(st[0], {"label": "Spokane, WA 99201", "lat": 47.6, "lon": -117.4})
    assert not geo.in_region(st[0], {"label": "Portland, OR 97201", "lat": 45.5, "lon": -122.6})


def test_poll_runs_sources_in_parallel_and_isolates_failures(monkeypatch):
    import threading
    db = DB(":memory:")
    started, release = [], threading.Event()

    class Slow:
        name = "slow"
        def enabled(self): return True
        def fetch(self, region, db=None):
            started.append("slow"); release.wait(5); return []

    class Fast:
        name = "fast"
        def enabled(self): return True
        def fetch(self, region, db=None):
            started.append("fast"); release.set(); return [make(source="fast", source_id="1")]

    class Boom:
        name = "boom"
        def enabled(self): return True
        def fetch(self, region, db=None): raise RuntimeError("down")

    monkeypatch.setattr(poller, "SOURCES", [Slow(), Fast(), Boom()])
    monkeypatch.setattr(geo, "geocode", lambda d, q: {"lat": 47.38, "lon": -122.23, "zip": "98031", "label": "Kent, WA 98031"})
    assert poller.run_poll(db) == 1  # Fast could only finish while Slow was still waiting => parallel
    st = {s["source"]: s for s in db.statuses()}
    assert st["fast"]["state"] == "ok" and st["boom"]["state"] == "degraded" and "down" in st["boom"]["last_error"]


def test_placeholder_photos_are_ignored(monkeypatch):
    ph = "https://g.petango.com/shared/Photo-Not-Available-dog.gif"
    # Adopt-a-Pet's real photo URLs contain a *fallback* image parameter; they must be kept
    aap = "https://media.adoptapet.com/image/upload/d_Fallback-Photo_Dog-v3.png/c_fit,h_600/f_auto/1288587973"
    assert normalize.real_photos([ph, "http://x/real.jpg", aap]) == ["http://x/real.jpg", aap]
    db = DB(":memory:")
    monkeypatch.setattr(geo, "geocode", lambda d, q: {"lat": 47.38, "lon": -122.23, "zip": "98031", "label": "Kent, WA 98031"})
    db.upsert_listing(make(photos=[ph]))
    [card] = search.search(db, {"radius": 50})
    assert card["photos"] == [] and card["date_known"] is True


def test_adoptapet_firewall_challenge_never_blacklists(monkeypatch):
    html = (Path(__file__).parent / "fixtures" / "adoptapet_pet.html").read_text(encoding="utf-8", errors="replace")

    class Resp:
        def __init__(self, status, text="", headers=None):
            self.status_code, self.text, self.headers = status, text, headers or {}

    class FakeClient:
        def __init__(self, responses): self.responses, self.n = responses, 0
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, url):
            r = self.responses[min(self.n, len(self.responses) - 1)]; self.n += 1; return r

    def make_source(responses):
        src = adoptapet.AdoptAPet()
        src._entries = {"m": [("1", "kent", "WA", "husky-mix", "http://x/1"), ("2", "kent", "WA", "lab-mix", "http://x/2"),
                              ("3", "kent", "WA", "pug", "http://x/3"), ("4", "kent", "WA", "pug", "http://x/4"),
                              ("5", "kent", "WA", "pug", "http://x/5"), ("6", "kent", "WA", "pug", "http://x/6")]}
        monkeypatch.setattr(src, "_client", lambda: FakeClient(responses))
        monkeypatch.setattr(src, "_refresh_sitemaps", lambda c: None)
        return src

    monkeypatch.setattr(adoptapet.time, "sleep", lambda s: None)
    monkeypatch.setattr(geo, "geocode", lambda d, q: {"lat": 47.38, "lon": -122.23, "zip": "98031", "label": "Kent, WA 98031"})
    region = {"zip": "98031", "lat": 47.38, "lon": -122.23, "radius": 50}
    challenge = Resp(202, "", {"x-amzn-waf-action": "challenge"})

    # all challenged: raises (degraded), blacklists nothing, and enters cooldown
    db = DB(":memory:")
    src = make_source([challenge])
    try:
        src.fetch(region, db)
        assert False
    except RuntimeError as e:
        assert "rate limited" in str(e)
    assert db.known_ids("adoptapet") == set()
    try:
        src.fetch(region, db)
        assert False
    except RuntimeError as e:
        assert "slow down" in str(e)   # cooldown: no new requests at all

    # one good page then a challenge: keeps the dog, stops, and skips nothing
    db2 = DB(":memory:")
    src2 = make_source([Resp(200, html), challenge])
    got = src2.fetch(region, db2)
    assert [l["name"] for l in got] == ["Kira"]
    assert db2.known_ids("adoptapet") == set()  # (the caller upserts; challenged pages were NOT blacklisted)

    # a 200 page we cannot parse is an error, not a "skip"
    db3 = DB(":memory:")
    src3 = make_source([Resp(200, "<html>layout changed</html>")])
    try:
        src3.fetch(region, db3)
        assert False
    except RuntimeError:
        pass
    assert db3.known_ids("adoptapet") == set()


def test_shelterluv_secondary_breed_none_string():
    rec = {"uniqueId": "X-2", "name": "A", "species": "Dog", "adoptable": 1, "breed": "Terrier", "secondary_breed": "None"}
    l = shelterluv.parse(rec, "o", "99201", False)
    assert l["breed_secondary"] is None and l["mixed"] == 0


def test_newest_sort_and_listed_within_filter(monkeypatch):
    db = DB(":memory:")
    monkeypatch.setattr(geo, "geocode", lambda d, q: {"lat": 47.38, "lon": -122.23, "zip": "98031", "label": "Kent, WA 98031"})
    now = time.time()
    day = 86400
    db.upsert_listing(make(source_id="1", name="Today-dated", listed_at=now - 5))
    db.upsert_listing(make(source_id="2", name="Last-week", listed_at=now - 6 * day))
    db.upsert_listing(make(source_id="3", name="Month-old", listed_at=now - 30 * day))
    db.upsert_listing(make(source_id="4", name="Undated", listed_at=None))  # first_seen = now
    names = lambda p: [c["name"] for c in search.search(db, p)]
    # default: dogs with no listing date form a block at the top, then dated dogs newest first
    assert names({"radius": 50}) == ["Undated", "Today-dated", "Last-week", "Month-old"]
    assert [c["date_known"] for c in search.search(db, {"radius": 50})] == [False, True, True, True]
    # "undated last" moves that block to the bottom
    assert names({"radius": 50, "undated_last": "1"}) == ["Today-dated", "Last-week", "Month-old", "Undated"]
    # the option only affects the Newest sort
    assert names({"radius": 50, "sort": "closest", "undated_last": "1"}) == names({"radius": 50, "sort": "closest"})
    # "listed within" ignores undated dogs unless asked
    assert names({"radius": 50, "max_days": 7}) == ["Today-dated", "Last-week"]
    assert names({"radius": 50, "max_days": 7, "include_undated": "1"}) == ["Undated", "Today-dated", "Last-week"]
    assert names({"radius": 50, "max_days": 1}) == ["Today-dated"]


def test_merged_card_keeps_the_real_listing_date(monkeypatch):
    db = DB(":memory:")
    monkeypatch.setattr(geo, "geocode", lambda d, q: {"lat": 47.38, "lon": -122.23, "zip": "98031", "label": "Kent, WA 98031"})
    a = db.upsert_listing(make(source="x", source_id="1", name="Pal", listed_at=time.time() - 10 * 86400))[0]
    b = db.upsert_listing(make(source="y", source_id="1", name="Pal", listed_at=None))[0]
    db.set_canonicals({a: a, b: a})
    [card] = search.search(db, {"radius": 50})
    assert card["date_known"] is True and 9.9 < card["days_listed"] < 10.1


def test_initial_load_of_undated_source_sorts_as_unknown(monkeypatch):
    db = DB(":memory:")
    monkeypatch.setattr(geo, "geocode", lambda d, q: {"lat": 47.38, "lon": -122.23, "zip": "98031", "label": "Kent, WA 98031"})
    now = time.time()
    db.upsert_listing(make(source="undated-src", source_id="1", name="Old-timer", listed_at=None))   # seen in the first poll
    db.set_status("undated-src", True, count=1)                                                       # first poll completes
    time.sleep(0.01)
    db.upsert_listing(make(source="undated-src", source_id="2", name="Truly-new", listed_at=None))   # appears in a later poll
    db.upsert_listing(make(source="dated-src", source_id="1", name="Dated-3d", listed_at=now - 3 * 86400))
    cards = {c["name"]: c for c in search.search(db, {"radius": 50})}
    assert cards["Old-timer"]["age_unknown"] is True
    assert cards["Truly-new"]["age_unknown"] is False and cards["Dated-3d"]["age_unknown"] is False
    # undated block on top (the one first seen most recently first), dated dogs after; "undated last" flips it
    assert [c["name"] for c in search.search(db, {"radius": 50})] == ["Truly-new", "Old-timer", "Dated-3d"]
    assert [c["name"] for c in search.search(db, {"radius": 50, "undated_last": "1"})] == ["Dated-3d", "Truly-new", "Old-timer"]
    assert db.statuses()[0]["first_ok"] is not None


# ---- Petfinder manual import ----
from app.sources import petfinder as pf

PF_CARDS = """<html><body><div class="grid">
<div class="card"><a href="/dog/bella-12345678/wa/seattle/seattle-humane-wa123/"><img src="https://cdn.example/b.jpg"></a>
 <h3>Bella</h3><div>Labrador Retriever Mix</div><div>Young &bull; Female &bull; Medium</div><div>Seattle, WA</div><div>12 miles away</div></div>
<div class="card"><a href="/dog/max-87654321/wa/kent/kent-rescue-wa9/"><img data-src="//cdn.example/m.jpg"><span>Featured</span></a>
 <h3>Max</h3><div>Beagle</div><div>Adult &bull; Male &bull; Small</div><div>Kent, WA</div></div>
<div class="card"><a href="/dog/bella-12345678/wa/seattle/seattle-humane-wa123/">Bella again (name link)</a></div>
<a href="/cat/felix-111111/wa/kent/x/">a cat link that must be ignored</a>
</div></body></html>"""

PF_JSON = """<html><script type="application/json" id="data">{"animals":[
 {"id": 555, "name": "Rosie", "species": "Dog", "breeds": {"primary": "Pit Bull Terrier", "secondary": null, "mixed": true},
  "age": "Baby", "gender": "Female", "size": "Medium", "colors": {"primary": "Brindle"}, "published_at": "2026-09-20T10:00:00+0000",
  "photos": [{"medium": "http://i/m.jpg", "large": "http://i/l.jpg"}], "url": "https://www.petfinder.com/dog/rosie-555/wa/tacoma/org-wa1/",
  "contact": {"address": {"city": "Tacoma", "state": "WA", "postcode": "98402"}}, "description": "Transport available."},
 {"id": 556, "name": "Tom", "species": "Cat", "breeds": {"primary": "Tabby"}}]}</script></html>"""


def test_petfinder_parse_dog_cards():
    dogs = {d["name"]: d for d in pf.parse_page(PF_CARDS, "https://www.petfinder.com/search/dogs-for-adoption/us/wa/kent/")}
    assert set(dogs) == {"Bella", "Max"}   # duplicate link merged, cat ignored
    b = dogs["Bella"]
    assert b["source"] == "petfinder" and b["source_id"] == "12345678"
    assert b["breed_primary"] == "Labrador Retriever" and b["mixed"] == 1 and b["sex"] == "female"
    assert b["age_group"] == "young" and b["size"] == "medium" and b["city"] == "Seattle" and b["state"] == "WA"
    assert b["photos"] == ["https://cdn.example/b.jpg"] and b["url"].endswith("seattle-humane-wa123/")
    assert b["org"] == "Seattle Humane"
    assert dogs["Max"]["photos"] == ["https://cdn.example/m.jpg"] and dogs["Max"]["size"] == "small"


def test_petfinder_parse_embedded_json():
    [d] = pf.parse_page(PF_JSON)
    assert d["name"] == "Rosie" and d["source_id"] == "555" and d["zip"] == "98402" and d["color"] == "Brindle"
    assert d["breed_primary"] == "Pit Bull Terrier" and d["mixed"] == 1 and d["age_group"] == "puppy"
    assert d["photos"] == ["http://i/l.jpg"] and d["transport"] == 1 and d["listed_at"]


def test_petfinder_import_flow_and_routes(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from urllib.parse import urlencode
    from app import auth, importer
    monkeypatch.setattr(geo, "geocode", lambda d, q: {"lat": 47.38, "lon": -122.23, "zip": "98031", "label": "Kent, WA 98031"})
    monkeypatch.setattr(importer, "LAST_PAGE", Path(__file__).parent / "fixtures" / "_last_import_test.html")
    db = DB(":memory:")                       # never touch the real database
    app_ = FastAPI()
    app_.middleware("http")(auth.auth_middleware)
    app_.include_router(importer.make_router(db))
    assert TestClient(app_).get("/import").status_code == 401   # non-local callers are refused without the API token
    client = TestClient(app_, client=("127.0.0.1", 50000))      # the user's own browser
    token = importer._token(db)
    # the instructions page carries a bookmarklet with the token
    page = client.get("/import").text
    assert "javascript:" in page and token in page
    # form post (what the bookmarklet does): wrong token rejected, right token imports
    body = urlencode({"token": "nope", "url": "https://www.petfinder.com/x", "html": PF_CARDS})
    h = {"Content-Type": "application/x-www-form-urlencoded"}
    assert client.post("/import/petfinder", content=body, headers=h).status_code == 403
    ok = client.post("/import/petfinder", content=urlencode({"token": token, "url": "https://www.petfinder.com/x", "html": PF_CARDS}), headers=h)
    assert ok.status_code == 200 and "Imported 2 dogs" in ok.text
    r = client.post("/api/import/petfinder", json={"html": PF_JSON, "url": "https://www.petfinder.com/"}).json()
    assert r["found"] == 1
    assert pf.Petfinder().manual is True and db.statuses()[0]["source"] == "petfinder"
    # a page with no dogs says so instead of failing
    empty = client.post("/import/petfinder", content=urlencode({"token": token, "html": "<html>" + "x" * 300}), headers=h)
    assert "No dogs recognized" in empty.text
    # imported dogs expire if never re-imported
    assert db.q("SELECT COUNT(*) AS n FROM listings WHERE source='petfinder' AND status='active'")[0]["n"] == 3
    db.run("UPDATE listings SET last_seen = last_seen - ? WHERE source='petfinder'", ((pf.EXPIRE_DAYS + 1) * 86400,))
    db.expire_stale("petfinder", pf.EXPIRE_DAYS)
    assert db.q("SELECT COUNT(*) AS n FROM listings WHERE source='petfinder' AND status='active'")[0]["n"] == 0
    (Path(__file__).parent / "fixtures" / "_last_import_test.html").unlink(missing_ok=True)


def test_shelterluv_identical_secondary_breed_dropped():
    rec = {"uniqueId": "X-3", "name": "A", "species": "Dog", "adoptable": 1,
           "breed": "Mixed Breed (Medium)", "secondary_breed": "Mixed Breed (Medium)"}
    l = shelterluv.parse(rec, "o", "99201", False)
    assert l["breed_secondary"] is None and l["mixed"] == 1


def test_petfinder_real_card_layout():
    """Three genuine cards captured from petfinder.com (UUID ids, PFPetDetailCard markup)."""
    html = (Path(__file__).parent / "fixtures" / "petfinder_cards.html").read_text(encoding="utf-8")
    dogs = {d["name"]: d for d in pf.parse_page(html, "https://www.petfinder.com/search/dogs-for-adoption/us/wa/seattle/")}
    assert set(dogs) == {"Playful Parker", "Dolly", "Grace"}
    p = dogs["Playful Parker"]
    assert p["source"] == "petfinder" and p["source_id"] == "2cb759fc-d418-4b52-80a3-239ae7d82ea8"
    assert p["breed_primary"] == "American Staffordshire Terrier" and p["breed_secondary"] == "French Bulldog" and p["mixed"] == 1
    assert p["sex"] == "male" and p["age_group"] == "puppy" and p["city"] == "Seattle" and p["state"] == "WA"
    assert p["org"] == "Dreaming of Furever Animal Rescue" and "Good with: Dogs, Kids" in p["description"]
    assert p["photos"][0].startswith("https://dbw3zep4prcju.cloudfront.net/animal/2cb759fc") and p["url"].endswith("/details/")
    assert dogs["Dolly"]["breed_primary"] == "Mixed Breed" and dogs["Dolly"]["sex"] == "female"
    assert dogs["Grace"]["breed_secondary"] == "Blue Heeler"
    # a mangled bullet (U+FFFD) must not break the age/sex line
    bad = html.replace("•", "�")
    assert {d["name"] for d in pf.parse_page(bad)} == set(dogs)


def test_ui_is_served_fresh():
    from fastapi.testclient import TestClient
    from app import main
    c = TestClient(main.app, client=("127.0.0.1", 50000))
    page = c.get("/")
    assert page.status_code == 200 and page.headers["cache-control"] == "no-cache"
    assert "/static/app.js?v=" in page.text and "/static/styles.css?v=" in page.text
    js = c.get("/static/app.js")
    assert js.status_code == 200 and js.headers["cache-control"] == "no-cache" and "undated_last" in js.text


# ---- phone access: token auth, pairing, safe startup ----
TOKEN = "t" * 32


def _auth_app(monkeypatch):
    from fastapi import FastAPI
    from app import auth, config, pairing
    monkeypatch.setattr(config, "API_TOKEN", TOKEN)
    monkeypatch.setattr(config, "REQUIRE_TOKEN", True)   # these tests are about token mode
    a = FastAPI()
    a.middleware("http")(auth.auth_middleware)
    a.include_router(pairing.router)

    @a.get("/x")
    def x():
        return {"ok": True}
    return a


def test_token_auth_for_remote_clients(monkeypatch):
    from fastapi.testclient import TestClient
    a = _auth_app(monkeypatch)
    phone = TestClient(a, client=("100.64.0.9", 40000))
    assert phone.get("/x").status_code == 401                                          # no token
    assert phone.get("/x", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert phone.get("/x?token=" + "x" * 32).status_code == 401
    h = phone.get("/x", headers={"Authorization": "Bearer " + TOKEN})
    assert h.status_code == 200 and "af_token" in h.headers.get("set-cookie", "")       # header works, sets a cookie
    assert phone.get("/x").status_code == 200                                          # ...then the cookie alone works
    fresh = TestClient(a, client=("100.64.0.9", 40000))
    assert fresh.get("/x?token=" + TOKEN).status_code == 200                           # first load via ?token=
    html = TestClient(a, client=("192.168.1.5", 1)).get("/x", headers={"accept": "text/html"})
    assert html.status_code == 401 and "Access denied" in html.text                    # friendly page for browsers


def test_local_requests_trusted_but_proxied_ones_are_not(monkeypatch):
    from fastapi.testclient import TestClient
    a = _auth_app(monkeypatch)
    me = TestClient(a, client=("127.0.0.1", 5))
    assert me.get("/x").status_code == 200
    # a local proxy (tailscale serve, a tunnel) adds forwarding headers: it must not get the local free pass
    assert me.get("/x", headers={"X-Forwarded-For": "100.64.0.9"}).status_code == 401
    assert me.get("/x", headers={"X-Forwarded-For": "100.64.0.9", "Authorization": "Bearer " + TOKEN}).status_code == 200


def test_pair_page_is_local_only(monkeypatch):
    from fastapi.testclient import TestClient
    from app import pairing
    a = _auth_app(monkeypatch)
    monkeypatch.setattr(pairing, "tailscale_ip", lambda: "100.64.0.5")
    monkeypatch.setattr(pairing, "lan_ip", lambda: "10.0.0.176")
    page = TestClient(a, client=("127.0.0.1", 5)).get("/pair")
    assert page.status_code == 200 and TOKEN in page.text and "100.64.0.5" in page.text and "adoptionfinder://connect" in page.text
    assert "<svg" in page.text                                                           # QR code rendered
    remote = TestClient(a, client=("100.64.0.9", 5)).get("/pair", headers={"Authorization": "Bearer " + TOKEN})
    assert remote.status_code == 404                                                     # never shows the token to the network
    link = pairing.pairing_link("100.64.0.5", 8000, TOKEN)
    assert link == f"adoptionfinder://connect?url=http%3A%2F%2F100.64.0.5%3A8000&token={TOKEN}"


def test_refuses_to_listen_on_network_without_token(monkeypatch):
    import pytest
    from app import __main__ as entry, config
    started = []
    monkeypatch.setattr(entry.uvicorn, "run", lambda *a, **k: started.append(k))
    monkeypatch.setattr(config, "HOST", "0.0.0.0")
    monkeypatch.setattr(config, "REQUIRE_TOKEN", True)
    monkeypatch.setattr(config, "API_TOKEN", "short")
    with pytest.raises(SystemExit):
        entry.main()
    assert not started
    monkeypatch.setattr(config, "API_TOKEN", TOKEN)
    entry.main()
    assert started and started[0]["host"] == "0.0.0.0"
    started.clear()
    monkeypatch.setattr(config, "HOST", "127.0.0.1")
    monkeypatch.setattr(config, "API_TOKEN", "")
    entry.main()
    assert started   # local-only needs no token


def test_phone_setup_env_editing():
    from app import phone_setup
    t = "RESCUEGROUPS_API_KEY=abc\nAPI_TOKEN=\nHOST=127.0.0.1\n"
    t = phone_setup.set_key(t, "HOST", "0.0.0.0")
    t = phone_setup.set_key(t, "API_TOKEN", "tok")
    t = phone_setup.set_key(t, "NEWKEY", "1")
    assert t == "RESCUEGROUPS_API_KEY=abc\nAPI_TOKEN=tok\nHOST=0.0.0.0\nNEWKEY=1\n"


def test_apk_download_is_public_but_nothing_else_is(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from app import pairing
    fake = tmp_path / "AdoptionFinder.apk"
    fake.write_bytes(b"PK\x03\x04fake-apk-bytes")
    monkeypatch.setattr(pairing, "APK_PATH", fake)
    a = _auth_app(monkeypatch)
    stranger = TestClient(a, client=("192.168.1.50", 40000))          # another device on the Wi-Fi, no token
    r = stranger.get("/apk")
    assert r.status_code == 200 and r.content == fake.read_bytes()
    assert r.headers["content-type"] == "application/vnd.android.package-archive"
    assert "AdoptionFinder.apk" in r.headers["content-disposition"]
    assert stranger.get("/x").status_code == 401                      # everything else still needs the token
    assert stranger.get("/pair").status_code == 401
    assert stranger.post("/apk").status_code in (401, 405)            # only GET is exempt
    fake.unlink()
    assert stranger.get("/apk").status_code == 404                    # missing file: clean error, still no secrets


def test_pair_page_offers_apk_install(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from app import pairing
    fake = tmp_path / "a.apk"; fake.write_bytes(b"x")
    monkeypatch.setattr(pairing, "APK_PATH", fake)
    monkeypatch.setattr(pairing, "tailscale_ip", lambda: None)
    monkeypatch.setattr(pairing, "lan_ip", lambda: "10.0.0.176")
    a = _auth_app(monkeypatch)
    page = TestClient(a, client=("127.0.0.1", 5)).get("/pair").text
    assert "Install the app on the phone" in page and "http://10.0.0.176:8000/apk" in page and "<svg" in page


def test_home_network_mode_needs_no_token_on_private_networks_only(monkeypatch):
    from fastapi.testclient import TestClient
    from app import config
    a = _auth_app(monkeypatch)
    monkeypatch.setattr(config, "REQUIRE_TOKEN", False)
    get = lambda ip, **kw: TestClient(a, client=(ip, 40000)).get("/x", **kw).status_code
    for private in ("192.168.1.20", "10.0.0.55", "172.16.4.9", "100.64.0.9", "169.254.1.1"):
        assert get(private) == 200, private                         # home LAN / Tailscale: just works
    for public in ("8.8.8.8", "203.0.113.7", "93.184.216.34"):
        assert get(public) == 401, public                           # the internet: still needs the token
    assert get("8.8.8.8", headers={"Authorization": "Bearer " + TOKEN}) == 200
    # a request relayed by a local proxy never gets the no-token pass, whatever address it claims
    assert get("192.168.1.20", headers={"X-Forwarded-For": "8.8.8.8"}) == 401
    assert TestClient(a, client=("127.0.0.1", 5)).get("/x", headers={"X-Forwarded-For": "192.168.1.20"}).status_code == 401
    # token mode is unchanged
    monkeypatch.setattr(config, "REQUIRE_TOKEN", True)
    assert get("192.168.1.20") == 401


def test_home_network_mode_startup_and_pages(monkeypatch):
    import pytest
    from fastapi.testclient import TestClient
    from app import __main__ as entry, config, pairing
    started = []
    monkeypatch.setattr(entry.uvicorn, "run", lambda *a, **k: started.append(k))
    monkeypatch.setattr(config, "HOST", "0.0.0.0")
    monkeypatch.setattr(config, "REQUIRE_TOKEN", False)
    monkeypatch.setattr(config, "API_TOKEN", "")
    entry.main()
    assert started                                                  # no token needed to start in home-network mode
    a = _auth_app(monkeypatch)
    monkeypatch.setattr(config, "REQUIRE_TOKEN", False)
    monkeypatch.setattr(config, "API_TOKEN", "")
    monkeypatch.setattr(config, "HOST", "0.0.0.0")
    monkeypatch.setattr(pairing, "tailscale_ip", lambda: None)
    monkeypatch.setattr(pairing, "lan_ip", lambda: "10.0.0.176")
    page = TestClient(a, client=("127.0.0.1", 5)).get("/pair").text
    assert "No pairing and no token" in page and "http://10.0.0.176:8000/apk" in page and "Token:" not in page
    # the import pages carry a secret for the PC's own browser: never served to other devices, even without a token
    from app import importer
    from fastapi import FastAPI
    b = FastAPI()
    b.middleware("http")(__import__("app.auth", fromlist=["x"]).auth_middleware)
    b.include_router(importer.make_router(DB(":memory:")))
    lan = TestClient(b, client=("192.168.1.20", 5))
    assert lan.get("/import").status_code == 404
    assert lan.post("/api/import/petfinder", json={"html": "x" * 300}).status_code == 404
    assert lan.post("/import/petfinder", content=b"token=x&html=y").status_code == 404


def test_source_filter_and_counts(monkeypatch):
    db = DB(":memory:")
    monkeypatch.setattr(geo, "geocode", lambda d, q: {"lat": 47.38, "lon": -122.23, "zip": "98031", "label": "Kent, WA 98031"})
    a1 = db.upsert_listing(make(source="alpha", source_id="1", name="Solo-A"))[0]
    db.upsert_listing(make(source="beta", source_id="1", name="Solo-B"))
    db.upsert_listing(make(source="beta", source_id="2", name="Solo-B2"))
    m1 = db.upsert_listing(make(source="alpha", source_id="3", name="Merged"))[0]
    m2 = db.upsert_listing(make(source="gamma", source_id="3", name="Merged"))[0]
    db.set_canonicals({m1: m1, m2: m1})                               # one card made of alpha + gamma
    names = lambda p, **kw: sorted(c["name"] for c in search.search(db, {"radius": 50, **p}, **kw))
    counts = {}
    assert names({}, counts=counts) == ["Merged", "Solo-A", "Solo-B", "Solo-B2"]
    assert counts == {"alpha": 2, "beta": 2, "gamma": 1}              # the merged card counts once per source it has
    assert names({"hide_sources": "beta"}) == ["Merged", "Solo-A"]
    assert names({"hide_sources": "alpha"}) == ["Merged", "Solo-B", "Solo-B2"]          # merged card still has gamma
    assert names({"hide_sources": "alpha|gamma"}) == ["Solo-B", "Solo-B2"]               # all of its sources hidden
    assert names({"hide_sources": "alpha|beta|gamma"}) == []
    assert names({"hide_sources": "nonexistent"}) == names({})                           # unknown names are harmless
    c2 = {}
    names({"hide_sources": "beta"}, counts=c2)
    assert c2 == counts                                                                  # counts ignore the source filter itself
    only_merged = search.search(db, {"radius": 50, "hide_sources": "alpha"})
    assert len([c for c in only_merged if c["name"] == "Merged"][0]["sources"]) == 2     # the card keeps all its sources


def test_dogs_api_returns_source_counts(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    monkeypatch.setattr(geo, "geocode", lambda d, q: {"lat": 47.38, "lon": -122.23, "zip": "98031", "label": "Kent, WA 98031"})
    monkeypatch.setattr(main, "db", DB(":memory:"))        # never the real database
    main.db.upsert_listing(make(source="alpha", source_id="1"))
    r = TestClient(main.app, client=("127.0.0.1", 5)).get("/api/dogs", params={"radius": 50, "hide_sources": "zzz"}).json()
    assert r["source_counts"] == {"alpha": 1} and r["count"] == 1


def test_phone_setup_defaults_to_home_network_mode_and_never_flips_back(monkeypatch, tmp_path):
    from app import phone_setup
    env = tmp_path / ".env"
    env.write_text("RESCUEGROUPS_API_KEY=abc\nAPI_TOKEN=\nHOST=127.0.0.1\nREQUIRE_TOKEN=1\n", encoding="utf-8")
    monkeypatch.setattr(phone_setup, "ENV", env)
    monkeypatch.setattr(phone_setup.sys, "argv", ["phone_setup"])                 # no flag: home-network mode
    phone_setup.main()
    t = env.read_text(encoding="utf-8")
    assert "REQUIRE_TOKEN=0" in t and "HOST=0.0.0.0" in t and "RESCUEGROUPS_API_KEY=abc" in t
    phone_setup.main()                                                           # running it again changes nothing
    assert env.read_text(encoding="utf-8") == t
    monkeypatch.setattr(phone_setup.sys, "argv", ["phone_setup", "--token"])      # token mode only when asked
    phone_setup.main()
    t2 = env.read_text(encoding="utf-8")
    assert "REQUIRE_TOKEN=1" in t2 and len(t2.split("API_TOKEN=")[1].split("\n")[0]) >= 20
