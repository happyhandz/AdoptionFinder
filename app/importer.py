"""Routes for importing pages the user is looking at (currently Petfinder) into the app."""
import hmac
import json
import html as htmllib
import secrets
import time
from urllib.parse import parse_qs

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import HTMLResponse

from . import auth, config, dedupe, events, petfinder_crawl
from .sources import petfinder

MAX_BYTES = 30_000_000
LAST_PAGE = config.ROOT / "data" / "last_petfinder_import.html"


def _token(db):
    t = db.get_setting("import_token")
    if not t:
        t = secrets.token_urlsafe(16)
        db.set_setting("import_token", t)
    return t


def _run_import(db, page_html, url):
    LAST_PAGE.parent.mkdir(exist_ok=True)
    LAST_PAGE.write_text(page_html, encoding="utf-8")  # kept so the parser can be tuned against what was really sent
    found, new = petfinder.import_page(db, page_html, url)
    dedupe.run(db, fetch_photos=False)
    events.publish("source_done", {"source": petfinder.SOURCE, "new": new})
    return found, new


def make_router(db):
    router = APIRouter()

    @router.post("/api/import/petfinder")
    def import_json(request: Request, body: dict = Body(...)):
        if not auth.is_local(request):
            raise HTTPException(404)
        page = body.get("html") or ""
        if len(page) < 200:
            raise HTTPException(400, "html is empty")
        found, new = _run_import(db, page, body.get("url") or "https://www.petfinder.com/")
        return {"found": found, "new": new}

    @router.post("/import/petfinder", response_class=HTMLResponse)
    async def import_form(request: Request):
        if not auth.is_local(request):
            raise HTTPException(404)
        raw = await request.body()
        if len(raw) > MAX_BYTES:
            return HTMLResponse(_result_page("That page is too large to import.", 0, 0), status_code=413)
        form = {k: v[0] for k, v in parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True).items()}
        if not hmac.compare_digest(form.get("token", ""), _token(db)):
            return HTMLResponse(_result_page("Bad import token. Re-drag the bookmarklet from the Import page.", 0, 0),
                                status_code=403)
        found, new = _run_import(db, form.get("html", ""), form.get("url") or "https://www.petfinder.com/")
        return HTMLResponse(_result_page(None, found, new))

    # ---- automated Petfinder crawl: started from the web page on the PC or the phone app; the crawl itself
    # ---- runs in Chrome on the PC. Same access rules as the rest of the app (private network, or the token).
    def _saved_urls():
        raw = db.get_setting("pf_urls")
        return petfinder_crawl.clean_urls(raw) if raw else [petfinder_crawl.DEFAULT_URL]

    @router.get("/api/petfinder/crawl")
    def crawl_info():
        urls = _saved_urls()
        scrubbed = {u: len(petfinder_crawl._scrub_ids(db, petfinder_crawl.url_key(u))) for u in urls}
        last = db.get_setting("pf_last_run")
        return {"state": petfinder_crawl.snapshot(), "urls": urls, "scrubbed": scrubbed,
                "pace": db.get_setting("pf_pace") or petfinder_crawl.DEFAULT_PACE, "paces": petfinder_crawl.PACES,
                "last_run": json.loads(last) if last else None, "chrome": bool(petfinder_crawl.find_chrome())}

    @router.put("/api/petfinder/urls")
    def save_urls(body: dict = Body(...)):
        urls = petfinder_crawl.clean_urls(body.get("urls") or "")
        if not urls:
            raise HTTPException(400, "Add at least one petfinder.com search URL")
        db.set_setting("pf_urls", "\n".join(urls))
        return {"urls": urls}

    @router.post("/api/petfinder/crawl")
    def crawl_start(body: dict = Body(default={})):
        mode = body.get("mode", "auto")
        if mode not in ("auto", "full", "update"):
            raise HTTPException(400, "mode must be auto, full or update")
        pace = body.get("pace") or db.get_setting("pf_pace") or petfinder_crawl.DEFAULT_PACE
        if pace not in petfinder_crawl.PACES:
            raise HTTPException(400, "pace must be one of: " + ", ".join(petfinder_crawl.PACES))
        if not petfinder_crawl.find_chrome():
            raise HTTPException(400, "Google Chrome was not found on this PC")
        if not petfinder_crawl.start(db, _saved_urls(), mode, pace):
            raise HTTPException(409, "A Petfinder crawl is already running")
        db.set_setting("pf_pace", pace)
        return {"started": True, "mode": mode, "pace": pace}

    @router.post("/api/petfinder/crawl/stop")
    def crawl_stop():
        petfinder_crawl.request_stop()
        return {"stopping": petfinder_crawl.is_running()}

    @router.get("/import", response_class=HTMLResponse)
    def import_page(request: Request):
        if not auth.is_local(request):
            raise HTTPException(404)
        base = f"{request.url.scheme}://{request.url.netloc}"
        return HTMLResponse(_instructions_page(base, _token(db), db.statuses()))

    return router


def _result_page(error, found, new):
    if error:
        msg = f"<h2>Not imported</h2><p>{htmllib.escape(error)}</p>"
    elif found == 0:
        msg = ("<h2>No dogs recognized on that page</h2><p>Make sure you clicked the button on a Petfinder "
               "<b>search results</b> page with dogs showing. The page was saved to "
               "<code>data/last_petfinder_import.html</code> so the parser can be fixed.</p>")
    else:
        msg = f"<h2>Imported {found} dog{'s' if found != 1 else ''}</h2><p>{new} new to the app.</p>"
    return ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>Petfinder import</title><body style='font:16px system-ui;max-width:560px;margin:60px auto;padding:0 16px'>"
            f"{msg}<p><a href='/'>Open Adoption Finder</a> &middot; you can close this tab.</p></body>")


def _bookmarklet(base, token):
    return ("javascript:(function(){var f=document.createElement('form');f.method='POST';f.target='_blank';f.acceptCharset='UTF-8';"
            f"f.action='{base}/import/petfinder';"
            f"[['token','{token}'],['url',location.href],['html',document.documentElement.outerHTML]]"
            ".forEach(function(p){var t=document.createElement('textarea');t.name=p[0];t.value=p[1];f.appendChild(t)});"
            "f.style.display='none';document.body.appendChild(f);f.submit()})()")


def _instructions_page(base, token, statuses):
    bm = _bookmarklet(base, token)
    st = next((s for s in statuses if s["source"] == petfinder.SOURCE), None)
    last = "never" if not st or not st.get("last_ok") else time.strftime("%b %d %I:%M %p", time.localtime(st["last_ok"]))
    return """<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>
<title>Import Petfinder</title>
<style>body{font:16px/1.5 system-ui;max-width:680px;margin:40px auto;padding:0 16px;color:#1d2330}
a.bm{display:inline-block;background:#2f6fed;color:#fff;padding:10px 16px;border-radius:8px;text-decoration:none;font-weight:600}
code{background:#eef;padding:1px 5px;border-radius:4px} li{margin:6px 0} .box{border:1px solid #ccd;border-radius:8px;padding:12px 16px;margin:18px 0}</style>
<h1>Import dogs from Petfinder</h1>
<p>Petfinder blocks automated access, so this works the other way: <b>you</b> browse Petfinder normally, then send the page you
are looking at to this app. Nothing contacts Petfinder from here. Last import: <b>__LAST__</b>.</p>
<div class=box><h3>1. Add the button (once)</h3>
<p>Drag this to your bookmarks bar (Ctrl+Shift+B shows the bar):</p>
<p><a class=bm href="__BM__">Send to Adoption Finder</a></p></div>
<div class=box><h3>2. Use it</h3><ol>
<li>On petfinder.com, search dogs near you with the filters you want.</li>
<li>Scroll to load the dogs you want (it only sends what the page has loaded) and click the bookmark.</li>
<li>A new tab says how many dogs were imported. Repeat for other searches or page 2, 3...</li></ol>
<p>Imported dogs stay for __DAYS__ days after you last imported them, then disappear unless you import again.</p></div>
<div class=box><h3>Or upload a saved page</h3>
<p>Save the Petfinder results page (Ctrl+S, "Webpage, HTML only") and choose it here:</p>
<input type=file id=f accept=".html,.htm"> <button id=go>Import file</button> <p id=out></p></div>
<script>
document.getElementById('go').onclick = async () => {
  const file = document.getElementById('f').files[0]; const out = document.getElementById('out');
  if (!file) { out.textContent = 'Choose a file first.'; return; }
  out.textContent = 'Importing...';
  try {
    const r = await fetch('/api/import/petfinder', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({html: await file.text(), url: 'https://www.petfinder.com/'})});
    const j = await r.json(); out.textContent = r.ok ? 'Imported ' + j.found + ' dogs (' + j.new + ' new).' : (j.detail || 'Failed');
  } catch (e) { out.textContent = 'Failed: ' + e; }
};
</script>
<p><a href="/">Back to Adoption Finder</a></p>""".replace("__LAST__", last).replace(
        "__BM__", htmllib.escape(bm, quote=True)).replace("__DAYS__", str(petfinder.EXPIRE_DAYS))
