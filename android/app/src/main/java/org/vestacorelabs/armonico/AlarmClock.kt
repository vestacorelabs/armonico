package org.vestacorelabs.armonico

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build
import org.json.JSONArray
import org.json.JSONObject
import java.util.Calendar

/**
 * The app's own alarm clock: a time, the days of the week and a song, with no Home Assistant needed.
 *
 * The alarms are kept as JSON in their own preferences file. Only the nearest one is handed to
 * Android, with AlarmManager.setAlarmClock: the kind of alarm clock apps use, which wakes the phone at
 * the exact minute, also in Doze, shows in the status bar, and lets the app start its service from the
 * background. When it goes off, AlarmReceiver asks the bridge to ring the song (BridgeService,
 * ACTION_ALARM), exactly as an alarm from Home Assistant rings, and the next one is set.
 *
 * Each alarm: {"id": 1, "h": 7, "m": 0, "days": [false, true, ...] (Sunday first), "song": "wake_up", "on": true}.
 * No day chosen means once: it turns itself off after ringing.
 */
object AlarmClock {
    private const val FILE = "alarm_clock"
    const val ACTION_FIRE = "org.vestacorelabs.armonico.ALARM_FIRE"
    const val EXTRA_ID = "id"

    private fun prefs(ctx: Context) = ctx.getSharedPreferences(FILE, Context.MODE_PRIVATE)

    fun all(ctx: Context): MutableList<JSONObject> = try {
        val a = JSONArray(prefs(ctx).getString("alarms", "[]"))
        MutableList(a.length()) { a.getJSONObject(it) }
    } catch (e: Exception) { mutableListOf() }

    fun saveAll(ctx: Context, list: List<JSONObject>) {
        prefs(ctx).edit().putString("alarms", JSONArray(list).toString()).apply()
        schedule(ctx)
        WearSync.push(ctx)
    }

    fun put(ctx: Context, a: JSONObject) {
        val list = all(ctx)
        if (!a.has("id")) a.put("id", (list.maxOfOrNull { it.optInt("id") } ?: 0) + 1)
        val i = list.indexOfFirst { it.optInt("id") == a.optInt("id") }
        if (i >= 0) list[i] = a else list += a
        saveAll(ctx, list)
    }

    fun remove(ctx: Context, id: Int) = saveAll(ctx, all(ctx).filter { it.optInt("id") != id })

    /** Snooze, from the watch: the same song again in a few minutes, as a one-time alarm marked "snooze",
     *  which goes away once it rang. The lists on the phone and the watch do not show it; "Next" does. */
    fun snooze(ctx: Context, song: String, minutes: Int) {
        val at = java.util.Calendar.getInstance().apply { add(java.util.Calendar.MINUTE, minutes) }
        put(ctx, JSONObject().put("h", at.get(java.util.Calendar.HOUR_OF_DAY)).put("m", at.get(java.util.Calendar.MINUTE))
            .put("song", song).put("on", true).put("snooze", true).put("days", JSONArray(List(7) { false })))
    }

    /** The alarms set by hand, without the snoozes. */
    fun shown(ctx: Context) = all(ctx).filter { !it.optBoolean("snooze") }

    fun days(a: JSONObject): BooleanArray {
        val d = a.optJSONArray("days")
        return BooleanArray(7) { d?.optBoolean(it) ?: false }
    }

    /** The next time this alarm rings, in milliseconds, or null when it is off. */
    fun next(a: JSONObject, now: Long = System.currentTimeMillis()): Long? {
        if (!a.optBoolean("on")) return null
        val d = days(a)
        val once = d.none { it }
        val c = Calendar.getInstance().apply {
            timeInMillis = now
            set(Calendar.HOUR_OF_DAY, a.optInt("h")); set(Calendar.MINUTE, a.optInt("m"))
            set(Calendar.SECOND, 0); set(Calendar.MILLISECOND, 0)
        }
        for (i in 0..7) {
            if (c.timeInMillis > now && (once || d[c.get(Calendar.DAY_OF_WEEK) - 1])) return c.timeInMillis
            c.add(Calendar.DAY_OF_MONTH, 1)
        }
        return null
    }

    /** The nearest alarm and its time. */
    fun nearest(ctx: Context): Pair<JSONObject, Long>? =
        all(ctx).mapNotNull { a -> next(a)?.let { a to it } }.minByOrNull { it.second }

    fun canSchedule(ctx: Context): Boolean =
        Build.VERSION.SDK_INT < 31 || (ctx.getSystemService(Context.ALARM_SERVICE) as AlarmManager).canScheduleExactAlarms()

    fun permissionSettings(ctx: Context) =
        Intent(android.provider.Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM, android.net.Uri.parse("package:" + ctx.packageName))

    private fun fireIntent(ctx: Context, id: Int) = PendingIntent.getBroadcast(
        ctx, 100, Intent(ctx, AlarmReceiver::class.java).setAction(ACTION_FIRE).putExtra(EXTRA_ID, id),
        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
    )

    /** Hands the nearest alarm to Android, or cancels the one handed before when there is none. */
    fun schedule(ctx: Context) {
        val am = ctx.getSystemService(Context.ALARM_SERVICE) as AlarmManager
        val n = nearest(ctx)
        if (n == null) { am.cancel(fireIntent(ctx, 0)); return }
        if (!canSchedule(ctx)) return                     // the permissions card asks for it
        val show = PendingIntent.getActivity(ctx, 101, Intent(ctx, MainActivity::class.java).putExtra("tab", 2),
            PendingIntent.FLAG_IMMUTABLE)
        am.setAlarmClock(AlarmManager.AlarmClockInfo(n.second, show), fireIntent(ctx, n.first.optInt("id")))
    }

    /** An alarm went off: the bridge rings its song, a one-time alarm turns off, and the next is set. */
    fun fire(ctx: Context, id: Int) {
        val list = all(ctx)
        val a = list.firstOrNull { it.optInt("id") == id }
        if (a != null && a.optBoolean("on")) {
            BridgeService.start(ctx, BridgeService.ACTION_ALARM, a.optString("song"))
            if (days(a).none { it }) a.put("on", false)
        }
        saveAll(ctx, list.filter { !(it.optBoolean("snooze") && !it.optBoolean("on")) })
    }
}

/** The alarm clock's own broadcast, from AlarmManager only (not exported). */
class AlarmReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        if (intent.action != AlarmClock.ACTION_FIRE) return
        AlarmClock.fire(ctx, intent.getIntExtra(AlarmClock.EXTRA_ID, -1))
    }
}
