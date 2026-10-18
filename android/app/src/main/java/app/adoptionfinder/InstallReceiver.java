package app.adoptionfinder;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageInstaller;
import android.widget.Toast;

/** Receives the result of an app-update install session and shows Android's confirmation screen when it asks. */
public class InstallReceiver extends BroadcastReceiver {
    public static final String ACTION = "app.adoptionfinder.INSTALL_RESULT";

    @Override
    @SuppressWarnings("deprecation")
    public void onReceive(Context context, Intent intent) {
        int status = intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE);
        switch (status) {
            case PackageInstaller.STATUS_PENDING_USER_ACTION: {
                // Android wants the person to confirm the install
                Intent confirm = intent.getParcelableExtra(Intent.EXTRA_INTENT);
                if (confirm != null) {
                    confirm.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                    context.startActivity(confirm);
                }
                break;
            }
            case PackageInstaller.STATUS_SUCCESS:
                Toast.makeText(context, "Adoption Finder was updated. Open it again.", Toast.LENGTH_LONG).show();
                break;
            default: {
                String why = intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE);
                Toast.makeText(context, "Update did not install" + (why == null ? "" : ": " + why), Toast.LENGTH_LONG).show();
            }
        }
    }
}
