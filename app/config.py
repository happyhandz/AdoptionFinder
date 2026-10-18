import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_env():
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()

RESCUEGROUPS_API_KEY = os.environ.get("RESCUEGROUPS_API_KEY", "")
API_TOKEN = os.environ.get("API_TOKEN", "")
# 0 = devices on a private network (home Wi-Fi, Tailscale) need no token; public addresses still do
REQUIRE_TOKEN = os.environ.get("REQUIRE_TOKEN", "1") != "0"
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8000"))
POLL_MINUTES = float(os.environ.get("POLL_MINUTES", "30"))
# Safety cap on pages visited per Petfinder search URL by the automated crawl
PETFINDER_MAX_PAGES = int(os.environ.get("PETFINDER_MAX_PAGES", "120"))
DEFAULT_LOCATION = os.environ.get("DEFAULT_LOCATION", "98031")
DEFAULT_RADIUS = int(os.environ.get("DEFAULT_RADIUS", "50"))
DEMO = os.environ.get("DEMO", "0") == "1"
DB_PATH = Path(os.environ.get("DB_PATH", ROOT / "data" / "adoption.db"))
UI_DIR = ROOT / "ui"
USER_AGENT = "AdoptionFinder/0.1 (personal use; local)"
