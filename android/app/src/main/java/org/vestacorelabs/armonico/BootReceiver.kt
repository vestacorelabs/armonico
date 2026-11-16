package org.vestacorelabs.armonico

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/** The phone started, or the app was updated: the bridge starts again if that was chosen. The alarm clock is
 *  set again then too, and when the clock, the time zone or the permission for exact alarms changes. */
class BootReceiver : BroadcastReceiver() {
    private val again = setOf(
        Intent.ACTION_TIME_CHANGED, Intent.ACTION_TIMEZONE_CHANGED,
        "android.app.action.SCHEDULE_EXACT_ALARM_PERMISSION_STATE_CHANGED",
    )

    override fun onReceive(ctx: Context, intent: Intent) {
        // exported for the system's broadcasts: any other sender is ignored
        if (intent.action in again) { AlarmClock.schedule(ctx); return }
        if (intent.action != Intent.ACTION_BOOT_COMPLETED && intent.action != Intent.ACTION_MY_PACKAGE_REPLACED) return
        AlarmClock.schedule(ctx)
        val p = Settings.prefs(ctx)
        if (!p.getBoolean("autostart", true)) return
        if (!Settings.ready(ctx)) return                              // nothing set up yet
        BridgeService.start(ctx)
    }
}
