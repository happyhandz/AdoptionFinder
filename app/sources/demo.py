"""Fake listings so the UI can be exercised without API keys (DEMO=1)."""
import random
import time

from .. import config
from .base import Source

_NAMES = ["Bella", "Max", "Luna", "Charlie", "Daisy", "Rocky", "Molly", "Bear", "Sadie", "Duke",
          "Zoe", "Finn", "Piper", "Gus", "Nala", "Otis", "Ruby", "Tank", "Willow", "Cooper"]
_BREEDS = ["Labrador Retriever", "Border Collie", "Pit Bull Terrier", "Beagle", "German Shepherd Dog",
           "Chihuahua", "Australian Cattle Dog", "Husky", "Corgi", "Poodle"]
_COLORS = ["Black", "Brown", "Tan", "White", "Black and White", "Brindle", "Yellow"]
_AGES = [("puppy", 3), ("puppy", 8), ("young", 18), ("adult", 48), ("senior", 110)]
_CITIES = [("Kent", "WA", "98031", 47.38, -122.23), ("Tacoma", "WA", "98402", 47.25, -122.44),
           ("Seattle", "WA", "98101", 47.61, -122.33), ("Olympia", "WA", "98501", 47.04, -122.90),
           ("Portland", "OR", "97201", 45.50, -122.68)]

_counter = {"n": 0}


class Demo(Source):
    name = "demo"

    def enabled(self):
        return config.DEMO

    def fetch(self, region, db=None):
        rng = random.Random(42)
        out = []
        for i in range(24):
            grp, months = rng.choice(_AGES)
            city, st, zp, lat, lon = rng.choice(_CITIES)
            name = _NAMES[i % len(_NAMES)]
            out.append(self._make(i, name, rng.choice(_BREEDS), rng.choice(_COLORS), grp, months,
                                  city, st, zp, lat, lon, rng, days=rng.randint(0, 40)))
        # one brand-new dog each poll so live-update can be seen
        _counter["n"] += 1
        n = _counter["n"]
        if n > 1:
            for k in range(1, n):
                out.append(self._make(100 + k, f"NewPup{k}", "Golden Retriever", "Yellow", "puppy", 3,
                                      "Kent", "WA", "98031", 47.38, -122.23, rng, days=0,
                                      transport=(k % 2 == 0)))
        return out

    @staticmethod
    def _make(i, name, breed, color, grp, months, city, st, zp, lat, lon, rng, days, transport=False):
        return {
            "source": "demo", "source_id": f"demo-{i}", "url": "https://example.com/dog/%d" % i,
            "name": name, "breed_primary": breed, "breed_secondary": None, "mixed": i % 3 == 0,
            "color": color, "age_group": grp, "age_text": f"{months} months", "age_months": months,
            "sex": rng.choice(["male", "female"]), "size": rng.choice(["small", "medium", "large"]),
            "description": "Friendly and playful." + (" Transport available." if transport else ""),
            "photos": [f"https://placedog.net/500/400?id={(i % 90) + 1}"],
            "city": city, "state": st, "zip": zp, "lat": lat, "lon": lon, "org": "Demo Rescue",
            "transport": 1 if transport else 0,
            "listed_at": time.time() - days * 86400,
        }
