"""python -m app.phone_setup [--token] : switch on phone access in .env.

  (no flag)  home-network mode: listen on the network; devices on your home Wi-Fi/LAN and Tailscale need no token,
             public internet addresses still do. This is the default so running the command again never turns the
             token requirement back on by accident.
  --token    token mode: every device off this PC must present the token (use if the app is ever reachable
             from the internet).
"""
import re
import secrets
import shutil
import sys

from . import auth, config

ENV = config.ROOT / ".env"
EXAMPLE = config.ROOT / ".env.example"


def set_key(text, key, value):
    line = f"{key}={value}"
    if re.search(rf"^{key}=.*$", text, re.M):
        return re.sub(rf"^{key}=.*$", line, text, flags=re.M)
    return text.rstrip("\n") + "\n" + line + "\n"


def main():
    open_mode = "--token" not in sys.argv     # "--open" is accepted too, and is the default
    if not ENV.exists():
        if EXAMPLE.exists():
            shutil.copy(EXAMPLE, ENV)
        else:
            ENV.write_text("", encoding="utf-8")
    text = ENV.read_text(encoding="utf-8")
    if open_mode:
        text = set_key(text, "HOST", "0.0.0.0")
        text = set_key(text, "REQUIRE_TOKEN", "0")
        ENV.write_text(text, encoding="utf-8")
        print("Home-network mode is switched on in .env (HOST=0.0.0.0, REQUIRE_TOKEN=0).")
        print("Devices on your home Wi-Fi/LAN (and Tailscale) can use the app with nothing to enter;")
        print("requests from public internet addresses still need the token. Restart Adoption Finder.")
        return
    text = set_key(text, "REQUIRE_TOKEN", "1")
    m = re.search(r"^API_TOKEN=(.*)$", text, re.M)
    token = (m.group(1).strip() if m else "")
    created = False
    if len(token) < auth.MIN_TOKEN_LEN:
        token = secrets.token_urlsafe(24)
        text = set_key(text, "API_TOKEN", token)
        created = True
    text = set_key(text, "HOST", "0.0.0.0")
    ENV.write_text(text, encoding="utf-8")
    print("Phone access is switched on in .env:")
    print(f"  HOST=0.0.0.0   (listens on the network; anything not on this PC needs the token)")
    print(f"  API_TOKEN={'(new token generated)' if created else '(kept your existing token)'}")
    print()
    print("Next:")
    print("  1. Restart Adoption Finder (close it and run run.bat).")
    print("  2. Install Tailscale on this PC and the phone (tailscale.com/download) and sign in to the same account.")
    print("  3. Open http://localhost:8000/pair on this PC for the address, token and pairing link.")
    print("  4. (Recommended) Run phone\\allow_tailscale_firewall.ps1 as Administrator so Windows only accepts")
    print("     connections to the app from Tailscale.")


if __name__ == "__main__":
    sys.exit(main())
