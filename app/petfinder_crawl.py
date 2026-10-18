"""Walk Petfinder search pages in a real Chrome window and import the dogs, only when asked.

This automates what you do by hand with the bookmark: open a search page, send it to the app, go to the next
page, repeat. It drives a normal, visible Chrome window (its own profile folder, so your everyday browser is
left alone) through the DevTools port, at a human pace (a pause of several seconds between pages), and stops
as soon as Petfinder shows a check or block page. Nothing hides the automation or solves checks: if Petfinder asks
for a check, the crawl waits for YOU to complete it in the window, and gives up if you don't.

Modes (per search URL):
  full    every page, from page 1 to the last page shown in the pagination bar. Dogs from the previous scrub that
          are no longer listed are marked gone (unless another saved search still lists them).
  update  newest-first pages until the dogs start repeating what the previous scrub saw (half a page or more
          already known), then stop. Dogs from the previous scrub are assumed still listed.
  auto    "update" if this search was scrubbed before, otherwise "full".

Runs only when started from the web UI (or the API). Never on a schedule.
"""
import json
import logging
import math
import random
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.request
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from . import config, dedupe, events
from .sources import petfinder

log = logging.getLogger("petfinder_crawl")

DEBUG_PORT = 9340
PROFILE_DIR = config.ROOT / "data" / "petfinder_chrome"
DEFAULT_URL = ("https://www.petfinder.com/search/dogs-for-adoption/us/wa/seattle/"
               "?age=baby&includeOutOfTown=false&sort=newest_addition&page=1")
# Seconds to pause between pages. "normal" is about what a person clicking Next takes; "fast" is a quick clicker.
PACES = {"fast": (2.0, 4.0), "normal": (6.0, 11.0), "gentle": (12.0, 20.0)}
DEFAULT_PACE = "normal"
POLL = 0.4                      # how often to look at the page while waiting for it to draw
LOAD_TIMEOUT = 45               # seconds for a page's dog cards to appear
CHECK_WAIT = 300                # seconds to wait for you to finish a Petfinder check in the window
MAX_PAGES = config.PETFINDER_MAX_PAGES   # per search URL, whatever the pagination says (PETFINDER_MAX_PAGES in .env)
KNOWN_SHARE_STOP = 0.5          # update mode: stop once this share of a page is already known
PER_PAGE = 12
CARDS_JS = "document.querySelectorAll('div[class*=\"PFPetDetailCard_flipWrapper\"]').length"
BLOCK_MARKERS = ("temporarily unavailable", "access denied", "verify you are human", "captcha", "just a moment",
                 "unusual traffic", "request blocked", "are you a robot")


# ---- pure helpers ---------------------------------------------------------------------------------

def set_page(url, n):
    u = urlparse(url)
    q = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True) if k != "page"]
    q.append(("page", str(n)))
    return urlunparse(u._replace(query=urlencode(q)))


def url_key(url):
    """The search without its page number, so every page of one search shares one key."""
    u = urlparse(url.strip())
    q = sorted((k, v) for k, v in parse_qsl(u.query, keep_blank_values=True) if k != "page")
    return urlunparse(u._replace(query=urlencode(q), fragment="")).lower()


def total_pages(html):
    """'Page 13 of 41' in the pagination bar, else ceil('484 pet results' / 12), else None."""
    m = re.search(r"Page\s+(\d+)\s+of\s+(\d+)", html)
    if m:
        return int(m.group(2))
    m = re.search(r"([\d,]+)\s+pet\s+results?", re.sub(r"<[^>]+>", " ", html))
    if m:
        return max(1, math.ceil(int(m.group(1).replace(",", "")) / PER_PAGE))
    return None


def looks_blocked(title, text):
    blob = f"{title or ''} {(text or '')[:600]}".lower()
    return any(m in blob for m in BLOCK_MARKERS)


def clean_urls(raw):
    out = []
    for line in (raw if isinstance(raw, list) else str(raw or "").splitlines()):
        line = line.strip()
        if line and urlparse(line).netloc.endswith("petfinder.com") and line not in out:
            out.append(line)
    return out


# ---- Chrome over the DevTools port --------------------------------------------------------------------

def find_chrome():
    for p in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"):
        if shutil.which(p):
            return p
    return shutil.which("chrome") or shutil.which("google-chrome")


class ChromeSession:
    """A visible Chrome window we start (or attach to) and drive with a handful of DevTools calls."""

    def __init__(self, port=DEBUG_PORT, profile=PROFILE_DIR):
        self.port, self.profile = port, profile
        self.proc, self.ws, self._id = None, None, 0

    def _targets(self):
        return json.load(urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json", timeout=3))

    def open(self):
        from websockets.sync.client import connect
        try:
            tabs = self._targets()            # already running (e.g. the previous crawl's window)
        except Exception:
            exe = find_chrome()
            if not exe:
                raise RuntimeError("Google Chrome was not found on this PC")
            self.profile.mkdir(parents=True, exist_ok=True)
            # The app usually runs minimized in the background and you may start a crawl from your phone while the PC is
            # idle or locked. Without these flags Chrome freezes pages in a minimized/hidden window (the page stops answering).
            self.proc = subprocess.Popen([exe, f"--remote-debugging-port={self.port}", f"--user-data-dir={self.profile}",
                                          "--no-first-run", "--no-default-browser-check",
                                          "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
                                          "--disable-backgrounding-occluded-windows", "--window-size=1280,900", "about:blank"],
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            tabs = None
            for _ in range(60):
                time.sleep(0.5)
                try:
                    tabs = self._targets()
                    break
                except Exception:
                    pass
            if tabs is None:
                raise RuntimeError("Chrome did not start")
        page = next(t for t in tabs if t.get("type") == "page")
        self.ws = connect(page["webSocketDebuggerUrl"], max_size=100_000_000)
        self.call("Page.enable")
        self.call("Runtime.enable")
        try:   # make sure the window is a normal, shown window (it can be born minimized when the app was started minimized)
            win = self.call("Browser.getWindowForTarget", timeout=10, targetId=page["id"])
            self.call("Browser.setWindowBounds", timeout=10, windowId=win["windowId"], bounds={"windowState": "normal"})
            self.call("Page.bringToFront", timeout=10)
        except Exception as e:
            log.info("could not adjust the Chrome window: %s", e)

    def call(self, method, timeout=60, **params):
        self._id += 1
        self.ws.send(json.dumps({"id": self._id, "method": method, "params": params}))
        deadline = time.time() + timeout
        while True:
            try:
                msg = json.loads(self.ws.recv(timeout=max(deadline - time.time(), 0.1)))
            except TimeoutError:
                raise TimeoutError(f"Chrome did not answer {method} within {timeout} s") from None
            if msg.get("id") == self._id:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error'].get('message')}")
                return msg.get("result", {})

    def eval(self, expr):
        r = self.call("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        return r.get("result", {}).get("value")

    def navigate(self, url):
        self.call("Page.navigate", url=url)

    def snapshot(self):
        """(title, visible text start, number of dog cards, full html) of the current page."""
        return (self.eval("document.title"), self.eval("(document.body && document.body.innerText || '').slice(0, 600)"),
                self.eval(CARDS_JS) or 0, None)

    def html(self):
        return self.eval("document.documentElement.outerHTML")

    def scroll_to_bottom(self):
        self.eval("window.scrollTo(0, document.body.scrollHeight)")

    def has_pagination(self):
        return bool(self.eval("/Page\\s+\\d+\\s+of\\s+\\d+/.test(document.body ? document.body.innerText : '')"))

    def close(self, quit_browser=True):
        try:
            if self.ws:
                if quit_browser and self.proc is not None:
                    try:
                        self.call("Browser.close", timeout=5)
                    except Exception:
                        pass
                self.ws.close()
        finally:
            if self.proc is not None and quit_browser:
                try:
                    self.proc.wait(timeout=8)
                except Exception:
                    self.proc.terminate()


# ---- state shared with the web UI --------------------------------------------------------------------

_lock = threading.Lock()
_stop = threading.Event()
state = {"running": False, "status": "idle", "message": "", "host": "", "mode": "", "url": "", "url_index": 0, "url_count": 0,
         "page": 0, "pages": 0, "dogs_found": 0, "dogs_new": 0, "started": None, "finished": None, "log": []}


def _say(message, status=None, **fields):
    with _lock:
        state["message"] = message
        if status:
            state["status"] = status
        state.update(fields)
        state["log"] = (state["log"] + [f"{time.strftime('%H:%M:%S')}  {message}"])[-40:]
        snap = dict(state)
    log.info(message)
    events.publish("petfinder_progress", snap)


def snapshot():
    with _lock:
        return dict(state)


def request_stop():
    _stop.set()


def is_running():
    return snapshot()["running"]


# ---- the crawl -----------------------------------------------------------------------------------------

def _scrub_ids(db, key):
    return {r["dog_id"] for r in db.q("SELECT dog_id FROM petfinder_scrub WHERE url_key=?", (key,))}


def _nap(seconds):
    """Sleep in short steps so Stop is honoured quickly."""
    end = time.time() + seconds
    while time.time() < end and not _stop.is_set():
        time.sleep(min(0.5, max(end - time.time(), 0)))


def _wait_for_cards(browser, wait_for_user=True):
    """Wait for a page's dog cards. Returns ('ok', n) | ('empty', 0) | ('blocked', 0) | ('stopped', 0)."""
    deadline = time.time() + LOAD_TIMEOUT
    last, stable, asked = -1, 0, False
    while not _stop.is_set():
        title, text, n, _ = browser.snapshot()
        if looks_blocked(title, text) and n == 0:
            if not wait_for_user:
                return "blocked", 0
            if not asked:
                asked = True
                _say("Petfinder is showing a check. Finish it in the Chrome window and the crawl will continue "
                     f"(waiting up to {CHECK_WAIT // 60} minutes).", status="waiting")
                deadline = time.time() + CHECK_WAIT
            if time.time() > deadline:
                return "blocked", 0
        else:
            if asked:
                asked = False
                _say("Check cleared, continuing.", status="running")
                deadline = time.time() + LOAD_TIMEOUT
            if n > 0:
                stable = stable + 1 if n == last else 0
                last = n
                if stable >= 2:
                    browser.scroll_to_bottom()     # the pagination bar renders at the bottom
                    end = time.time() + 4          # wait for "Page N of M" to appear, but never more than 4 s
                    while time.time() < end and not browser.has_pagination():
                        time.sleep(0.25)
                    return "ok", n
            elif time.time() > deadline:
                return "empty", 0
        time.sleep(POLL)
    return "stopped", 0


def crawl(db, urls, mode="auto", browser_factory=ChromeSession, pace=DEFAULT_PACE):
    """Run one crawl over `urls`. Blocking; call from a thread. Returns the final state."""
    urls = clean_urls(urls)
    if not urls:
        _say("No Petfinder search URLs to crawl.", status="error", running=False)
        return snapshot()
    _stop.clear()
    with _lock:
        state.update({"running": True, "status": "starting", "message": "Starting Chrome on your PC...", "mode": mode,
                      "host": socket.gethostname(), "url": "",
                      "url_index": 0, "url_count": len(urls), "page": 0, "pages": 0, "dogs_found": 0, "dogs_new": 0,
                      "started": time.time(), "finished": None, "log": []})
    events.publish("petfinder_progress", snapshot())
    browser = browser_factory()
    all_seen, total_new, outcome = set(), 0, "done"
    try:
        browser.open()
        for ui, url in enumerate(urls, 1):
            if _stop.is_set():
                outcome = "stopped"
                break
            key = url_key(url)
            prev = _scrub_ids(db, key)
            effective = "full" if (mode == "full" or not prev) else "update"
            _say(f"Search {ui}/{len(urls)}: {effective} scrub" + (" (first time for this search)" if not prev and mode != "full" else ""),
                 status="running", url=url, url_index=ui, page=0, pages=0)
            seen, completed, pages_total = set(), False, None
            page = 1
            while page <= MAX_PAGES and not _stop.is_set():
                browser.navigate(set_page(url, page))
                result, n = _wait_for_cards(browser)
                if result == "stopped":
                    break
                if result == "blocked":
                    _say("Petfinder did not clear its check. Stopping.", status="error")
                    outcome = "blocked"
                    break
                if result == "empty":
                    if page == 1:
                        _say("No dogs on the first page. Is the search URL right, or did Petfinder change its page?", status="error")
                        outcome = "error"
                    else:
                        completed = True            # walked past the last page
                    break
                html = browser.html()
                if pages_total is None:
                    pages_total = total_pages(html)
                    with _lock:
                        state["pages"] = min(pages_total or 0, MAX_PAGES)
                listings = petfinder.parse_page(html, set_page(url, page))
                ids = [l["source_id"] for l in listings]
                new = petfinder.import_listings(db, listings)
                seen.update(ids)
                total_new += new
                known = sum(1 for i in ids if i in prev)
                with _lock:
                    state["page"], state["dogs_found"], state["dogs_new"] = page, len(all_seen | seen), total_new
                _say(f"Page {page}" + (f" of {pages_total}" if pages_total else "") +
                     f": {len(ids)} dogs, {new} new to the app" + (f", {known} already known" if prev else ""), status="running")
                if effective == "update" and ids and known / len(ids) >= KNOWN_SHARE_STOP:
                    _say(f"Reached dogs from the last scrub ({known} of {len(ids)} on this page): stopping early.")
                    completed = True
                    break
                if pages_total is not None and page >= pages_total:
                    completed = True
                    break
                if not ids:
                    completed = True
                    break
                page += 1
                _nap(random.uniform(*PACES.get(pace, PACES[DEFAULT_PACE])))
            # book-keeping for this search
            if seen:
                db.run_many("INSERT OR IGNORE INTO petfinder_scrub (url_key, dog_id, seen_at) VALUES (?,?,?)",
                            [(key, i, time.time()) for i in seen])
            if effective == "full" and completed and outcome == "done" and not _stop.is_set():
                gone = prev - seen
                if gone:
                    db.run_many("DELETE FROM petfinder_scrub WHERE url_key=? AND dog_id=?", [(key, i) for i in gone])
                    still = {r["dog_id"] for r in db.q("SELECT DISTINCT dog_id FROM petfinder_scrub")}
                    db.mark_gone(petfinder.SOURCE, [i for i in gone if i not in still])
                    _say(f"{len(gone)} dogs from the previous scrub are no longer listed.")
            elif prev:
                db.touch(petfinder.SOURCE, prev)       # an update only sees the newest pages: assume the rest are still listed
            all_seen |= seen
            if outcome in ("blocked", "error"):
                break
            if _stop.is_set():
                outcome = "stopped"
                break
        if all_seen:
            db.set_status(petfinder.SOURCE, True, count=len(all_seen))
            try:
                dedupe.run(db)
            except Exception:
                log.exception("dedupe after Petfinder crawl failed")
    except Exception as e:
        log.exception("Petfinder crawl failed")
        outcome = "error"
        _say(f"Crawl failed: {e}", status="error")
    finally:
        try:
            browser.close()
        except Exception:
            pass
    final = {"done": ("done", f"Finished: {len(all_seen)} dogs seen, {total_new} new."),
             "stopped": ("stopped", f"Stopped. {len(all_seen)} dogs seen so far, {total_new} new."),
             "blocked": ("error", "Stopped: Petfinder showed a check that was not completed."),
             "error": ("error", snapshot()["message"] or "The crawl failed.")}[outcome]
    with _lock:
        state["running"], state["finished"] = False, time.time()
    _say(final[1], status=final[0])
    db.set_setting("pf_last_run", json.dumps({"at": time.time(), "mode": mode, "outcome": outcome,
                                              "dogs": len(all_seen), "new": total_new}))
    events.publish("source_done", {"source": petfinder.SOURCE, "new": total_new})
    return snapshot()


def start(db, urls, mode="auto", pace=DEFAULT_PACE):
    """Start a crawl in a background thread. False if one is already running."""
    with _lock:
        if state["running"]:
            return False
        state["running"] = True          # claimed before the thread starts so a double-click can't start two
    threading.Thread(target=crawl, args=(db, urls, mode, ChromeSession, pace), daemon=True, name="petfinder-crawl").start()
    return True
