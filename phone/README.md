# Phone app

`AdoptionFinder.apk` is a small Android app that opens the Adoption Finder running on your PC. It shows the same
page (filters, saved searches, Refresh button, live updates). **Your PC must be on and the app running.**

## Home Wi-Fi: no pairing, no token (current setup)

1. On the PC, switch on phone access once: `.venv\Scripts\python -m app.phone_setup`, then restart Adoption Finder.
   (This is home-network mode: no token. Running it again is harmless; it never turns the token requirement on.)
2. Open **http://localhost:8000/pair** on the PC: it shows an install link and QR code.
3. On the phone (same Wi-Fi), open that link (`http://<PC address>:8000/apk`), download the file and open it to install.
   Allow "Install unknown apps" for your browser if asked. Play Protect may warn that it is unrecognized; that is expected for your own app.
4. Open the app. It connects to the PC by itself. Nothing to enter.

Who can use it: any device on your home network (home addresses and Tailscale). Requests from public internet addresses still need
the token (`API_TOKEN` in `.env`), so a mistaken router port-forward does not expose the app. If you don't want visitors' phones to reach it, put them on a guest Wi-Fi.

The app is built to open your PC's address at the time of the build (shown when you run the build script). If that address
changes (routers can hand out a new one), change it in the app (menu, top right, **Server settings**) or rebuild with
`phone\build_apk.ps1`. Giving the PC a fixed address in your router ("DHCP reservation") avoids this.

## Updating the app from the phone
In the app: menu (top right) > **Check for app update**. It asks your PC which version it has (`/apk/version`); if it is newer than the
installed one, tap **Update**: the app downloads it, checks its checksum and hands it to Android's installer, which asks you to confirm.
The first time, Android also asks you to allow Adoption Finder to install apps ("Allow from this source"). If the in-app install ever fails,
the app offers to open the download in your browser instead. Android only accepts an update signed with the same key as the installed app,
so a tampered file cannot replace it.

To publish a new build from the PC: `powershell -ExecutionPolicy Bypass -File phoneuild_apk.ps1`. Each build raises the version by one
and writes `phoneersion.json`; phones see it the next time they check. (The very first install of a build that has this feature has to be done by hand,
from `http://<PC address>:8000/apk`.)

## Petfinder from the phone
The Petfinder button in the app's page (top of the screen) opens the same panel as on the PC: pick the pause, press **Check for new dogs** or
**Full scrub**, and watch progress. The crawl itself runs in Chrome on the PC, so the PC must be on.

## Away from home (later): Tailscale + token

Tailscale addresses already work without a token in home-network mode. Only if you ever expose the app beyond your own devices, switch to
token mode with `.venv\Scripts\python -m app.phone_setup --token` (creates a token, sets `REQUIRE_TOKEN=1`), install Tailscale on the PC and phone,
run `phone\allow_tailscale_firewall.ps1` as Administrator, restart the app, and enter the Tailscale address (`http://100.x.y.z:8000`) and token in
the app's Server settings (or open the pairing link from `/pair`). Never expose the app to the internet without the token on.

## Troubleshooting
| Symptom | Fix |
|---|---|
| "Can't reach your PC" | PC off or app not running; phone on a different Wi-Fi (or guest network) or a VPN is on; the PC's address changed (Server settings). |
| "Access denied" | The PC is in token mode: enter the token in Server settings. |
| Page loads but is empty | Wait for the first fetch to finish on the PC, then Reload (menu). |

## Rebuilding the app
`powershell -ExecutionPolicy Bypass -File phone\build_apk.ps1` (optional `-Server http://<address>:8000`). It uses the JDK, Android SDK and
Gradle in `tools\`. Project source is in `android\` (one Java file: `MainActivity.java`). Delete `tools\` to remove the build tools.

## Notes
- No secrets are inside the APK. The token, if you use one, is kept in the app's private storage.
- Traffic is plain HTTP: inside the Tailscale tunnel it is encrypted; on home Wi-Fi it stays on your network.
- `/pair` and the Petfinder import page only open on the PC itself, never from other devices.
