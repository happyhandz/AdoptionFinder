"""/pair: shows how to connect the phone app. Only reachable from this PC (it displays the token)."""
import hashlib
import html as htmllib
import json
import ipaddress
import shutil
import socket
import subprocess
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from . import auth, config

router = APIRouter()
APK_PATH = config.ROOT / "phone" / "AdoptionFinder.apk"
VERSION_PATH = config.ROOT / "phone" / "version.json"
TAILSCALE_NET = ipaddress.ip_network("100.64.0.0/10")
_TS_PATHS = [r"C:\Program Files\Tailscale\tailscale.exe", r"C:\Program Files (x86)\Tailscale\tailscale.exe"]


def tailscale_ip():
    """This PC's Tailscale IPv4 address, or None if Tailscale isn't installed / connected."""
    exe = shutil.which("tailscale") or next((p for p in _TS_PATHS if shutil.which(p)), None)
    if exe:
        try:
            out = subprocess.run([exe, "ip", "-4"], capture_output=True, text=True, timeout=4).stdout.split()
            if out:
                return out[0]
        except Exception:
            pass
    try:  # fall back to scanning this machine's addresses for one in Tailscale's range
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if ipaddress.ip_address(ip) in TAILSCALE_NET:
                return ip
    except Exception:
        pass
    return None


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))  # no packet is sent; just picks the outbound interface
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


def pairing_link(host, port, token):
    return f"adoptionfinder://connect?url={quote(f'http://{host}:{port}', safe='')}&token={quote(token, safe='')}"


def qr_svg(text):
    try:
        import qrcode
        import qrcode.image.svg
        img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
        return img.to_string(encoding="unicode")
    except Exception:
        return ""


def _card(title, host, port, token, note):
    link = pairing_link(host, port, token)
    qr = qr_svg(link)
    return (f"<div class=box><h3>{htmllib.escape(title)}</h3>"
            f"<p>Address for the app: <code>http://{htmllib.escape(host)}:{port}</code></p>"
            f"<p class=note>{note}</p>"
            f"<p>Pairing link (open it on the phone with the app installed): <br>"
            f"<textarea readonly rows=3 onclick='this.select()'>{htmllib.escape(link)}</textarea></p>"
            f"{('<div class=qr>' + qr + '</div>') if qr else ''}</div>")


@router.get("/apk", include_in_schema=False)
def apk():
    """The phone app installer. Public on the network on purpose (see auth.PUBLIC_GET_PATHS): it holds no secrets."""
    if not APK_PATH.exists():
        raise HTTPException(404, "APK not built yet: run phone\\build_apk.ps1")
    return FileResponse(APK_PATH, media_type="application/vnd.android.package-archive", filename="AdoptionFinder.apk")


@router.get("/apk/version", include_in_schema=False)
def apk_version():
    """Which build of the phone app this PC has (for the app's "Check for app update"). Public: no secrets in it."""
    try:
        data = json.loads(VERSION_PATH.read_text(encoding="utf-8-sig")) if VERSION_PATH.exists() else {}
    except ValueError:
        data = {}
    data.setdefault("versionCode", 0)
    if APK_PATH.exists():  # size and checksum always describe the file that /apk actually serves
        blob = APK_PATH.read_bytes()
        data["size"], data["sha256"] = len(blob), hashlib.sha256(blob).hexdigest()
    else:
        data["versionCode"] = 0
    return JSONResponse(data, headers={"Cache-Control": "no-store"})


def _install_card(hosts):
    rows = []
    for label, host in hosts:
        url = f"http://{host}:{config.PORT}/apk"
        qr = qr_svg(url)
        rows.append(f"<p><b>{htmllib.escape(label)}:</b> <code>{htmllib.escape(url)}</code></p>"
                    f"{('<div class=qr>' + qr + '</div>') if qr else ''}")
    if not rows:
        return ""
    state = "" if APK_PATH.exists() else "<p class=note><b>AdoptionFinder.apk is not built yet.</b></p>"
    return ("<div class=box><h3>Install the app on the phone</h3>"
            "<p>On the phone, open this address in its browser (or scan the code), download the file, and open it to install. "
            "No token is needed for this step: the installer contains no secrets.</p>" + state + "".join(rows) + "</div>")


_STYLE = """<style>body{font:16px/1.5 system-ui;max-width:720px;margin:30px auto;padding:0 16px;color:#1d2330}
.box{border:1px solid #ccd;border-radius:10px;padding:12px 18px;margin:16px 0}.warn{background:#fff4e5;border-color:#e0a050}
code{background:#eef;padding:1px 5px;border-radius:4px}textarea{width:100%;font:12px monospace}.note{color:#667}
.qr svg{width:220px;height:220px}</style>"""
_HEAD = "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"


@router.get("/pair", response_class=HTMLResponse)
def pair(request: Request):
    if not auth.is_local(request):
        raise HTTPException(404)
    token = config.API_TOKEN
    port = config.PORT
    listening_ok = config.HOST not in auth.LOOPBACK
    ts, lan = tailscale_ip(), lan_ip()
    install = _install_card([h for h in (("Home Wi-Fi", lan), ("Tailscale", ts)) if h[1]])

    if listening_ok and not config.REQUIRE_TOKEN:
        # home-network mode: nothing to pair
        addr = f"http://{lan}:{port}" if lan else "(this PC's address)"
        return HTMLResponse(f"""{_HEAD}<title>Install the phone app</title>{_STYLE}
<h1>Install the phone app</h1>
<div class=box><b>No pairing and no token.</b> Install the app below, open it, and it connects to this PC by itself
(it is built to look for <code>{htmllib.escape(addr)}</code>). It works while the phone is on your home Wi-Fi and this PC is on
with Adoption Finder running.</div>
{install}
<div class=box><b>Who can open it?</b> Any device on your home network. Requests from the public internet still need the token,
so an accidental router port-forward does not expose it. Use a guest Wi-Fi network for visitors' phones if you
don't want them able to open it.</div>
<p class=note>If the PC's address ever changes, change it in the app (menu, Server settings), or give this PC a fixed address in your router (DHCP reservation).</p>
<p><a href="/">Back to Adoption Finder</a></p>""")

    body = [install]
    if not token or not listening_ok:
        body.append("<div class='box warn'><b>Phone access is not switched on yet.</b> Run "
                    "<code>.venv\\Scripts\\python -m app.phone_setup</code> (nothing to enter on your home network or Tailscale), "
                    "then restart Adoption Finder. Add <code>--token</code> to require a token instead.</div>")
    if ts:
        body.append(_card("Over Tailscale (works anywhere)", ts, port, token or "(token not set)",
                          "Tailscale must be connected on the PC and the phone."))
    else:
        body.append("<div class='box warn'><b>Tailscale not detected on this PC.</b> Install it from tailscale.com/download, "
                    "sign in, then reload this page.</div>")
    if lan:
        body.append(_card("Home Wi-Fi only", lan, port, token or "(token not set)",
                          "Works only while the phone is on the same Wi-Fi as this PC."))
    return HTMLResponse(f"""{_HEAD}<title>Pair the phone app</title>{_STYLE}
<h1>Pair the phone app</h1>
<p>Install <b>AdoptionFinder.apk</b> on the phone, then either open a pairing link below on the phone, or type the address and
token into the app's Server settings. <b>The token is a password: don't share it or post this page.</b></p>
<div class=box><b>Token:</b> <code>{htmllib.escape(token) if token else '(not set)'}</code></div>
{''.join(body)}
<p><a href="/">Back to Adoption Finder</a></p>""")
