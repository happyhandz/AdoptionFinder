"""The automated Petfinder crawl, tested with a fake browser and fake pages: no Chrome, no network."""
import json
import time
import urllib.parse
import uuid
from pathlib import Path

from app import geo
from app import petfinder_crawl as pc
from app.db import DB

PF_START = ("https://www.petfinder.com/search/dogs-for-adoption/us/wa/seattle/"
            "?age=baby&includeOutOfTown=false&sort=newest_addition&page=1")


def dog_id(n):
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"dog{n}"))


def card(n):
    return (f'<div class="tw-w-full PFPetDetailCard_flipWrapper__x"><div>'
            f'<a href="/dog/dog{n}-{dog_id(n)}/wa/seattle/some-rescue-wa1/details/"><img src="https://cdn.example/{n}.jpg"></a>'
            f'<div class="tw-font-extrabold">Dog{n}</div><span>Puppy \u2022 Female</span>'
            f'<span>Labrador Retriever</span><span>Shelter</span><span>Some Rescue</span></div></div>')


class FakeChrome:
    """Stands in for ChromeSession. `site` maps page number -> dog numbers on it (newest first)."""
    instances = []

    def __init__(self, site, blocked_polls=0, on_navigate=None):
        self.site, self.visited, self.page = site, [], 0
        self.blocked_polls, self.on_navigate, self.closed = blocked_polls, on_navigate, False
        FakeChrome.instances.append(self)

    def open(self):
        pass

    def navigate(self, url):
        self.page = int(dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))["page"])
        self.visited.append(self.page)
        if self.on_navigate:
            self.on_navigate(self.page)

    def snapshot(self):
        if self.blocked_polls:
            if self.blocked_polls < 10 ** 6:
                self.blocked_polls -= 1
            return ("This site is temporarily unavailable", "We are sorry", 0, None)
        return ("Dogs & Puppies for Adoption", "ok", len(self.site.get(self.page, [])), None)

    def html(self):
        dogs = self.site.get(self.page, [])
        total = max(self.site) if self.site else 1
        return ("<html><body>" + "".join(card(n) for n in dogs) +
                f'<nav aria-label="Pagination">Previous Page {self.page} of {total} Next</nav></body></html>')

    def scroll_to_bottom(self):
        pass

    def has_pagination(self):
        return True

    def close(self, quit_browser=True):
        self.closed = True


def run(db, site, mode="auto", urls=(PF_START,), **kw):
    FakeChrome.instances.clear()
    out = pc.crawl(db, list(urls), mode, browser_factory=lambda: FakeChrome(site, **kw))
    return out, FakeChrome.instances[0]


def fast(monkeypatch):
    monkeypatch.setattr(pc.time, "sleep", lambda s: None)
    monkeypatch.setattr(pc, "PACES", {"fast": (0, 0), "normal": (0, 0), "gentle": (0, 0)})
    monkeypatch.setattr(pc, "CHECK_WAIT", 0.3)
    monkeypatch.setattr(pc, "LOAD_TIMEOUT", 0.3)
    monkeypatch.setattr(geo, "geocode", lambda d, q: {"lat": 47.6, "lon": -122.3, "zip": "98101", "label": "Seattle, WA 98101"})


def active(db):
    return {r["source_id"] for r in db.q("SELECT source_id FROM listings WHERE source='petfinder' AND status='active'")}


def test_helpers():
    assert pc.set_page(PF_START, 7).endswith("page=7") and "age=baby" in pc.set_page(PF_START, 7)
    assert pc.set_page("https://www.petfinder.com/x/", 2) == "https://www.petfinder.com/x/?page=2"
    assert pc.url_key(pc.set_page(PF_START, 1)) == pc.url_key(pc.set_page(PF_START, 9))
    assert pc.url_key(PF_START) != pc.url_key(PF_START.replace("age=baby", "age=young"))
    assert pc.total_pages('<nav aria-label="Pagination">Previous Page 13 of 41 Next</nav>') == 41
    assert pc.total_pages("<h1>Seattle Dogs 484 pet results</h1>") == 41            # 484 / 12, rounded up
    assert pc.total_pages("<html>nothing</html>") is None
    assert pc.looks_blocked("This site is temporarily unavailable | Nestle", "") and pc.looks_blocked("Just a moment...", "")
    assert not pc.looks_blocked("Dogs & Puppies for Adoption in Seattle, WA | Petfinder", "12 dogs")
    assert pc.clean_urls("https://www.petfinder.com/a/\n\nhttps://evil.example/x\nhttps://www.petfinder.com/a/\nnot a url") == \
        ["https://www.petfinder.com/a/"]
    real = Path(__file__).parent / "fixtures" / "petfinder_cards.html"
    assert pc.total_pages(real.read_text(encoding="utf-8") + "<nav>Page 2 of 5</nav>") == 5


def test_first_crawl_is_a_full_scrub(monkeypatch):
    fast(monkeypatch)
    db = DB(":memory:")
    out, chrome = run(db, {1: [1, 2, 3, 4], 2: [5, 6, 7, 8], 3: [9, 10]})
    assert out["status"] == "done" and not out["running"] and chrome.closed
    assert chrome.visited == [1, 2, 3]                                   # every page, no more
    assert active(db) == {dog_id(n) for n in range(1, 11)}
    assert out["dogs_found"] == 10 and out["dogs_new"] == 10 and out["pages"] == 3
    assert len(pc._scrub_ids(db, pc.url_key(PF_START))) == 10
    assert db.statuses()[0]["source"] == "petfinder" and db.statuses()[0]["count"] == 10
    assert json.loads(db.get_setting("pf_last_run"))["outcome"] == "done"


def test_update_crawl_stops_when_it_meets_known_dogs(monkeypatch):
    fast(monkeypatch)
    db = DB(":memory:")
    run(db, {1: [1, 2, 3, 4], 2: [5, 6, 7, 8], 3: [9, 10, 11, 12]})
    db.run("UPDATE listings SET last_seen = last_seen - 100000 WHERE source='petfinder'")     # make them look stale
    # two brand-new dogs appear at the top (newest first): half of page 1 is already known -> stop after page 1
    out, chrome = run(db, {1: [20, 21, 1, 2], 2: [3, 4, 5, 6], 3: [7, 8, 9, 10], 4: [11, 12]})
    assert chrome.visited == [1] and out["dogs_new"] == 2
    assert active(db) >= {dog_id(20), dog_id(21)} | {dog_id(n) for n in range(1, 13)}        # unseen older dogs stay listed
    recent = db.q("SELECT COUNT(*) AS n FROM listings WHERE source='petfinder' AND last_seen > ?", (time.time() - 3600,))[0]["n"]
    assert recent == 14                                                  # every previous dog was touched, so none expire
    # a page of entirely new dogs does not stop it: it keeps going until it meets known ones
    out, chrome = run(db, {1: [30, 31, 32, 33], 2: [34, 35, 36, 37], 3: [20, 21, 1, 2], 4: [3, 4]})
    assert chrome.visited == [1, 2, 3] and out["dogs_new"] == 8


def test_full_scrub_marks_missing_dogs_gone_unless_another_search_has_them(monkeypatch):
    fast(monkeypatch)
    db = DB(":memory:")
    url_b = PF_START.replace("age=baby", "age=young")
    run(db, {1: [1, 2, 3, 4]})                                           # search A saw dogs 1-4
    run(db, {1: [4, 5]}, urls=(url_b,))                                  # search B saw dogs 4 and 5
    assert active(db) == {dog_id(n) for n in (1, 2, 3, 4, 5)}
    run(db, {1: [1, 2]}, mode="full")                                    # A re-scrubbed: 3 and 4 vanished from A
    assert dog_id(3) not in active(db)                                   # only A listed 3 -> gone
    assert dog_id(4) in active(db)                                       # B still lists 4 -> stays
    assert {dog_id(n) for n in (1, 2, 5)} <= active(db)


def test_crawl_waits_for_a_check_then_continues_or_gives_up(monkeypatch):
    fast(monkeypatch)
    db = DB(":memory:")
    out, chrome = run(db, {1: [1, 2]}, blocked_polls=3)                  # a check shows for 3 polls, then clears
    assert out["status"] == "done" and active(db) == {dog_id(1), dog_id(2)}
    assert any("check" in line.lower() for line in out["log"])
    db2 = DB(":memory:")
    out, chrome = run(db2, {1: [1, 2]}, blocked_polls=10 ** 6)           # never clears
    assert out["status"] == "error" and "check" in out["message"].lower() and active(db2) == set() and chrome.closed


def test_stop_button_and_bad_input(monkeypatch):
    fast(monkeypatch)
    db = DB(":memory:")
    out, chrome = run(db, {1: [1, 2], 2: [3, 4], 3: [5, 6]}, on_navigate=lambda p: pc.request_stop() if p == 2 else None)
    assert out["status"] == "stopped" and chrome.visited == [1, 2] and not out["running"]
    assert active(db) == {dog_id(1), dog_id(2)}                          # what was imported before Stop stays; nothing marked gone
    out, _ = run(DB(":memory:"), {})                                     # first page has no dogs
    assert out["status"] == "error" and "first page" in out["message"]
    out = pc.crawl(DB(":memory:"), ["https://example.com/not-petfinder"], browser_factory=lambda: FakeChrome({}))
    assert out["status"] == "error" and "No Petfinder" in out["message"]


def test_crawl_routes_follow_the_app_access_rules_and_validate(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app import auth, config, importer
    monkeypatch.setattr(config, "REQUIRE_TOKEN", False)
    monkeypatch.setattr(config, "API_TOKEN", "t" * 32)
    db = DB(":memory:")
    a = FastAPI()
    a.middleware("http")(auth.auth_middleware)
    a.include_router(importer.make_router(db))
    me = TestClient(a, client=("127.0.0.1", 5))
    phone = TestClient(a, client=("100.110.177.118", 5))      # the phone, over Tailscale
    stranger = TestClient(a, client=("8.8.8.8", 5))           # the public internet
    started = []
    monkeypatch.setattr(pc, "start", lambda d, urls, mode, pace: started.append((urls, mode, pace)) or True)
    monkeypatch.setattr(pc, "find_chrome", lambda: "C:\\chrome.exe")
    # the phone app can use the Petfinder panel...
    assert phone.get("/api/petfinder/crawl").status_code == 200
    assert phone.post("/api/petfinder/crawl", json={"mode": "auto"}).json()["started"] is True and started
    assert phone.post("/api/petfinder/crawl/stop").status_code == 200
    # ...the public internet cannot (no token), and with the token it can
    for method, path in (("get", "/api/petfinder/crawl"), ("post", "/api/petfinder/crawl"), ("put", "/api/petfinder/urls")):
        assert getattr(stranger, method)(path).status_code == 401, path
    assert stranger.get("/api/petfinder/crawl", headers={"Authorization": "Bearer " + "t" * 32}).status_code == 200
    # in token mode even a home-network device needs the token
    monkeypatch.setattr(config, "REQUIRE_TOKEN", True)
    assert phone.get("/api/petfinder/crawl").status_code == 401
    monkeypatch.setattr(config, "REQUIRE_TOKEN", False)
    # the pages that carry the bookmark import secret stay on the PC itself
    assert phone.get("/import").status_code == 404 and phone.post("/api/import/petfinder", json={"html": "x" * 300}).status_code == 404
    assert me.get("/import").status_code == 200
    # validation
    info = me.get("/api/petfinder/crawl").json()
    assert info["urls"] == [pc.DEFAULT_URL] and info["chrome"] is True
    assert me.put("/api/petfinder/urls", json={"urls": "https://evil.example/\n  "}).status_code == 400
    saved = me.put("/api/petfinder/urls", json={"urls": PF_START + "\nhttps://www.petfinder.com/search/dogs-for-adoption/us/wa/kent/"}).json()
    assert saved["urls"][0] == PF_START and len(saved["urls"]) == 2
    assert me.post("/api/petfinder/crawl", json={"mode": "bogus"}).status_code == 400
    assert me.post("/api/petfinder/crawl", json={"mode": "full", "pace": "bogus"}).status_code == 400
    started.clear()
    assert me.post("/api/petfinder/crawl", json={"mode": "full"}).json() == {"started": True, "mode": "full", "pace": "normal"}
    assert started[0][1:] == ("full", "normal") and len(started[0][0]) == 2
    assert me.post("/api/petfinder/crawl", json={"mode": "auto", "pace": "fast"}).json()["pace"] == "fast"
    assert started[1][2] == "fast" and me.get("/api/petfinder/crawl").json()["pace"] == "fast"   # remembered for next time
    monkeypatch.setattr(pc, "start", lambda d, urls, mode, pace: False)
    assert me.post("/api/petfinder/crawl", json={}).status_code == 409    # already running
    monkeypatch.setattr(pc, "find_chrome", lambda: None)
    assert me.post("/api/petfinder/crawl", json={}).status_code == 400


def test_pace_controls_the_pause_between_pages(monkeypatch):
    monkeypatch.setattr(pc, "PACES", {"fast": (2.0, 2.0), "normal": (7.0, 7.0), "gentle": (15.0, 15.0)})
    monkeypatch.setattr(geo, "geocode", lambda d, q: {"lat": 47.6, "lon": -122.3, "zip": "98101", "label": "Seattle, WA 98101"})
    monkeypatch.setattr(pc.time, "sleep", lambda s: None)
    monkeypatch.setattr(pc.random, "uniform", lambda a, b: a)
    for pace, expected in (("fast", 2.0), ("normal", 7.0), ("gentle", 15.0), ("nonsense", 7.0)):
        naps = []
        monkeypatch.setattr(pc, "_nap", lambda sec, naps=naps: naps.append(sec))
        FakeChrome.instances.clear()
        pc.crawl(DB(":memory:"), [PF_START], "full", browser_factory=lambda: FakeChrome({1: [1], 2: [2], 3: [3]}), pace=pace)
        assert naps == [expected, expected], (pace, naps)            # one pause between each pair of pages, none after the last


def test_apk_version_endpoint_is_public_and_describes_the_served_file(monkeypatch, tmp_path):
    import hashlib
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app import auth, config, pairing
    monkeypatch.setattr(config, "REQUIRE_TOKEN", True)            # strictest mode: still public
    monkeypatch.setattr(config, "API_TOKEN", "t" * 32)
    apk, ver = tmp_path / "a.apk", tmp_path / "version.json"
    apk.write_bytes(b"PK\x03\x04some-apk-bytes")
    ver.write_text(json.dumps({"versionCode": 7, "versionName": "1.6", "size": 1, "sha256": "stale"}), encoding="utf-8")
    monkeypatch.setattr(pairing, "APK_PATH", apk)
    monkeypatch.setattr(pairing, "VERSION_PATH", ver)
    a = FastAPI()
    a.middleware("http")(auth.auth_middleware)
    a.include_router(pairing.router)
    stranger = TestClient(a, client=("8.8.8.8", 9))
    r = stranger.get("/apk/version")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    v = r.json()
    assert v["versionCode"] == 7 and v["versionName"] == "1.6"
    assert v["size"] == apk.stat().st_size and v["sha256"] == hashlib.sha256(apk.read_bytes()).hexdigest()   # never the stale values
    assert stranger.post("/apk/version").status_code in (401, 405)
    apk.unlink()
    assert stranger.get("/apk/version").json()["versionCode"] == 0        # nothing published: the app says so
    apk.write_bytes(b"x"); ver.write_text("{not json", encoding="utf-8")
    assert stranger.get("/apk/version").json()["versionCode"] == 0        # unreadable version file: safe default, no crash


def test_the_crawl_runs_in_the_server_process_on_the_pc_not_on_the_caller(monkeypatch):
    """A request from the phone only *starts* the crawl; Chrome is launched by the server, on the PC."""
    import os
    import socket
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app import auth, config, importer
    monkeypatch.setattr(config, "REQUIRE_TOKEN", False)
    launched = []

    class FakePopen:
        def __init__(self, cmd, **kw):
            launched.append({"cmd": cmd, "pid_of_launcher": os.getpid(), "thread": __import__("threading").current_thread().name})

        def wait(self, timeout=None):
            return 0

        def terminate(self):
            pass

    seen_threads = []
    monkeypatch.setattr(pc.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(pc, "find_chrome", lambda: "C:\chrome.exe")
    monkeypatch.setattr(pc.ChromeSession, "_targets", lambda self: (_ for _ in ()).throw(OSError()) if not launched else
                        [{"type": "page", "webSocketDebuggerUrl": "ws://x"}])
    monkeypatch.setattr(pc.ChromeSession, "open", lambda self: pc.subprocess.Popen([pc.find_chrome(), f"--user-data-dir={self.profile}"]))
    monkeypatch.setattr(pc.ChromeSession, "navigate", lambda self, url: seen_threads.append("navigated"))
    monkeypatch.setattr(pc.ChromeSession, "snapshot", lambda self: ("Dogs", "ok", 0, None))
    monkeypatch.setattr(pc.ChromeSession, "close", lambda self, quit_browser=True: None)
    fast(monkeypatch)
    db = DB(":memory:")
    a = FastAPI()
    a.middleware("http")(auth.auth_middleware)
    a.include_router(importer.make_router(db))
    phone = TestClient(a, client=("100.110.177.118", 5))                      # a different device on the tailnet
    assert phone.post("/api/petfinder/crawl", json={"mode": "full"}).json()["started"] is True
    for _ in range(100):                                                      # wait for the background crawl thread
        if not pc.is_running() and launched:
            break
        time.sleep(0.05)
    assert launched and "--user-data-dir" in " ".join(launched[0]["cmd"])     # Chrome was launched by the server process...
    assert launched[0]["pid_of_launcher"] == os.getpid()                      # ...in this (the PC's) process,
    assert launched[0]["thread"] == "petfinder-crawl"                         # on the crawl's own background thread
    assert pc.snapshot()["host"] == socket.gethostname()                      # and the status says which machine ran it


def test_chrome_is_launched_so_it_keeps_working_when_minimized_or_hidden(monkeypatch):
    """The app runs minimized and crawls may be started from the phone: Chrome must not freeze in a hidden window."""
    import websockets.sync.client as wsc
    launched, calls = [], []

    class FakePopen:
        def __init__(self, cmd, **kw):
            launched.append(cmd)

    class FakeWS:
        def send(self, data):
            msg = json.loads(data)
            calls.append((msg["method"], msg.get("params", {})))
            self.id = msg["id"]

        def recv(self, timeout=None):
            return json.dumps({"id": self.id, "result": {"windowId": 7}})

        def close(self):
            pass

    monkeypatch.setattr(pc.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(pc, "find_chrome", lambda: "C:\chrome.exe")
    monkeypatch.setattr(pc.time, "sleep", lambda s: None)
    monkeypatch.setattr(wsc, "connect", lambda url, **kw: FakeWS())
    state = {"up": False}

    def targets(self):
        if not launched:
            raise OSError("not running yet")
        return [{"type": "page", "id": "T1", "webSocketDebuggerUrl": "ws://x"}]

    monkeypatch.setattr(pc.ChromeSession, "_targets", targets)
    session = pc.ChromeSession()
    session.open()
    cmd = " ".join(launched[0])
    for flag in ("--disable-renderer-backgrounding", "--disable-background-timer-throttling", "--disable-backgrounding-occluded-windows",
                 "--remote-debugging-port=", "--user-data-dir="):
        assert flag in cmd, flag
    assert "petfinder_chrome" in cmd                                           # its own profile, not your everyday Chrome
    methods = [m for m, _ in calls]
    assert "Browser.setWindowBounds" in methods and "Page.bringToFront" in methods   # a minimized-born window is restored
    assert dict(calls)["Browser.setWindowBounds"]["bounds"] == {"windowState": "normal"}
    # a command Chrome never answers is reported by name, not as a bare "timed out"
    class SilentWS(FakeWS):
        def recv(self, timeout=None):
            raise TimeoutError("timed out")
    session.ws = SilentWS()
    try:
        session.call("Runtime.evaluate", timeout=0.2)
        assert False
    except TimeoutError as e:
        assert "Runtime.evaluate" in str(e)
