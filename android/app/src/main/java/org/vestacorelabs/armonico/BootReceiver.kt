package org.vestacorelabs.armonico

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/** The phone started, or the app was updated: the bridge starts again if that was chosen. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        // exported for the system's broadcasts: any other sender is ignored
        if (intent.action != Intent.ACTION_BOOT_COMPLETED && intent.action != Intent.ACTION_MY_PACKAGE_REPLACED) return
        val p = Settings.prefs(ctx)
        if (!p.getBoolean("autostart", true)) return
        if (!Settings.ready(ctx)) return                              // nothing set up yet
        BridgeService.start(ctx)
    }
}
