"""Petango widgets, 24Pet Connect, Oregon Humane and the extra Shelterluv rescues, tested on real captured pages."""
import re
from pathlib import Path

from app import geo, normalize
from app.db import DB
from app.sources import oregonhumane, pet24, petango, shelterluv

FX = Path(__file__).parent / "fixtures"
W1 = "https://ws.petango.com/webservices/adoptablesearch/wsAdoptableAnimals.aspx?species=dog&authkey=KEY1"
W2 = "https://ws.petango.com/webservices/adoptablesearch/wsAdoptableAnimals2.aspx?species=All&authkey=KEY2"


def read(name):
    return (FX / name).read_text(encoding="utf-8")


def test_age_shorthand():
    assert normalize.parse_age_months("6y 5m") == 77 and normalize.parse_age_months("1y") == 12
    assert 2.0 < normalize.parse_age_months("9w") < 2.2 and normalize.parse_age_months("3m") == 3
    assert normalize.parse_age_months("1 year 11 months") == 23 and normalize.parse_age_months("12 years 1 month") == 145
    assert normalize.parse_age_months("3 miles away") is None and normalize.parse_age_months("Puppy") is None


def test_petango_widget_parse():
    dogs = {d["name"]: d for d in petango.parse(read("petango_widget1.html"), "Greenhill", "97402", W1)}
    a = dogs["Apollo"]
    assert a["source"] == "petango" and a["source_id"] == "60870723" and a["org"] == "Greenhill" and a["zip"] == "97402"
    assert a["breed_primary"] == "Mixed Breed" and a["mixed"] == 1 and a["size"] == "large"      # "Mixed Breed, Large (over 44 lbs...)/Mix"
    assert a["sex"] == "male" and a["age_months"] == 23 and a["photos"][0].startswith("https://g.petango.com/photos/")
    assert a["url"] == "https://ws.petango.com/webservices/adoptablesearch/wsAdoptableAnimalDetails.aspx?id=60870723&css=&authkey=KEY1"
    assert dogs["Bohemian Rhapsody"]["age_months"] == 2 and dogs["Azure"]["sex"] == "female"
    # second widget flavour: cats are dropped, a coat description in the breed is stripped, details link follows the flavour
    dogs2 = {d["name"]: d for d in petango.parse(read("petango_widget2.html"), "Wenatchee", "98801", W2)}
    assert "Rocky" in dogs2 and all(d["source_id"] != "42861889" for d in dogs2.values())        # the fixture's cat (id 42861889)
    r = dogs2["Rocky"]
    assert r["breed_primary"] == "Chihuahua" and r["mixed"] == 1 and r["breed_secondary"] is None and r["age_months"] == 145
    assert r["url"].startswith("https://ws.petango.com/webservices/adoptablesearch/wsAdoptableAnimalDetails2.aspx?id=55212936")


def test_24petconnect_parse_and_total():
    html = read("pet24_results.html")
    assert pet24.total_animals(html) == 38
    dogs = {d["name"]: d for d in pet24.parse(html, "Tri-Cities", "99301", "PP6628")}
    assert set(dogs) == {"Rambo", "Rocky", "Paco"}
    r = dogs["Rambo"]
    assert r["source"] == "24petconnect" and r["source_id"] == "49485041"
    assert r["breed_primary"] == "German Shepherd Dog" and r["mixed"] == 1                      # "German Shepherd Dog mix"
    assert r["sex"] == "male" and r["age_months"] == 72 and r["age_group"] == "adult"
    assert r["listed_at"] == 1788307200.0                                                         # "Brought to the shelter: 2026.09.02"
    assert dogs["Paco"]["breed_primary"] == "Alaskan Husky" and r["photos"][0].startswith("https://g.petango.com/photos/")


def test_oregon_humane_parse():
    dogs = {d["name"]: d for d in oregonhumane.parse(read("ohs_cards.html"))}
    assert set(dogs) == {"Frankie", "Bella", "Molly", "Gilbert"}                                  # the fixture's cat is dropped
    f = dogs["Frankie"]
    assert f["source"] == "oregonhumane" and f["source_id"] == "346124" and f["url"].endswith("/adopt/details/346124")
    assert f["breed_primary"] == "Australian Cattledog" and f["mixed"] == 1                       # breed code looked up in the page's own list, + Mix
    assert f["sex"] == "male" and f["size"] == "medium" and f["state"] == "OR" and f["zip"] == "97211"
    assert f["org"] == "Oregon Humane Society (Portland)" and f["photos"][0].startswith("https://oregonhumane.shelterbuddy.com/")
    m = dogs["Molly"]
    assert (m["breed_primary"], m["breed_secondary"]) == ("Australian Shepherd", "Great Pyrenees") and m["size"] == "small"
    assert 2.0 < m["age_months"] < 2.2 and m["age_group"] == "puppy"                              # "9w", not "0 months"
    assert dogs["Bella"]["breed_primary"] == "Mixed Breed"                                        # code the page doesn't list -> mixed breed
    assert dogs["Gilbert"]["zip"] == "97301" and dogs["Gilbert"]["org"].endswith("(Salem)")        # the Salem campus
    assert [oregonhumane.size_from_weight(w) for w in ("11.3", "24.9", "25", "59.9", "60", "74.3", "x")] == \
        ["small", "small", "medium", "medium", "large", "large", None]


def _geo(monkeypatch):
    spots = {"97402": (44.0, -123.1, "Eugene, OR 97402"), "98801": (47.4, -120.3, "Wenatchee, WA 98801"),
             "83852": (48.3, -116.5, "Ponderay, ID 83852"), "99301": (46.2, -119.1, "Pasco, WA 99301"),
             "97211": (45.6, -122.7, "Portland, OR 97211"), "97301": (44.9, -123.0, "Salem, OR 97301")}

    def fake(db, q):
        if q in spots:
            lat, lon, label = spots[q]
            return {"lat": lat, "lon": lon, "zip": q, "label": label}
        return None
    monkeypatch.setattr(geo, "geocode", fake)


def test_sources_only_ask_for_organizations_inside_the_search_area(monkeypatch):
    _geo(monkeypatch)
    asked = []
    monkeypatch.setattr(petango, "_get", lambda url: asked.append(url) or read("petango_widget1.html"))
    monkeypatch.setattr(petango.time, "sleep", lambda s: None)
    db = DB(":memory:")
    near_eugene = {"zip": "97402", "lat": 44.0, "lon": -123.1, "radius": 50}
    dogs = petango.Petango().fetch(near_eugene, db)
    assert len(asked) == 1 and "KEY" not in asked[0] and "x092s378" in asked[0]                  # only Greenhill, 1 request
    assert dogs and {d["state"] for d in dogs} == {"OR"} and dogs[0]["city"] == "Eugene" and dogs[0]["lat"] == 44.0
    seattle = {"zip": "98031", "lat": 47.38, "lon": -122.23, "radius": 20}
    asked.clear()
    assert petango.Petango().fetch(seattle, db) == [] and asked == []                             # nothing in range: no requests at all
    # whole-state search for Oregon asks only the Oregon shelter
    asked.clear()
    petango.Petango().fetch({"zip": "97201", "lat": 45.5, "lon": -122.7, "radius": 250, "statewide": True, "state": "OR"}, db)
    assert len(asked) == 1


def test_24petconnect_pages_by_offset_until_the_total(monkeypatch):
    _geo(monkeypatch)
    one = re.search(r'<div class="gridResult".*?</div>\s*</div>\s*(?=<div class="gridResult"|</body>)', read("pet24_results.html"), re.S).group(0)

    def page(first, count, total):
        cards = "".join(one.replace("49485041", str(500000 + first + n)) for n in range(count))
        return f"<html><body><h3>Animals: {first + 1} - {first + count} of {total}</h3>{cards}</body></html>"

    served, calls = {0: page(0, 30, 38), 30: page(30, 8, 38)}, []

    class R:
        status_code = 200

        def __init__(self, text):
            self.text = text

        def raise_for_status(self):
            pass

    def fake_get(url, params=None, **kw):
        calls.append(params["index"])
        return R(served.get(params["index"], "<html><body></body></html>"))

    monkeypatch.setattr(pet24.httpx, "get", fake_get)
    monkeypatch.setattr(pet24.time, "sleep", lambda s: None)
    out = pet24.Pet24().fetch({"zip": "99301", "lat": 46.2, "lon": -119.1, "radius": 50}, DB(":memory:"))
    assert calls == [0, 30]                       # offsets 0 and 30, then stops: that page ends at the total of 38
    assert len(out) == 38 and len({d["source_id"] for d in out}) == 38
    assert {d["state"] for d in out} == {"WA"} and out[0]["city"] == "Pasco"


def test_oregon_humane_fetch_filters_campuses_by_distance(monkeypatch):
    _geo(monkeypatch)
    monkeypatch.setattr(oregonhumane.httpx, "get", lambda *a, **k: type("R", (), {"status_code": 200, "text": read("ohs_cards.html"), "raise_for_status": lambda self: None})())
    db = DB(":memory:")
    portland_only = {"zip": "97211", "lat": 45.6, "lon": -122.7, "radius": 20}                 # Salem is ~45 miles away
    names = {d["name"] for d in oregonhumane.OregonHumane().fetch(portland_only, db)}
    assert names == {"Frankie", "Bella", "Molly"}
    both = {"zip": "97211", "lat": 45.6, "lon": -122.7, "radius": 100}
    assert len(oregonhumane.OregonHumane().fetch(both, db)) == 4
    seattle = {"zip": "98031", "lat": 47.38, "lon": -122.23, "radius": 20}
    assert oregonhumane.OregonHumane().fetch(seattle, db) == []                                 # out of range: nothing requested


def test_shelterluv_orgs_have_a_state_from_their_location(monkeypatch):
    ids = [o[0] for o in shelterluv.ORGS]
    assert len(ids) == len(set(ids))                                                            # no rescue listed twice
    for needed in (35916, 42542, 10355, 29277, 17653, 38292, 16072, 13962):                    # the Oregon / Idaho / Spokane additions
        assert needed in ids
    assert all(len(o[2]) == 5 and o[2].isdigit() for o in shelterluv.ORGS)
    _geo(monkeypatch)
    spots = {"83333": (43.5, -114.3, "Hailey, ID 83333")}
    monkeypatch.setattr(geo, "geocode", lambda d, q: ({"lat": spots[q][0], "lon": spots[q][1], "zip": q, "label": spots[q][2]} if q in spots else None))
    monkeypatch.setattr(shelterluv, "ORGS", [(17653, "Mountain Humane", "83333", False)])
    monkeypatch.setattr(shelterluv.time, "sleep", lambda s: None)

    class R:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"animals": [{"uniqueId": "IDMH-A-1", "name": "Pip", "species": "Dog", "adoptable": 1, "breed": "Husky", "sex": "Male",
                                 "birthday": "1700000000", "photos": {}}]}

    class C:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, url, params=None): return R()

    monkeypatch.setattr(shelterluv.httpx, "Client", C)
    out = shelterluv.Shelterluv().fetch({"zip": "83333", "lat": 43.5, "lon": -114.3, "radius": 50}, DB(":memory:"))
    assert len(out) == 1 and out[0]["state"] == "ID" and out[0]["city"] == "Hailey"            # not hard-coded to WA any more
