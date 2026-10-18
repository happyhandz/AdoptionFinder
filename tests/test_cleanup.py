"""The removed-dog check: what it removes, what it refuses to remove, when it runs, and who can start it."""
import json
import time
import time as _t
_real_sleep = _t.sleep      # poller.time is the same module: patching its sleep patches ours too
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler

from app import geo, poller
from app.db import DB


def dog(source, sid, name="Rex", **kw):
    base = {"source": source, "source_id": sid, "url": "u", "name": name, "breed_primary": "Beagle", "breed_secondary": None,
            "mixed": 0, "color": None, "age_group": "adult", "age_text": "4 years", "age_months": 48, "sex": "male",
            "size": "medium", "description": "", "photos": ["p.jpg"], "city": "Kent", "state": "WA", "zip": "98031",
            "lat": 47.38, "lon": -122.23, "org": "Org", "transport": 0, "listed_at": time.time()}
    base.update(kw)
    return base


class FakeSource:
    """Returns whatever is in `.dogs` (a list of source ids); `.boom` makes it fail."""
    manual = False

    def __init__(self, name, ids=()):
        self.name, self.ids, self.boom = name, list(ids), False

    def enabled(self):
        return True

    def fetch(self, region, db=None):
        if self.boom:
            raise RuntimeError("site is down")
        return [dog(self.name, i, name=f"Dog{i}") for i in self.ids]


def setup(monkeypatch, *sources):
    monkeypatch.setattr(poller, "SOURCES", list(sources))
    monkeypatch.setattr(geo, "geocode", lambda d, q: {"lat": 47.38, "lon": -122.23, "zip": "98031", "label": "Kent, WA 98031"})
    monkeypatch.setattr(poller.time, "sleep", lambda s: None)
    return DB(":memory:")


def active(db, source):
    return {r["source_id"] for r in db.q("SELECT source_id FROM listings WHERE source=? AND status='active'", (source,))}


def run_cleanup(db, trigger="manual"):
    assert poller.start_cleanup(db, trigger) is True
    for _ in range(300):
        if not poller.cleanup_snapshot()["running"]:
            break
        _real_sleep(0.02)
    return poller.cleanup_snapshot()


def test_a_normal_refresh_never_removes_dogs(monkeypatch):
    src = FakeSource("s", ["1", "2", "3"])
    db = setup(monkeypatch, src)
    poller.run_poll(db)
    assert active(db, "s") == {"1", "2", "3"}
    src.ids = ["1"]                                       # 2 and 3 are no longer listed
    for _ in range(3):
        poller.run_poll(db)                               # ...but refreshing only adds and refreshes
    assert active(db, "s") == {"1", "2", "3"}


def test_the_cleanup_check_removes_dogs_no_longer_listed(monkeypatch):
    src, other = FakeSource("s", ["1", "2", "3", "4", "5", "6"]), FakeSource("o", ["a", "b"])
    db = setup(monkeypatch, src, other)
    poller.run_poll(db)
    src.ids = ["1", "2", "3", "4", "9"]                   # 5 and 6 are gone, 9 is new, other shelter unchanged
    _real_sleep(0.01)
    st = run_cleanup(db)
    assert st["status"] == "done" and st["removed"] == 2 and not st["running"]
    assert active(db, "s") == {"1", "2", "3", "4", "9"}   # 9 was added by the check; 5 and 6 removed
    assert active(db, "o") == {"a", "b"}
    assert "Removed 2 dogs" in st["message"]
    last = json.loads(db.get_setting("cleanup_last"))
    assert last["removed"] == 2 and last["trigger"] == "manual"
    assert last["sources"]["s"] == {"before": 6, "fetched": 5, "removed": 2, "status": "ok", "note": ""}
    assert last["sources"]["o"]["removed"] == 0
    # a removed dog that is listed again comes back at the next refresh
    src.ids = ["1", "2", "3", "4", "9", "5"]
    poller.run_poll(db)
    assert "5" in active(db, "s")


def test_a_source_that_returns_too_little_is_not_trusted(monkeypatch):
    big, tiny = FakeSource("big", [str(i) for i in range(10)]), FakeSource("tiny", ["x", "y", "z"])
    db = setup(monkeypatch, big, tiny)
    poller.run_poll(db)
    big.ids = ["0", "1"]                                  # 2 of 10: looks like a broken page, not 8 adoptions
    tiny.ids = []                                         # a 3-dog shelter really can empty out
    st = run_cleanup(db)
    assert len(active(db, "big")) == 10 and st["sources"]["big"]["status"] == "skipped"
    assert "incomplete" in st["sources"]["big"]["note"] and st["sources"]["big"]["removed"] == 0
    assert active(db, "tiny") == set() and st["sources"]["tiny"]["removed"] == 3
    assert "skipped for safety" in st["message"] and st["removed"] == 3
    big.ids = [str(i) for i in range(6)]                  # 6 of 10 = 60% kept: believable, so the 4 missing are removed
    st = run_cleanup(db)
    assert active(db, "big") == {str(i) for i in range(6)}


def test_a_failing_source_loses_nothing_and_others_still_run(monkeypatch):
    bad, good = FakeSource("bad", ["1", "2", "3", "4", "5"]), FakeSource("good", ["a", "b", "c", "d", "e"])
    db = setup(monkeypatch, bad, good)
    poller.run_poll(db)
    bad.boom = True
    good.ids = ["a", "b", "c", "d"]
    st = run_cleanup(db)
    assert st["sources"]["bad"]["status"] == "error" and "site is down" in st["sources"]["bad"]["note"]
    assert len(active(db, "bad")) == 5                    # nothing removed from the broken source
    assert active(db, "good") == {"a", "b", "c", "d"}     # the healthy one was cleaned
    assert db.statuses() and {s["source"]: s["state"] for s in db.statuses()} == {"bad": "degraded", "good": "ok"}


def test_manual_sources_like_petfinder_are_left_to_their_own_expiry(monkeypatch):
    pf = FakeSource("petfinder", [])
    pf.manual = True
    db = setup(monkeypatch, pf)
    db.upsert_listing(dog("petfinder", "p1"))
    db.upsert_listing(dog("petfinder", "p2"))
    st = run_cleanup(db)
    assert active(db, "petfinder") == {"p1", "p2"} and "petfinder" not in st["sources"]      # not asked, not removed
    db.run("UPDATE listings SET last_seen = last_seen - ? WHERE source='petfinder'", (20 * 86400,))
    run_cleanup(db)
    assert active(db, "petfinder") == set()               # but dogs not imported for 14+ days still expire


def test_checks_and_refreshes_do_not_overlap(monkeypatch):
    db = setup(monkeypatch, FakeSource("s", ["1"]))
    assert poller._run_lock.acquire(blocking=False)       # pretend a refresh is running
    try:
        assert poller.start_cleanup(db, "manual") is False
        assert poller.run_poll(db) == 0
        # the 8 AM job waits for the refresh to finish rather than skipping the day
        waits = []
        monkeypatch.setattr(poller.time, "sleep", lambda s: (waits.append(s), poller._run_lock.release())[0] if len(waits) == 0 else waits.append(s))
        assert poller.scheduled_cleanup(db, wait_minutes=30) is True
    finally:
        for _ in range(300):
            if not poller.cleanup_snapshot()["running"]:
                break
            _real_sleep(0.02)
    assert poller.cleanup_snapshot()["trigger"] == "scheduled" and waits
    # if the refresh never ends, the job gives up after its wait instead of hanging forever
    assert poller._run_lock.acquire(blocking=False)
    try:
        monkeypatch.setattr(poller.time, "sleep", lambda s: None)
        clock = iter([0, 0, 10 ** 6, 10 ** 6, 10 ** 6, 10 ** 6])
        monkeypatch.setattr(poller.time, "time", lambda: next(clock, 10 ** 6))
        assert poller.scheduled_cleanup(db, wait_minutes=1) is False
    finally:
        poller._run_lock.release()


def test_adopt_a_pet_cleanup_opens_each_known_dog_because_the_sitemap_keeps_adopted_pets(monkeypatch):
    from app.sources import adoptapet
    db = setup(monkeypatch)
    for sid in ("1", "2", "3", "4", "5"):
        db.upsert_listing(dog("adoptapet", sid))
    src = adoptapet.AdoptAPet()
    # the sitemap lists 1-4 (5 left it) plus a brand-new dog 99; dog 2's page says "deleted" (adopted) although it is still in the sitemap
    src._entries = {"m": [(i, "kent", "WA", "husky-mix", f"http://x/{i}") for i in ("1", "2", "3", "4", "99")]}
    opened = []

    class R:
        status_code, headers = 200, {}

        def __init__(self, url):
            self.text = url

    class FakeClient:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, url):
            opened.append(url)
            return R(url)

    monkeypatch.setattr(src, "_client", lambda: FakeClient())
    monkeypatch.setattr(src, "_refresh_sitemaps", lambda c: None)
    monkeypatch.setattr(adoptapet, "parse_detail", lambda text: {"id": text.rsplit("/", 1)[1]})
    monkeypatch.setattr(adoptapet, "listing_from_detail",
                        lambda pet, tail: None if pet["id"] == "2" else dog("adoptapet", pet["id"], name="fresh"))
    monkeypatch.setattr(poller, "SOURCES", [src])
    st = run_cleanup(db)
    assert sorted(opened) == ["http://x/1", "http://x/2", "http://x/3", "http://x/4"]    # known dogs only; the new dog 99 is not fetched
    assert active(db, "adoptapet") == {"1", "3", "4"}                                    # 2 adopted, 5 left the sitemap
    assert st["status"] == "done" and st["sources"]["adoptapet"]["removed"] == 2
    # a later sitemap pass must not resurrect the adopted dog
    opened.clear()
    run_cleanup(db)
    assert "2" not in active(db, "adoptapet") and "http://x/2" not in opened
    # a challenge stops the checking, and nobody is removed because of it
    monkeypatch.setattr(src, "_cooldown_until", 0)
    R.status_code = 202
    st = run_cleanup(db)
    assert active(db, "adoptapet") == {"1", "3", "4"}


def test_it_runs_daily_at_8am_pacific_and_the_job_is_set_up():
    sched = BackgroundScheduler(timezone="UTC")
    from app import main
    main.schedule_jobs(sched, DB(":memory:"))
    job = sched.get_job("cleanup")
    assert job is not None and job.func is poller.scheduled_cleanup and job.misfire_grace_time == 6 * 3600
    la = ZoneInfo("America/Los_Angeles")
    fire = lambda now: job.trigger.get_next_fire_time(None, now.astimezone(la))
    nxt = fire(datetime(2026, 10, 2, 9, 30, tzinfo=la))                 # summer (PDT): after today's 8 AM -> tomorrow 8 AM
    assert (nxt.year, nxt.month, nxt.day, nxt.hour, nxt.minute) == (2026, 10, 3, 8, 0)
    assert nxt.utcoffset().total_seconds() == -7 * 3600
    nxt = fire(datetime(2026, 12, 1, 7, 59, tzinfo=la))                 # winter (PST): same morning, 8 AM = 16:00 UTC
    assert (nxt.month, nxt.day, nxt.hour) == (12, 1, 8) and nxt.utcoffset().total_seconds() == -8 * 3600
    assert nxt.astimezone(ZoneInfo("UTC")).hour == 16
    # exactly once per day: the run after 8:00 is the next day, never the same day twice
    assert fire(datetime(2026, 12, 1, 8, 0, 1, tzinfo=la)).day == 2
    assert sched.get_job("poll") is not None


def test_cleanup_routes_follow_the_app_access_rules(monkeypatch):
    from fastapi.testclient import TestClient
    from app import config, main
    monkeypatch.setattr(config, "REQUIRE_TOKEN", False)
    monkeypatch.setattr(config, "API_TOKEN", "t" * 32)
    monkeypatch.setattr(main, "db", DB(":memory:"))
    sched = BackgroundScheduler(timezone="UTC")
    main.schedule_jobs(sched, main.db)
    sched.start(paused=True)                                            # computes next run times without firing anything
    monkeypatch.setattr(main, "scheduler", sched)
    started = []
    monkeypatch.setattr(poller, "start_cleanup", lambda d, trigger="manual": started.append(trigger) or True)
    phone = TestClient(main.app, client=("100.110.177.118", 5))
    stranger = TestClient(main.app, client=("8.8.8.8", 5))
    try:
        info = phone.get("/api/cleanup").json()
        assert info["schedule"] == "Every day at 8:00 AM Pacific time" and info["last"] is None
        assert "8:00 AM Pacific" in info["next_run_text"] and info["next_run"] > time.time()
        assert phone.post("/api/cleanup").json() == {"started": True} and started == ["manual"]
        assert stranger.get("/api/cleanup").status_code == 401 and stranger.post("/api/cleanup").status_code == 401
        monkeypatch.setattr(poller, "start_cleanup", lambda d, trigger="manual": False)
        busy = phone.post("/api/cleanup")
        assert busy.status_code == 409 and "already running" in busy.json()["detail"]
        main.db.set_setting("cleanup_last", json.dumps({"at": 1, "removed": 3, "trigger": "scheduled", "sources": {}}))
        assert phone.get("/api/cleanup").json()["last"]["removed"] == 3
    finally:
        sched.shutdown(wait=False)


def test_breed_box_excludes_with_a_leading_dash_and_lists_options(monkeypatch):
    from app import search
    assert search.split_breed_terms("Lab, -chihuahua , - pit bull,-,-Chihuahua_Short_Coat") == (["lab"], ["chihuahua", "pit_bull", "chihuahua_short_coat"])
    db = DB(":memory:")
    monkeypatch.setattr(geo, "geocode", lambda d, q: {"lat": 47.38, "lon": -122.23, "zip": "98031", "label": "Kent, WA 98031"})
    for sid, b1, b2 in (("1", "Chihuahua", None), ("2", "American Pit Bull Terrier", None),
                        ("3", "Labrador Retriever", "Beagle"), ("4", "Beagle", None)):
        db.upsert_listing(dog("s", sid, name=f"D{sid}", breed_primary=b1, breed_secondary=b2))
    names = lambda p, o=None: sorted(c["name"] for c in search.search(db, {"location": "98031", "radius": 50, **p}, None, o))
    assert names({}) == ["D1", "D2", "D3", "D4"]
    assert names({"breed": "-chihuahua, -pit bull"}) == ["D3", "D4"]
    assert names({"breed": "beagle, -labrador"}) == ["D4"]
    opts = {}
    names({"breed": "beagle"}, opts)                       # options ignore the breed box itself
    assert opts == {"Chihuahua": 1, "American Pit Bull Terrier": 1, "Labrador Retriever": 1, "Beagle": 2}

    # a breed with its own comma/spaces is matched as one underscore word and never splits the filter
    db.upsert_listing(dog("s", "5", name="D5", breed_primary="Chihuahua, Short Coat"))
    db.upsert_listing(dog("s", "6", name="D6", breed_primary="Chihuahua"))
    assert names({"breed": "chihuahua_short_coat"}) == ["D5"]
    assert names({"breed": "-chihuahua_short_coat, -pit_bull"}) == ["D1", "D3", "D4", "D6"]
    assert names({"breed": "labrador retriever"}) == ["D3"]          # spaces still work and mean the same as underscores
