import time

import httpx

from .. import config, normalize
from .base import Source

BASE = "https://api.rescuegroups.org/v5"
PAGE = 100


class RescueGroups(Source):
    name = "rescuegroups"

    def enabled(self):
        return bool(config.RESCUEGROUPS_API_KEY)

    def fetch(self, region, db=None):
        headers = {
            "Authorization": config.RESCUEGROUPS_API_KEY,
            "Content-Type": "application/vnd.api+json",
            "User-Agent": config.USER_AGENT,
        }
        body = {"data": {"filterRadius": {"miles": int(region["radius"]), "postalcode": region["zip"]}}}
        out, page = [], 1
        with httpx.Client(timeout=30, headers=headers) as client:
            while True:
                r = client.post(
                    f"{BASE}/public/animals/search/available/dogs/haspic",
                    params={"limit": PAGE, "page": page, "include": "pictures,orgs,locations",
                            "sort": "-animals.createdDate"},
                    json=body,
                )
                r.raise_for_status()
                payload = r.json()
                out.extend(self._parse(payload))
                pages = payload.get("meta", {}).get("pages", 1)
                if page >= pages:
                    break
                page += 1
                time.sleep(0.5)
        return out

    @staticmethod
    def _parse(payload):
        inc = {(i["type"], i["id"]): i.get("attributes", {}) for i in payload.get("included", [])}

        def related(rec, rel):
            data = (rec.get("relationships", {}).get(rel) or {}).get("data") or []
            if isinstance(data, dict):
                data = [data]
            return [inc.get((d["type"], d["id"]), {}) for d in data]

        listings = []
        for rec in payload.get("data", []):
            a = rec.get("attributes", {})
            pics = sorted(related(rec, "pictures"), key=lambda p: p.get("order", 0))
            photos = [(p.get("large") or p.get("original") or {}).get("url") for p in pics]
            photos = [p for p in photos if p]
            if not photos and a.get("pictureThumbnailUrl"):
                photos = [a["pictureThumbnailUrl"]]
            loc = (related(rec, "locations") or [{}])[0]
            org = (related(rec, "orgs") or [{}])[0]
            desc = a.get("descriptionText") or normalize.strip_html(a.get("descriptionHtml"))
            age_text = a.get("ageString")
            months = normalize.parse_age_months(age_text)
            listings.append({
                "source": "rescuegroups",
                "source_id": str(rec["id"]),
                "url": a.get("url") or f"https://www.rescuegroups.org/animal/{rec['id']}",
                "name": a.get("name"),
                "breed_primary": a.get("breedPrimary"),
                "breed_secondary": a.get("breedSecondary"),
                "mixed": 1 if a.get("isBreedMixed") else 0,
                "color": a.get("colorDetails") or a.get("coloration"),
                "age_group": normalize.age_group_from_label(a.get("ageGroup"))
                or normalize.age_group_from_months(months),
                "age_text": age_text,
                "age_months": months,
                "sex": (a.get("sex") or "").lower() or None,
                "size": normalize.norm_size(a.get("sizeGroup")),
                "description": desc,
                "photos": photos,
                "city": loc.get("city") or org.get("city"),
                "state": loc.get("state") or org.get("state"),
                "zip": loc.get("postalcode") or org.get("postalcode"),
                "lat": loc.get("lat"),
                "lon": loc.get("lon"),
                "org": org.get("name"),
                "transport": normalize.detect_transport(desc),
                "listed_at": normalize.to_epoch(a.get("createdDate")),
            })
        return listings
