# Adoption Finder

> **Status: shelved (October 2026).** The project did its job: a dog was adopted with its help, and it is not in use for now.
> Everything works as of its last run (81 tests pass). To pick it up again, see **Resuming** at the bottom.

Local dog-adoption aggregator. Runs on your PC; UI at http://localhost:8000.

## Setup
1. Get a free RescueGroups key: https://www.rescuegroups.org/?page_id=1004
2. Copy `.env.example` to `.env`, paste the key into `RESCUEGROUPS_API_KEY`.
3. Double-click `run.bat` (first run creates the venv and installs deps).

Try it without a key: set `DEMO=1` in `.env` for sample dogs.

## Sources
| Source | How |
|---|---|
| RescueGroups | Official API (needs the key). |
| King County Regional Animal Services (Kent) | County open-data feed, no key. |
| Shelterluv rescues (20 orgs in WA, OR and ID: Tacoma Humane, Sunny Sky's, Little Dog Rescue, Spokane Humane, SpokAnimal, Dizzy Dog (Portland), Mountain Humane (Hailey), ...) | The public JSON endpoint every Shelterluv embed uses. Add an org in `app/sources/shelterluv.py`. |
| Adopt-a-Pet | No API, and its search pages are disallowed by robots.txt, so it reads the published sitemap (city/state in each pet URL) and fetches pet pages for dogs in your area, about 1/second. First fill takes a while; known dogs are never re-fetched. The sitemap regenerates about daily, so new dogs can lag by up to a day. |
| Oregon Humane Society (Portland + Salem) | Its adopt page carries every pet's data in the page itself (`app/sources/oregonhumane.py`). |
| Petango widget shelters (Greenhill in Eugene, Wenatchee Valley Humane, Better Together in Ponderay, ID) | The public Petango listing widget each shelter embeds on its own site. Add a shelter in `app/sources/petango.py`. |
| 24Pet Connect shelters (Tri-Cities Animal Shelter, Pasco) | The shelter's 24petconnect.com page, 30 dogs at a time. Add one in `app/sources/pet24.py`. |
| Any website (`sites/*.json`) | Config-driven scraper using a browser TLS fingerprint (curl_cffi). See `sites/README.md`. Included: PAWS, Seattle Humane. |
| Petfinder | No API and its pages refuse scripts, so it is fed from a real Chrome window, only when you ask. Press **Petfinder** in the header (PC only): paste your search URLs (the filters are in the URL), then **Check for new dogs** or **Full scrub**. It opens Chrome, walks the pages with a pause of several seconds between them, and imports the dogs. The first run does every page; later runs go newest-first and stop once they reach dogs from the last scrub. **Full scrub** also removes dogs that are no longer listed. If Petfinder shows a check, finish it in the Chrome window or the crawl stops. The older bookmark import still works (`/import`). |

All sources are polled in parallel, and results appear as each one finishes.

Oregon and Idaho dogs appear when your search reaches them: set the location to a city there (e.g. Portland) and tick **Whole state**, or raise the radius, and save the search so it keeps being polled.


## Removing dogs that are no longer listed

Regular refreshes only **add and update** dogs; they never remove any. Removal is a separate **check**: it asks every source for its current list again and removes the dogs that did not come back (adopted or taken down).

- **Automatic:** once a day at **8:00 AM Pacific time** (PST in winter, PDT in summer), while the app is running. If the app was off at 8, it runs on start-up when that is within 6 hours; otherwise it waits for the next morning. If a refresh is mid-run at 8, the check waits for it.
- **Manual:** the **Check listings** button in the header (also in the phone app) runs it right now and shows progress per source.
- **Safeguards:** a source that errors, is blocked, or is cooling down is skipped. A source that returns under 40% of the dogs held (when it holds 5 or more) is treated as incomplete and skipped. Skipped sources show the reason. A dog removed by mistake comes back at the next refresh.
- **Petfinder** is not covered (it is fed by hand): its dogs leave with a **Full scrub**, or after 14 days without being imported.
- **Adopt-a-Pet** keeps adopted pets in its sitemap, so being listed there proves nothing. Its check opens each held dog's own page and removes the dog if the page says it is no longer available. To stay under their firewall it opens at most 450 pages per check (about 30 minutes), never-checked dogs first and then the longest ago checked, closest first; repeated checks work through everything.

## Using it
- **Auto-refresh** dropdown in the header sets the polling interval (default 30 min, remembered). The **Refresh** button polls now. New dogs appear live.
- **Filters:** location + radius (5-200 mi) or **Whole state**, breed, color, age, sex, size, listing age, transport, keyword. Save any combination as a **saved search**; every saved search also widens what gets polled.
- **Duplicates:** the same dog listed on several sites is merged into one card showing every source. If two different dogs get merged, open the card and press **not the same dog** on the listing that doesn't belong; it stays split. Matching uses rescue name, sex, age, breed words, size, color, distance and (for borderline cases) a perceptual hash of the first photo. See `app/dedupe.py`.
- "Seen 3d ago" vs "listed 3d ago": some sources don't publish a listing date; then the badge shows when we first saw the dog.
- Non-localhost requests need `Authorization: Bearer <API_TOKEN>` (for the future phone app).

## Phone app
`phone/AdoptionFinder.apk` shows this same page on your phone, reaching your PC over Tailscale with a private token.
Setup steps and troubleshooting: `phone/README.md`. Quick version: `python -m app.phone_setup` (no token needed on your home Wi-Fi or Tailscale), install Tailscale on both,
restart the app, open `http://localhost:8000/pair`, install the APK and open it.
In the app, menu > **Check for app update** installs newer builds from the PC; the Petfinder panel works from the phone too.

## Layout
`app/` backend (FastAPI + SQLite + APScheduler) | `ui/` web page | `android/` + `phone/` phone app (source, APK, setup) | `sites/` scraper definitions | `RESCUES.md`, `wa_rescues.csv` research on WA rescues | `tests/`

## Tests
`.venv\Scripts\python -m pytest`


## Resuming later
1. Open a terminal in this folder: `python -m venv .venv`, then `.venv\Scripts\pip install -r requirements.txt` (or just double-click `run.bat`, which does both and starts the app).
2. Copy `.env.example` to `.env`. For the phone app on your home network or Tailscale keep `HOST=0.0.0.0` and `REQUIRE_TOKEN=0`. The app generates its own `API_TOKEN` setting if you use token mode.
3. Optional RescueGroups key: the API-key request form was filled in but never submitted. Request one at https://rescuegroups.org/services/request-an-api-key/ and set `RESCUEGROUPS_API_KEY` in `.env`; the source turns on by itself.
4. Check the sources before trusting results: shelter sites change their pages. Run `.venv\Scripts\python -m pytest -q` (the tests use captured pages, so they do not prove the live sites still work), then press **Refresh** and look at the source dots in the header.
5. The saved database (`data/adoption.db`) holds old listings. After a long gap run **Check listings** once so adopted dogs are removed (Adopt-a-Pet verifies 450 dogs per check, so repeat it a few times). Petfinder dogs expire after 14 days without an import.
6. The phone app: `phone/AdoptionFinder.apk` is built from `android/`. Rebuilding needs the JDK/Android SDK/Gradle that were kept in `tools/` (not included in the archive); see `phone/README.md`.

### Ideas not done
- Petstablished as a source (candidate 5 in the research list).
- A Windows start-up entry so the app starts with the PC.
- Verifying Adopt-a-Pet only for dogs matching a saved search, or a per-card "check this dog" button.
