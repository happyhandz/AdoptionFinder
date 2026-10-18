import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import config, dedupe, events, geo, search
from .sources.adoptapet import AdoptAPet
from .sources.demo import Demo
from .sources.kingcounty import KingCounty
from .sources import petfinder
from .sources.oregonhumane import OregonHumane
from .sources.pet24 import Pet24
from .sources.petango import Petango
from .sources.petfinder import Petfinder
from .sources.rescuegroups import RescueGroups
from .sources.shelterluv import Shelterluv
from .sources.webscrape import load_sites

log = logging.getLogger("poller")

SOURCES = [RescueGroups(), KingCounty(), Shelterluv(), OregonHumane(), Petango(), Pet24(), AdoptAPet(), Petfinder(), Demo()] + load_sites()
_run_lock = threading.Lock()
_dedupe_lock = threading.Lock()

# "Whole state" searches poll a fixed centre with a radius that covers the state, because
# sources take a point + radius. (zip, lat, lon, radius_miles)
STATE_CENTERS = {"WA": ("98926", 47.0, -120.55, 250)}


def watch_regions(db):
    """Default search + every saved search. Plain searches merge to the largest radius per ZIP;
    "whole state" searches become one region per state."""
    param_sets = [{"location": config.DEFAULT_LOCATION, "radius": config.DEFAULT_RADIUS}]
    param_sets += [s["params"] for s in db.list_searches()]
    regions = {}
    for p in param_sets:
        g = search.resolve_origin(db, p)
        if not g:
            continue
        if str(p.get("statewide")) in ("1", "true", "True"):
            st = geo.state_of(g)
            if st:
                zip_, lat, lon, radius = STATE_CENTERS.get(st, (g["zip"], g["lat"], g["lon"], 300))
                regions[("state", st)] = {"zip": zip_, "lat": lat, "lon": lon, "radius": radius,
                                          "statewide": True, "state": st}
                continue
        radius = int(float(p.get("radius") or config.DEFAULT_RADIUS))
        r = regions.setdefault(g["zip"], {"zip": g["zip"], "lat": g["lat"], "lon": g["lon"], "radius": 0})
        r["radius"] = max(r["radius"], radius)
    return list(regions.values())


def _fetch_into_db(src, regions, db, cleanup=False):
    """Fetch one source across all regions and store what it returns. Returns (listings seen, listings new)."""
    fetch = (getattr(src, "fetch_for_cleanup", None) or src.fetch) if cleanup else src.fetch
    seen = new = 0
    for region in regions:
        for listing in fetch(region, db):
            _, is_new = db.upsert_listing(listing)
            seen += 1
            new += is_new
    return seen, new


def _run_source(src, regions, db):
    """A normal refresh of one source: adds new dogs and refreshes known ones. It never removes anything;
    removal is the job of the daily/manual cleanup check (run_cleanup). Returns the number of new listings."""
    new = 0
    try:
        seen, new = _fetch_into_db(src, regions, db)
        db.set_status(src.name, True, count=seen)
    except Exception as e:  # one broken source must not stop the others
        log.exception("source %s failed", src.name)
        db.set_status(src.name, False, error=f"{type(e).__name__}: {e}")
    with _dedupe_lock:
        try:
            dedupe.run(db)
        except Exception:
            log.exception("dedupe failed")
    events.publish("source_done", {"source": src.name, "new": new})
    return new


def run_poll(db):
    """One full cycle: every source in parallel (each is rate-limited on its own host).
    Returns the number of new listings. Skips if a cycle (or a cleanup) is already running."""
    if not _run_lock.acquire(blocking=False):
        return 0
    try:
        events.publish("poll_started")
        regions = watch_regions(db)
        for s in SOURCES:
            if getattr(s, "manual", False):
                db.expire_stale(s.name, petfinder.EXPIRE_DAYS)
        active = [s for s in SOURCES if s.enabled() and not getattr(s, "manual", False)]
        with ThreadPoolExecutor(max_workers=max(len(active), 1), thread_name_prefix="src") as ex:
            total_new = sum(ex.map(lambda s: _run_source(s, regions, db), active))
        events.publish("poll_done", {"new": total_new, "at": time.time()})
        log.info("poll complete: %d new", total_new)
        return total_new
    finally:
        _run_lock.release()


def refresh_async(db):
    threading.Thread(target=run_poll, args=(db,), daemon=True).start()


# ---- the cleanup check: remove dogs that are no longer listed -----------------------------------------------
#
# Every source is asked for its current list again. Dogs we hold for that source that did not come back are marked
# gone. Safeguards, so a broken page can't wipe a shelter out:
#   * a source that errors, is blocked or is cooling down is skipped (nothing removed);
#   * a source that returns under CLEANUP_MIN_KEEP of what we hold is treated as incomplete (nothing removed).
# A dog removed by mistake comes back by itself at the next refresh. Petfinder is not covered (it is fed by hand);
# its dogs leave with a Full scrub, or after PETFINDER EXPIRE_DAYS days without being imported.

CLEANUP_TZ = "America/Los_Angeles"
CLEANUP_AT = (8, 0)                 # every day at 8:00 AM Pacific time (PST in winter, PDT in summer)
CLEANUP_MIN_KEEP = 0.4
CLEANUP_GUARD_FROM = 5              # sources holding fewer dogs than this are not guarded (too few to judge)

cleanup_state = {"running": False, "status": "idle", "message": "", "trigger": "", "started": None, "finished": None,
                 "removed": 0, "sources": {}}
_state_lock = threading.Lock()


def cleanup_snapshot():
    with _state_lock:
        return {**cleanup_state, "sources": {k: dict(v) for k, v in cleanup_state["sources"].items()}}


def _cleanup_say(message=None, source=None, info=None, **fields):
    with _state_lock:
        if message is not None:
            cleanup_state["message"] = message
        if source is not None:
            cleanup_state["sources"][source] = info
        cleanup_state.update(fields)
        snap = {**cleanup_state, "sources": {k: dict(v) for k, v in cleanup_state["sources"].items()}}
    events.publish("cleanup_progress", snap)


def _cleanup_source(src, regions, db):
    """Re-fetch one source and remove the dogs it no longer lists. Returns a summary dict."""
    before = db.count_active(src.name)
    started = time.time()
    src.retired = 0                 # dogs a source removed itself by checking their pages (Adopt-a-Pet)
    info = {"label": src.name, "before": before, "fetched": 0, "removed": 0, "status": "ok", "note": ""}
    try:
        seen, _ = _fetch_into_db(src, regions, db, cleanup=True)
        info["fetched"] = seen
        kept = db.count_seen_since(src.name, started)
        if before >= CLEANUP_GUARD_FROM and kept < CLEANUP_MIN_KEEP * before:
            info.update(status="skipped", note=f"returned {kept} of the {before} dogs held, which looks incomplete: nothing removed")
        else:
            info["removed"] = db.remove_unseen(src.name, started) + getattr(src, "retired", 0)
        db.set_status(src.name, True, count=seen)
    except Exception as e:
        log.exception("cleanup of %s failed", src.name)
        info.update(status="error", note=f"{type(e).__name__}: {e}: nothing removed")
        db.set_status(src.name, False, error=f"{type(e).__name__}: {e}")
    return info


def _cleanup_run(db, trigger):
    """Body of a cleanup. The caller holds _run_lock."""
    try:
        _cleanup_say("Asking every source for its current list...", status="running")
        regions = watch_regions(db)
        for s in SOURCES:
            if getattr(s, "manual", False):
                db.expire_stale(s.name, petfinder.EXPIRE_DAYS)
        active = [s for s in SOURCES if s.enabled() and not getattr(s, "manual", False)]

        def one(src):
            info = _cleanup_source(src, regions, db)
            _cleanup_say(source=src.name, info=info)
            return info

        with ThreadPoolExecutor(max_workers=max(len(active), 1), thread_name_prefix="cleanup") as ex:
            results = list(ex.map(one, active))
        with _dedupe_lock:
            try:
                dedupe.run(db)
            except Exception:
                log.exception("dedupe after cleanup failed")
        removed = sum(r["removed"] for r in results)
        problems = [r for r in results if r["status"] != "ok"]
        summary = f"Removed {removed} dog{'s' if removed != 1 else ''} that are no longer listed."
        if problems:
            summary += f" {len(problems)} source{'s were' if len(problems) != 1 else ' was'} skipped for safety (see below)."
        last = {"at": time.time(), "trigger": trigger, "removed": removed,
                "sources": {r["label"]: {k: r[k] for k in ("before", "fetched", "removed", "status", "note")} for r in results}}
        db.set_setting("cleanup_last", json.dumps(last))
        with _state_lock:
            cleanup_state["finished"] = time.time()
        _cleanup_say(summary, status="done", running=False, removed=removed)
        events.publish("poll_done", {"new": 0, "at": time.time()})    # the page reloads its results
        log.info("cleanup (%s) complete: %d removed", trigger, removed)
    except Exception as e:
        log.exception("cleanup failed")
        with _state_lock:
            cleanup_state["finished"] = time.time()
        _cleanup_say(f"The check failed: {e}", status="error", running=False)
    finally:
        _run_lock.release()


def start_cleanup(db, trigger="manual"):
    """Start the cleanup in a background thread. False if a refresh or another cleanup is running."""
    if not _run_lock.acquire(blocking=False):
        return False
    with _state_lock:
        cleanup_state.update({"running": True, "status": "starting", "message": "Starting...", "trigger": trigger,
                              "started": time.time(), "finished": None, "removed": 0, "sources": {}})
    events.publish("cleanup_progress", cleanup_snapshot())
    threading.Thread(target=_cleanup_run, args=(db, trigger), daemon=True, name="cleanup").start()
    return True


def scheduled_cleanup(db, wait_minutes=30):
    """The 8 AM job. If a refresh is mid-run, wait for it (up to wait_minutes) instead of skipping the day."""
    deadline = time.time() + wait_minutes * 60
    while not start_cleanup(db, "scheduled"):
        if time.time() > deadline:
            log.warning("scheduled cleanup skipped: a refresh was still running after %d minutes", wait_minutes)
            return False
        time.sleep(20)
    return True
