package app.adoptionfinder;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.text.InputType;
import android.view.Menu;
import android.view.MenuItem;
import android.view.View;
import android.webkit.CookieManager;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

import java.util.HashMap;
import java.util.Map;

/**
 * A thin shell around the Adoption Finder web page served by your PC.
 * The server address and private token are entered once (or arrive in a pairing link,
 * adoptionfinder://connect?url=...&token=...) and are kept in the app's private storage.
 * The token is sent as a cookie and an Authorization header; the PC refuses everything without it.
 */
public class MainActivity extends Activity {
    private static final int MENU_RELOAD = 1;
    private static final int MENU_SETTINGS = 2;
    private static final int MENU_UPDATE = 3;

    private WebView web;
    private SharedPreferences prefs;
    private String server = "";
    private String token = "";
    private boolean errorShown = false;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        prefs = getSharedPreferences("af", MODE_PRIVATE);
        server = prefs.getString("server", "");
        token = prefs.getString("token", "");
        if (server.isEmpty()) server = normalize(getString(R.string.default_server));

        web = new WebView(this);
        web.setBackgroundColor(Color.parseColor("#12151c"));
        setContentView(web);

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setLoadWithOverviewMode(true);
        CookieManager.getInstance().setAcceptCookie(true);

        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri u = request.getUrl();
                if (sameServer(u)) return false;
                // "Open on Adopt-a-Pet" etc. go to the phone's own browser
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, u));
                } catch (Exception ignored) { }
                return true;
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                errorShown = false;
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) {
                    problem("Can't reach your PC",
                            "Make sure the PC is on, Adoption Finder is running, and Tailscale is connected on this phone.\n\n"
                                    + error.getDescription());
                }
            }

            @Override
            public void onReceivedHttpError(WebView view, WebResourceRequest request, WebResourceResponse resp) {
                if (request.isForMainFrame() && resp.getStatusCode() == 401) {
                    problem("Access denied", "This PC wants a token. Enter it in Server settings (menu, top right).");
                }
            }
        });

        handleIntent(getIntent());
        load();  // no pairing step: the app opens the PC's address straight away
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        if (handleIntent(intent)) load();
    }

    /** Pairing link: adoptionfinder://connect?url=http://100.x.y.z:8000&token=... */
    private boolean handleIntent(Intent intent) {
        Uri u = intent == null ? null : intent.getData();
        if (u == null || !"adoptionfinder".equals(u.getScheme())) return false;
        String url = u.getQueryParameter("url");
        String tok = u.getQueryParameter("token");
        if (url == null || url.isEmpty()) return false;
        if (tok == null) tok = "";
        save(url, tok);
        Toast.makeText(this, "Paired with your PC", Toast.LENGTH_SHORT).show();
        return true;
    }

    private void save(String url, String tok) {
        server = normalize(url);
        token = tok.trim();
        prefs.edit().putString("server", server).putString("token", token).apply();
    }

    private static String normalize(String url) {
        String u = url.trim();
        if (!u.startsWith("http://") && !u.startsWith("https://")) u = "http://" + u;
        while (u.endsWith("/")) u = u.substring(0, u.length() - 1);
        return u;
    }

    private boolean sameServer(Uri u) {
        Uri s = Uri.parse(server);
        return s.getHost() != null && s.getHost().equalsIgnoreCase(u.getHost()) && s.getPort() == u.getPort();
    }

    private void load() {
        errorShown = false;
        Map<String, String> headers = new HashMap<>();
        if (!token.isEmpty()) {  // only needed when the PC is set to require a token
            CookieManager cm = CookieManager.getInstance();
            cm.setCookie(server, "af_token=" + token + "; Path=/; Max-Age=31536000");
            cm.flush();
            headers.put("Authorization", "Bearer " + token);
        }
        web.loadUrl(server + "/", headers);
    }

    private void problem(String title, String message) {
        if (errorShown || isFinishing()) return;
        errorShown = true;
        new AlertDialog.Builder(this)
                .setTitle(title)
                .setMessage(message)
                .setPositiveButton("Retry", (d, w) -> load())
                .setNeutralButton("Server settings", (d, w) -> showSettings(false))
                .setNegativeButton("Close", null)
                .show();
    }

    private void showSettings(boolean firstRun) {
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        int pad = (int) (20 * getResources().getDisplayMetrics().density);
        box.setPadding(pad, pad / 2, pad, 0);

        TextView hint = new TextView(this);
        hint.setText("Address of Adoption Finder on your PC, e.g. http://10.0.0.176:8000. "
                + "Leave the token empty on your home network; it is only needed if the PC is set to require one.");
        box.addView(hint);

        EditText url = new EditText(this);
        url.setHint("http://100.x.y.z:8000");
        url.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        url.setText(server);
        box.addView(url);

        EditText tok = new EditText(this);
        tok.setHint("Token (optional)");
        tok.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        tok.setText(token);
        box.addView(tok);

        AlertDialog.Builder b = new AlertDialog.Builder(this)
                .setTitle("Server settings")
                .setView(box)
                .setCancelable(true)
                .setPositiveButton("Save & connect", (d, w) -> {
                    String u = url.getText().toString().trim();
                    String t = tok.getText().toString().trim();
                    if (u.isEmpty()) {
                        Toast.makeText(this, "Address is required", Toast.LENGTH_SHORT).show();
                        showSettings(firstRun);
                        return;
                    }
                    save(u, t);
                    load();
                });
        b.setNegativeButton("Cancel", null);
        b.show();
    }

    @Override
    public boolean onCreateOptionsMenu(Menu menu) {
        menu.add(0, MENU_RELOAD, 0, "Reload").setShowAsAction(MenuItem.SHOW_AS_ACTION_NEVER);
        menu.add(0, MENU_SETTINGS, 1, "Server settings").setShowAsAction(MenuItem.SHOW_AS_ACTION_NEVER);
        menu.add(0, MENU_UPDATE, 2, "Check for app update").setShowAsAction(MenuItem.SHOW_AS_ACTION_NEVER);
        return true;
    }

    @Override
    public boolean onOptionsItemSelected(MenuItem item) {
        if (item.getItemId() == MENU_RELOAD) {
            load();
            return true;
        }
        if (item.getItemId() == MENU_SETTINGS) {
            showSettings(false);
            return true;
        }
        if (item.getItemId() == MENU_UPDATE) {
            new Updater(this, server).check();
            return true;
        }
        return super.onOptionsItemSelected(item);
    }

    @Override
    public void onBackPressed() {
        if (web.canGoBack()) web.goBack(); else super.onBackPressed();
    }

    @Override
    protected void onResume() {
        super.onResume();
        web.onResume();
    }

    @Override
    protected void onPause() {
        web.onPause();
        super.onPause();
    }
}
