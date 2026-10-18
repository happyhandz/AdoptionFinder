package app.adoptionfinder;

import android.app.Activity;
import android.app.AlertDialog;
import android.app.PendingIntent;
import android.content.Intent;
import android.content.pm.PackageInfo;
import android.content.pm.PackageInstaller;
import android.net.Uri;
import android.os.Build;
import android.provider.Settings;
import android.widget.Toast;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.security.MessageDigest;

/**
 * Checks the PC for a newer build of this app (GET /apk/version), downloads it (GET /apk), checks its
 * SHA-256, and hands it to Android's installer. Android itself refuses an update that is not signed with the
 * same key as the installed app, so a tampered file cannot replace it. If anything goes wrong the
 * download link is opened in the phone's browser instead.
 */
class Updater {
    private final Activity activity;
    private final String server;

    Updater(Activity activity, String server) {
        this.activity = activity;
        this.server = server;
    }

    private long installedCode() throws Exception {
        PackageInfo pi = activity.getPackageManager().getPackageInfo(activity.getPackageName(), 0);
        return Build.VERSION.SDK_INT >= 28 ? pi.getLongVersionCode() : pi.versionCode;
    }

    private String installedName() throws Exception {
        return activity.getPackageManager().getPackageInfo(activity.getPackageName(), 0).versionName;
    }

    private HttpURLConnection open(String path) throws IOException {
        HttpURLConnection c = (HttpURLConnection) new URL(server + path).openConnection();
        c.setConnectTimeout(8000);
        c.setReadTimeout(30000);
        return c;
    }

    private void toast(String msg) {
        activity.runOnUiThread(() -> Toast.makeText(activity, msg, Toast.LENGTH_LONG).show());
    }

    /** Menu action: look for an update and offer it. */
    void check() {
        toast("Checking for an update...");
        new Thread(() -> {
            try {
                HttpURLConnection c = open("/apk/version");
                if (c.getResponseCode() != 200) throw new IOException("The PC answered " + c.getResponseCode());
                ByteArrayOutputStream bo = new ByteArrayOutputStream();
                try (InputStream in = c.getInputStream()) {
                    byte[] buf = new byte[4096];
                    int n;
                    while ((n = in.read(buf)) > 0) bo.write(buf, 0, n);
                }
                JSONObject v = new JSONObject(bo.toString("UTF-8"));
                long latest = v.optLong("versionCode", 0);
                String latestName = v.optString("versionName", "?");
                long size = v.optLong("size", 0);
                String sha = v.optString("sha256", "");
                long current = installedCode();
                String currentName = installedName();
                activity.runOnUiThread(() -> {
                    if (latest <= 0) {
                        info("No update information", "The PC has no app build published yet.");
                    } else if (latest > current) {
                        new AlertDialog.Builder(activity)
                                .setTitle("Update available")
                                .setMessage("Version " + latestName + " is ready on your PC (you have " + currentName + ")."
                                        + (size > 0 ? "\nSize: " + (size / 1024) + " KB." : "") + "\n\nInstall it now?")
                                .setPositiveButton("Update", (d, w) -> install(size, sha))
                                .setNegativeButton("Later", null)
                                .show();
                    } else {
                        info("You're up to date", "Adoption Finder " + currentName + " is the newest version on your PC.");
                    }
                });
            } catch (Exception e) {
                activity.runOnUiThread(() -> new AlertDialog.Builder(activity)
                        .setTitle("Couldn't check for updates")
                        .setMessage("Is the PC on and Adoption Finder running?\n\n" + e.getMessage())
                        .setPositiveButton("OK", null).show());
            }
        }).start();
    }

    private void info(String title, String message) {
        new AlertDialog.Builder(activity).setTitle(title).setMessage(message).setPositiveButton("OK", null).show();
    }

    private void install(long size, String sha) {
        if (Build.VERSION.SDK_INT >= 26 && !activity.getPackageManager().canRequestPackageInstalls()) {
            new AlertDialog.Builder(activity)
                    .setTitle("Allow installing updates")
                    .setMessage("Android needs your OK before this app can install an update. Turn on \"Allow from this source\" "
                            + "for Adoption Finder, then come back and tap Update again.")
                    .setPositiveButton("Open settings", (d, w) -> activity.startActivity(new Intent(
                            Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:" + activity.getPackageName()))))
                    .setNegativeButton("Cancel", null)
                    .show();
            return;
        }
        toast("Downloading the update...");
        new Thread(() -> {
            try {
                PackageInstaller installer = activity.getPackageManager().getPackageInstaller();
                PackageInstaller.SessionParams params = new PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL);
                if (size > 0) params.setSize(size);
                int id = installer.createSession(params);
                try (PackageInstaller.Session session = installer.openSession(id)) {
                    MessageDigest md = MessageDigest.getInstance("SHA-256");
                    HttpURLConnection c = open("/apk");
                    if (c.getResponseCode() != 200) throw new IOException("The PC answered " + c.getResponseCode());
                    try (InputStream in = c.getInputStream(); OutputStream out = session.openWrite("AdoptionFinder.apk", 0, size > 0 ? size : -1)) {
                        byte[] buf = new byte[65536];
                        int n;
                        while ((n = in.read(buf)) > 0) {
                            out.write(buf, 0, n);
                            md.update(buf, 0, n);
                        }
                        session.fsync(out);
                    }
                    StringBuilder hex = new StringBuilder();
                    for (byte b : md.digest()) hex.append(String.format("%02x", b));
                    if (!sha.isEmpty() && !hex.toString().equalsIgnoreCase(sha)) {
                        session.abandon();
                        throw new IOException("The downloaded file did not match its checksum");
                    }
                    Intent result = new Intent(activity, InstallReceiver.class).setAction(InstallReceiver.ACTION);
                    int flags = PendingIntent.FLAG_UPDATE_CURRENT | (Build.VERSION.SDK_INT >= 31 ? PendingIntent.FLAG_MUTABLE : 0);
                    PendingIntent pending = PendingIntent.getBroadcast(activity, id, result, flags);
                    session.commit(pending.getIntentSender());
                }
            } catch (Exception e) {
                activity.runOnUiThread(() -> new AlertDialog.Builder(activity)
                        .setTitle("Couldn't install the update here")
                        .setMessage(e.getMessage() + "\n\nOpen the download in your browser instead?")
                        .setPositiveButton("Open download", (d, w) -> activity.startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(server + "/apk"))))
                        .setNegativeButton("Cancel", null).show());
            }
        }).start();
    }
}
