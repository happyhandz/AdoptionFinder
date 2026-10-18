import asyncio
import json
import logging
from contextlib import asynccontextmanager

from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import auth, config, dedupe, events, geo, importer, pairing, poller, search
from .db import DB

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

db = DB()
scheduler = BackgroundScheduler()
MIN_POLL, MAX_POLL = 5, 1440  # minutes; scrapers are rate-limited, so don't allow hammering


def poll_minutes():
    try:
        return float(db.get_setting("poll_minutes", config.POLL_MINUTES))
    except ValueError:
        return config.POLL_MINUTES


def schedule_jobs(sched, database):
    """The two recurring jobs: the refresh (interval from Settings) and the removed-dog check (daily, 8:00 AM Pacific)."""
    sched.add_job(poller.run_poll, "interval", minutes=poll_minutes(), args=[database],
                  id="poll", max_instances=1, coalesce=True)
    # A run missed because the app/PC was off still happens if the app starts within 6 hours of 8 AM; otherwise it waits for tomorrow.
    sched.add_job(poller.scheduled_cleanup, CronTrigger(hour=poller.CLEANUP_AT[0], minute=poller.CLEANUP_AT[1],
                                                       timezone=ZoneInfo(poller.CLEANUP_TZ)),
                  args=[database], id="cleanup", max_instances=1, coalesce=True, misfire_grace_time=6 * 3600)


@asynccontextmanager
async def lifespan(app):
    events.bind_loop(asyncio.get_running_loop())
    schedule_jobs(scheduler, db)
    scheduler.start()
    poller.refresh_async(db)  # initial query builds the listing set
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="AdoptionFinder", lifespan=lifespan)
app.middleware("http")(auth.auth_middleware)
app.include_router(importer.make_router(db))
app.include_router(pairing.router)


@app.get("/api/config")
def get_config():
    origin = geo.geocode(db, config.DEFAULT_LOCATION)
    return {
        "default_location": config.DEFAULT_LOCATION,
        "default_label": origin["label"] if origin else config.DEFAULT_LOCATION,
        "default_radius": config.DEFAULT_RADIUS,
        "poll_minutes": poll_minutes(),
        "has_rescuegroups_key": bool(config.RESCUEGROUPS_API_KEY),
        "demo": config.DEMO,
    }


@app.get("/api/dogs")
def list_dogs(request: Request):
    params = dict(request.query_params)
    origin = search.resolve_origin(db, params)
    if not origin:
        raise HTTPException(400, "Could not find that location. Try a ZIP or 'City, ST'.")
    counts = {}
    options = {}
    dogs = search.search(db, params, counts, options)
    breeds = sorted(options, key=lambda b: (-options[b], b.lower()))
    return {"breeds": [{"name": b, "count": options[b]} for b in breeds], "origin": origin["label"], "count": len(dogs), "dogs": dogs, "source_counts": counts}


@app.get("/api/searches")
def get_searches():
    return db.list_searches()


@app.post("/api/searches")
def create_search(body: dict = Body(...)):
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "name required")
    sid = db.save_search(name, body.get("params") or {})
    poller.refresh_async(db)  # a new saved search may cover new territory
    return {"id": sid}


@app.put("/api/searches/{sid}")
def update_search(sid: int, body: dict = Body(...)):
    db.save_search((body.get("name") or "").strip() or "Untitled", body.get("params") or {}, sid)
    poller.refresh_async(db)
    return {"id": sid}


@app.delete("/api/searches/{sid}")
def delete_search(sid: int):
    db.delete_search(sid)
    return {"ok": True}


@app.post("/api/refresh")
def refresh():
    poller.refresh_async(db)
    return {"started": True}


@app.get("/api/cleanup")
def cleanup_info():
    job = scheduler.get_job("cleanup")
    nxt = job.next_run_time if job else None
    last = db.get_setting("cleanup_last")
    return {"state": poller.cleanup_snapshot(), "last": json.loads(last) if last else None,
            "schedule": "Every day at 8:00 AM Pacific time", "next_run": nxt.timestamp() if nxt else None,
            "next_run_text": nxt.astimezone(ZoneInfo(poller.CLEANUP_TZ)).strftime("%a %b %d, %I:%M %p Pacific") if nxt else None}


@app.post("/api/cleanup")
def cleanup_start():
    if not poller.start_cleanup(db, "manual"):
        raise HTTPException(409, "A refresh or check is already running. Try again in a minute.")
    return {"started": True}


@app.put("/api/settings")
def put_settings(body: dict = Body(...)):
    try:
        m = float(body.get("poll_minutes"))
    except (TypeError, ValueError):
        raise HTTPException(400, "poll_minutes must be a number")
    if not MIN_POLL <= m <= MAX_POLL:
        raise HTTPException(400, f"poll_minutes must be between {MIN_POLL} and {MAX_POLL}")
    db.set_setting("poll_minutes", m)
    scheduler.reschedule_job("poll", trigger="interval", minutes=m)
    return {"poll_minutes": m}


@app.post("/api/split")
def split_listing(body: dict = Body(...)):
    """User says this source's listing is NOT the same dog as the others on its card."""
    try:
        lid = int(body.get("listing_id"))
    except (TypeError, ValueError):
        raise HTTPException(400, "listing_id required")
    others = db.q(
        "SELECT id FROM listings WHERE status='active' AND id!=? AND canonical_id="
        "(SELECT canonical_id FROM listings WHERE id=?)", (lid, lid))
    for o in others:
        db.add_split(lid, o["id"])
    dedupe.run(db, fetch_photos=False)
    events.publish("poll_done", {"new": 0})
    return {"split_from": [o["id"] for o in others]}


@app.get("/api/sources")
def sources():
    status = {s["source"]: s for s in db.statuses()}
    out = []
    for src in poller.SOURCES:
        if getattr(src, "manual", False):  # imported by hand: never "pending" or "degraded"
            out.append({**(status.get(src.name) or {"source": src.name}), "state": "manual",
                        "last_error": "Imported by hand: use Import Petfinder (top right) to add dogs"})
        elif src.enabled():
            out.append(status.get(src.name) or {"source": src.name, "state": "pending"})
    return out


@app.get("/api/events")
async def sse(request: Request):
    q = events.subscribe()

    async def gen():
        try:
            yield "retry: 3000\n\n"
            while not await request.is_disconnected():
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {msg}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            events.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)


@app.get("/")
def index():
    """index.html with ?v=<file mtime> on its CSS/JS, so a changed script is never served from a stale browser cache."""
    html = (config.UI_DIR / "index.html").read_text(encoding="utf-8")
    for name in ("app.js", "styles.css"):
        f = config.UI_DIR / name
        html = html.replace(f"/static/{name}", f"/static/{name}?v={int(f.stat().st_mtime)}")
    return HTMLResponse(html)


@app.middleware("http")
async def always_revalidate_ui(request, call_next):
    """The UI files change while we develop; make browsers check with the server each time (cheap 304s)."""
    resp = await call_next(request)
    path = request.url.path
    if path == "/" or path.startswith("/static/") or path == "/import":
        resp.headers["Cache-Control"] = "no-cache"
    return resp


app.mount("/static", StaticFiles(directory=config.UI_DIR), name="static")
