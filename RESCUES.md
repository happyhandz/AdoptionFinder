# Local rescues & shelters: coverage and how to pull each

Researched 2026-09-30 for the Kent, WA area. "Verified" means I fetched the page or feed and saw the data;
"Unverified" means it is inferred from the site's links/embeds and still needs a look.
robots.txt has not been checked for the direct-scrape candidates. Check it before building each one.

## Already covered by the app
| Source | Covers | Method |
|---|---|---|
| King County Regional Animal Services (Kent) | 23 dogs at the Kent adoption center | Public Socrata JSON feed. **Built.** |
| Adopt-a-Pet (via sitemap + pet pages) | Any rescue that lists there, e.g. The Haus of Dogs (Edmonds), Northwest Working Breed Rescue (Renton), Casa Dog, Tiramisu Dog Rescue, Wet Noses Foster Paws, Fur Ever Yours | **Built.** |
| RescueGroups API | Every rescue on RescueGroups, e.g. Dog Gone Seattle (uses the RescueGroups toolkit, verified) | **Built**, waiting on your API key |

## Direct-scrape candidates (not covered yet)
| Org | Location | Platform | Best way to pull | Status |
|---|---|---|---|---|
| Humane Society for Tacoma & Pierce County | Tacoma | WordPress page filled from Shelterluv | Parse `thehumanesociety.org/adoptable-pet-category/dogs/`. Each dog is a `pet-card` with name, Pet ID, breed, sex, age, status, photo and a detail link (16 dogs seen). Plain HTTP works. | **Verified. Best next one.** |
| Auburn Valley Humane Society | Auburn | WordPress + PetPoint (shelter id 6156) | The site's adoptable-pets page (`/pet-adoption/`) or the PetPoint adoptable list. I haven't found the dog list yet. | Unverified |
| Kitsap Humane Society | Silverdale | WordPress; pets pulled from Petfinder | `/adopt/available-pets/`. It is also on Petfinder, which we can't read. | Unverified |
| Puget Sound Rescue | Puyallup | Custom site | `/dogs-for-adoption` page. Platform unknown; needs a custom parser. | Unverified |
| NorthWest Wag Rescue | Gig Harbor | Wix site + Petstablished forms | Petstablished's public listing page for their org. Not yet located. | Unverified |
| Sunny Sky's Animal Rescue | Puget Sound area | Shelterluv "MatchMe" (id `SSAR`) | The MatchMe page is built with Livewire (JavaScript), so a plain fetch has no dog data. Needs a headless browser, or rely on their Petfinder listing. | Hard |
| Second Chance Dogs | Puget Sound area | WordPress, no listing platform found | Custom parser, or skip. Posts mostly to Facebook. | Unverified |
| Reber Ranch adoptions | Kent | Shopify store page | Static text page with no listing feed found. Check it by hand. | Low value |

## Blocked or unreachable (not pursued)
| Org | Problem |
|---|---|
| Seattle Humane (Bellevue) | Bot protection returns HTTP 403, same as Petfinder. Not bypassing. |
| PAWS (Lynnwood) | Same, HTTP 403. |
| Seattle Animal Shelter | Adoption page exists (`/animal-shelter/find-an-animal/adopt`) but I haven't inspected the listing. |
| Everett Animal Shelter, Thurston County (Olympia), Tacoma Metro Animal Control | I guessed URLs that failed or returned 404. Need the correct adoption page URLs. |
| Resilient Hearts, Path of Hope Rescue, Church of Pug | Site loaded empty (JavaScript), or the guessed URL did not connect. Need the correct URLs. |

## Recommendation
1. Don't write a scraper per rescue up front. Many local rescues already post on Adopt-a-Pet or RescueGroups, which the app reads. Once RescueGroups is live, compare each org's website list against the app's results, and only build direct scrapers for the gaps.
2. Build the Tacoma Humane Society parser next. It's the only large shelter I verified as easy to read.
3. Then Auburn Valley (PetPoint) and Puget Sound Rescue, after I find their dog lists.
4. Keep each direct scraper small and separate, one file per org, so a site redesign only breaks that one.
5. Shelters behind bot protection (Seattle Humane, PAWS) stay on email alerts.

---

# Statewide (all of Washington)

Full list: **`wa_rescues.csv`** (about 190 organizations: name, city, website, listing platform, Adopt-a-Pet page).
Built 2026-09-30 by crawling every Washington organization page in Adopt-a-Pet's published shelter sitemap, then
fetching each organization's own website to see what it uses to list dogs. This finds orgs that post on Adopt-a-Pet.
Shelters that are not on Adopt-a-Pet (e.g. SpokAnimal, Wenatchee Valley Humane, Humane Society of Skagit Valley's own
site) may be missing, and I did not confirm every listed org is dog-focused (some cat rescues appear).
Dog counts are only approximate (Adopt-a-Pet's page shows at most 24).

## What the sites use to list dogs
| Platform | Orgs | How we get them |
|---|---|---|
| Own website, no recognizable platform | ~97 | Only via Adopt-a-Pet/RescueGroups if they also post there; otherwise a custom scraper each |
| Also list on Adopt-a-Pet (mentioned on site) | ~27 | Already covered by the Adopt-a-Pet source |
| Also list on Petfinder | ~21 | Petfinder is blocked; covered only where they also post on Adopt-a-Pet/RescueGroups |
| **Shelterluv** | 15 | Best shared target: one scraper could serve all. Includes Humane Society for Tacoma & Pierce, Spokane Humane, Whitman County Humane, Colville Valley Animal Sanctuary, Rise Rescue Alliance (Port Angeles), Main Street Mutt Rescue (Ferndale), Pacific Rim Rescue, Little Dog Rescue, Sunny Sky's, The Pit Bull Pen |
| **RescueGroups** | 11 | Covered by the RescueGroups API once the key is in (Dog Gone Seattle, HOPE in Kent/Spokane, Friends for Life, A Doggie 4 You, Greyhound Pets, Collar of Hope, RPR Senior Dogs, Welfare for Animals Guild, ...) |
| Bot-protected (HTTP 403) | 9 | Not pursued: Seattle Humane, PAWS Lynnwood, Wagabond Dog Rescue (Republic), others |
| No website / Facebook-Instagram only | 26 | Not scrapable; rely on Adopt-a-Pet |
| Best Friends network | 5 | CARES Burien, PAWS of Grays Harbor, ... (Best Friends has its own pet search; not investigated) |
| Petstablished | 2 | NorthWest Wag Rescue, Little Lives Small Animal Rescue |
| 24Pet Connect | 1 | Tri-Cities Animal Shelter (Pasco) |
| ShelterManager | 1 | Haven (Seattle) |
| Unreachable | 3 | Bonney Lake and Bothell rescues |

## Regional shelters worth knowing (statewide)
- **Western WA:** Seattle Animal Shelter, Everett Animal Shelter, Kitsap Humane, Humane Society for SW Washington (Vancouver), Humane Society Mason County, Humane Society of Jefferson County, Joint Animal Services (Olympia), Whatcom-area rescues.
- **Eastern WA:** Spokane Humane, Yakima Humane, Blue Mountain Humane (Walla Walla), Tri-Cities Animal Shelter, Whitman County Humane, Colville Valley Animal Sanctuary.
- Found by web search but not in the Adopt-a-Pet crawl: SpokAnimal (spokanimal.org), Wenatchee Valley Humane Society (wenatcheehumane.org/dogs), Whatcom Humane Society, Humane Society of Skagit Valley, Rescue4All (Spokane). These need direct look-ups.

## Recommended plan for statewide coverage
1. **Widen the search area.** The app's radius slider stops at 200 miles from a location, and Adopt-a-Pet/RescueGroups queries are region-based. Add a "whole state" option so Spokane, Yakima and the Tri-Cities are included (Kent to Spokane is about 280 miles).
2. **One Shelterluv scraper** covers 15 orgs, including big ones (Tacoma, Spokane, Whitman). Worth building after checking how their listing pages load.
3. **Do not scrape the ~97 unrecognized own-sites one by one.** Compare against Adopt-a-Pet/RescueGroups results first and build direct scrapers only for orgs that are missing from both.

---

## Update: generic scraper (curl_cffi)

`app/sources/webscrape.py` plus `sites/*.json` (see `sites/README.md`) can scrape any site from a JSON
definition. Tried against the blocked sites on 2026-09-30:

| Site | Result |
|---|---|
| Seattle Humane | Works. Their page loads dogs from a JSON feed (`sites/seattle-humane.json`, 41 dogs). |
| PAWS Lynnwood | Works (`sites/paws.json`, dog cards on the page). |
| Wagabond Dog Rescue (Republic) | Still blocked (403). |
| **Petfinder** | **Still blocked**: listing pages answer 403 or a "temporarily unavailable" page. Its robots.txt allows crawling and its sitemap loads, but the sitemap only has breed/location pages, not individual pets. Left as email alerts. |


---

# Oregon and Idaho, plus new candidates (researched 2026-10-02)

Full lists: **`orid_rescues.csv`** (153 Oregon/Idaho organizations from Adopt-a-Pet's shelter sitemap, with website and listing platform)
and `wa_rescues.csv`. Open-data portals (Socrata) have no adoptable-dog feed for WA/OR/ID other than King County, which is already a source.

## Ranked candidates to add as sources
| # | Candidate | Covers | Verified how | Effort |
|---|---|---|---|---|
| 1 | **Petango widget scraper** (one source, a list of org widget URLs) | Greenhill Humane (Eugene OR, 30 dogs), Wenatchee Valley Humane (WA, 24), Better Together Animal Alliance (Ponderay ID), Auburn Valley (PetPoint) | The public widget URL (with its public authkey, found in each org's page) returned the full list with names, photos and links | Small: one parser, one config line per org |
| 2 | **24Pet Connect scraper** | Tri-Cities Animal Shelter (Pasco WA), Yakima Humane (WA) | `24petconnect.com/PP6628?at=DOG` returned a result grid with photos and ids | Small |
| 3 | **Oregon Humane Society** (Portland + Salem, largest in the Northwest) | ~100+ dogs | Server-rendered `pet-card` blocks: name, age, sex, weight, campus, link | Very small: a `sites/*.json` definition, no new code |
| 4 | **More Shelterluv orgs** (add to the existing list) | SpokAnimal (WA), Dizzy Dog Animal Rescue / DDAR (Portland transport, 21 dogs), Senior Dog Rescue of Oregon (Corvallis, id 10355), Oregon Pet Project (Newberg, id 29277), Mountain Humane (Hailey ID), PAWS (Driggs ID), Lake Lowell Animal Rescue (Nampa ID), Priest River Animal Rescue (ID) | Same public Shelterluv feed already used for 12 WA rescues | Tiny, once each numeric id is found (two are known) |
| 5 | **Petstablished pages** | Saving One Paw At A Time (21 dogs), JMM Rescue (11), Heart of Idaho Animal Sanctuary (10), Fuzzy Pawz (Boise, 7), NorthWest Wag Rescue (WA) | Platform identified only; listing pages not tested | Medium |
| 6 | RescueGroups API | Woofs and Wags (Florence OR, 16), Mastiff Rescue Oregon, New Rattitude and more | Covered automatically once the API key is in | None |

## Hard or not worth it
- **Multnomah County Animal Services (Troutdale), Idaho Humane Society (Boise), Humane Society of Central Oregon (Bend):** the dog lists load with JavaScript and no known platform shows in the page. They would need a look at the browser's network traffic first. Idaho Humane is on Adopt-a-Pet.
- **About 74 Oregon/Idaho rescues with their own sites, and 23 with no website or only social media:** each is small. Adopt-a-Pet already lists them (152 of 153 organizations show dogs there, 1,523 in total), so scraping them one by one isn't worth it.
- Cat-only rescues appear in the Shelterluv list (Kokua Cats, Cat Rescue & Adoption Network, Silverton Cat Rescue): skip.

## Coverage today
Oregon and Idaho dogs already come in through Adopt-a-Pet wherever your search area reaches (Portland is about 150 miles from Kent; Boise and Eugene are beyond the 200-mile slider). To see them, set the location to a city there (e.g. Portland) and tick **Whole state**, or raise the radius.


## Status of the ranked candidates (updated 2026-10-02)
| # | Candidate | Status |
|---|---|---|
| 1 | Petango widget scraper | **Built** (`app/sources/petango.py`): Greenhill (Eugene OR, 33 dogs), Wenatchee Valley Humane (WA, 25), Better Together (Ponderay ID, 16; its cats are dropped). Add more shelters with one line each. |
| 2 | 24Pet Connect scraper | **Built** (`app/sources/pet24.py`): Tri-Cities Animal Shelter (Pasco WA, 38 dogs; pages by offset). Yakima Humane turned out not to link to 24Pet from its pages, so it is not included. |
| 3 | Oregon Humane Society | **Built** (`app/sources/oregonhumane.py`): 56 dogs across Portland (35) and Salem (21). Breed names come from the page's own breed list; a few breed codes it doesn't list show as "Mixed Breed". No listing date is published. |
| 4 | More Shelterluv rescues | **Added** to the existing source: SpokAnimal, Dizzy Dog Animal Rescue (Portland, 54 dogs), Senior Dog Rescue of Oregon, Oregon Pet Project, Mountain Humane (ID), PAWS of Teton Valley (ID), Lake Lowell Animal Rescue (ID), Priest River Animal Rescue (ID). Listings now take their state from the rescue's location. |
| 5 | Petstablished pages | Not started (platform identified only; listing pages untested). |
| 6 | RescueGroups | Automatic once the API key is in. |
