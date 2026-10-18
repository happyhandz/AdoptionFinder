# Site definitions (generic scraper)

Every `*.json` file in this folder becomes a listing source, with no Python needed. A file holds one
site object or a list of them. Restart the app after adding or editing one.

Requests use `curl_cffi` with a real-browser TLS fingerprint, which gets past filters that reject
plain Python HTTP clients. It is deliberately polite: it obeys robots.txt when readable, waits
`delay_seconds` (default 2) between requests to a host, fetches at most 10 pages, and stops on a
403/429/"access denied" page (the source then shows as degraded). It does not use proxies, solve
captchas or drive a browser. If a site still blocks it, that site stays blocked. Check a site's
terms of use before adding it; this is for personal use.

## Building one
1. `python -m app.webtool probe <url>` shows what the page offers: JSON-LD, embedded JSON, likely
   card elements, known listing platforms (Petango, Shelterluv, ...) and whether it is blocked.
2. Write the definition (below). Many sites load dogs from a JSON endpoint; find it in the browser's
   network tab, or in the site's JavaScript, and use `"mode": "json"`. See `seattle-humane.json`.
3. `python -m app.webtool run sites/yours.json` prints what was extracted. Iterate until it looks right.

## Reference
```
name            unique label, shown in the UI                          (required)
org             organization name shown on the card
location        {"zip": "98087"} fixed location used for the distance filter
transport       true if dogs are shipped in from elsewhere (kept even outside the radius)
urls            list of URLs; "{page}" in a URL is replaced by 1, 2, 3...   (required)
pagination      {"start": 1, "max_pages": 3}   stops early on an empty or repeated page
mode            "css" (default) | "json" | "jsonld" | "embedded_json"
items           css: selector of one dog card.  json/embedded_json: dotted path to the list,
                e.g. "data.animals".  jsonld: optional regex on @type, e.g. "Product|Animal"
blob_regex      embedded_json only: regex whose group 1 is the JSON text in the page
require         fields that must be present, or the item is dropped (filters out ad cards)
breed_split     false if breeds contain commas, e.g. "Terrier, Pit Bull"
delay_seconds   minimum seconds between requests to this host (default 2)
respect_robots  default true
headers         extra request headers
enabled         false to switch a definition off
fields          mapping below                                          (required)
```

### Fields
Targets: `id name url photo breed breed2 age sex size color description city state zip species listed`.

Each field is a string or an object.

| Mode | String form | Object keys |
|---|---|---|
| css | `"h3.title"`, or `"img.pic@src"` for an attribute | `sel`, `attr`, `all` (list), `regex` (group 1), `map`, `default`, `prefix`, `suffix`. `sel: ":scope"` means the card itself, e.g. to read its `id` attribute. |
| json / embedded_json / jsonld | dotted path, e.g. `"pets.0.name"` | `path`, `key` (take this key from each list element, e.g. photos), `join`, `template` (`"https://x/{id}"`), `regex`, `map`, `default`, `prefix`, `suffix` |

Notes: `species` is optional; if present, anything that does not say "dog" is dropped. Ages like
`"2 years"`, `"5 months"` or `"54 months"` are understood (use `suffix` when the number is bare).
Sizes and sex are read from text. If no `id` is given, one is made from the URL.

## Included
- `paws.json`: PAWS Lynnwood, read from the page's dog cards (css mode).
- `seattle-humane.json`: Seattle Humane, read from the JSON feed its own Find-a-Pet page calls.
