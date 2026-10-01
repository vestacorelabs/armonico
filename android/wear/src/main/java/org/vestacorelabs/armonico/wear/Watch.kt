package org.vestacorelabs.armonico.wear

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.util.Log
import androidx.core.app.NotificationCompat
import com.google.android.gms.wearable.DataEventBuffer
import com.google.android.gms.wearable.DataMapItem
import com.google.android.gms.wearable.Wearable
import com.google.android.gms.wearable.WearableListenerService
import org.json.JSONObject

/**
 * What the watch knows and says. The phone app writes its state into one data item
 * (/armonico/state, see WearSync.kt there); the watch keeps the last one, shows it, and sends
 * commands back as messages to /armonico/cmd.
 */
object Watch {
    const val STATE = "/armonico/state"
    const val CMD = "/armonico/cmd"
    private const val CHANNEL = "alarm"
    private const val RING_ID = 7

    private fun prefs(ctx: Context) = ctx.getSharedPreferences("watch", Context.MODE_PRIVATE)

    // the watch's own settings
    fun vibrate(ctx: Context) = prefs(ctx).getBoolean("vibrate", true)
    fun sound(ctx: Context) = prefs(ctx).getBoolean("sound", false)
    fun ring(ctx: Context) = prefs(ctx).getBoolean("ring", true)
    fun set(ctx: Context, key: String, on: Boolean) = prefs(ctx).edit().putBoolean(key, on).apply()
    fun snoozeMin(ctx: Context) = prefs(ctx).getInt("snooze", 5)
    /** What the watch's own buttons do while the alarm rings: snooze, stop, or quiet the watch. */
    fun buttons(ctx: Context) = prefs(ctx).getString("buttons", "snooze") ?: "snooze"
    fun setInt(ctx: Context, key: String, v: Int) = prefs(ctx).edit().putInt(key, v).apply()
    fun setStr(ctx: Context, key: String, v: String) = prefs(ctx).edit().putString(key, v).apply()
    /** The shortcut the tile runs, or empty. */
    fun tileShortcut(ctx: Context) = prefs(ctx).getString("tile_sc", "") ?: ""

    /** Asks Wear OS to redraw the tile and the Complication from the new state. */
    fun updateViews(ctx: Context) {
        try { androidx.wear.tiles.TileService.getUpdater(ctx).requestUpdate(ArmonicoTile::class.java) }
        catch (e: Exception) { Log.w("Armonico", "tile update: ${e.message}") }
        try {
            androidx.wear.watchface.complications.datasource.ComplicationDataSourceUpdateRequester
                .create(ctx, android.content.ComponentName(ctx, AlarmComplication::class.java)).requestUpdateAll()
        } catch (e: Exception) { Log.w("Armonico", "complication update: ${e.message}") }
    }

    /** The last state the phone sent, or an empty one before the first. */
    fun state(ctx: Context): JSONObject =
        try { JSONObject(prefs(ctx).getString("state", "{}") ?: "{}") } catch (e: Exception) { JSONObject() }

    private val listeners = mutableListOf<() -> Unit>()
    fun listen(l: () -> Unit) = synchronized(listeners) { listeners += l }
    fun unlisten(l: () -> Unit) = synchronized(listeners) { listeners -= l }

    /** A new state from the phone: kept, shown, and the alarm rung or ended on the watch. */
    fun received(ctx: Context, json: String) {
        val before = state(ctx)
        prefs(ctx).edit().putString("state", json).putLong("at", System.currentTimeMillis()).apply()
        val now = state(ctx)
        val ringing = now.optBoolean("alarm")
        if (ringing && !before.optBoolean("alarm")) startRing(ctx, now.optString("song"))
        if (!ringing && before.optBoolean("alarm")) endRing(ctx)
        synchronized(listeners) { listeners.toList() }.forEach { it() }
        updateViews(ctx)
    }

    /** Reads the phone's data item directly: when the watch app opens, before any change arrives. */
    fun refresh(ctx: Context) {
        try {
            Wearable.getDataClient(ctx).getDataItems(Uri.parse("wear://*$STATE")).addOnSuccessListener { items ->
                val item = items.firstOrNull()
                val json = item?.let { DataMapItem.fromDataItem(it).dataMap.getString("json") }
                items.release()
                if (json != null) received(ctx, json)
            }
        } catch (e: Exception) { Log.w("Armonico", "watch refresh: ${e.message}") }
        send(ctx, JSONObject().put("op", "status"))
    }

    /** A command to the phone app, on every connected phone (there is one). Calls back with whether it went. */
    fun send(ctx: Context, cmd: JSONObject, done: (Boolean) -> Unit = {}) {
        val body = cmd.toString().toByteArray(Charsets.UTF_8)
        Wearable.getNodeClient(ctx).connectedNodes
            .addOnSuccessListener { nodes ->
                if (nodes.isEmpty()) { done(false); return@addOnSuccessListener }
                nodes.forEach { Wearable.getMessageClient(ctx).sendMessage(it.id, CMD, body) }
                done(true)
            }
            .addOnFailureListener { done(false) }
    }

    // ---- ringing on the watch
    private fun startRing(ctx: Context, song: String) {
        if (!ring(ctx)) return
        val nm = ctx.getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(CHANNEL, ctx.getString(R.string.ch_alarm), NotificationManager.IMPORTANCE_HIGH))
        val open = PendingIntent.getActivity(ctx, 0, Intent(ctx, RingActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val n = NotificationCompat.Builder(ctx, CHANNEL)
            .setSmallIcon(R.drawable.ic_notify)
            .setContentTitle(ctx.getString(R.string.ringing))
            .setContentText(song)
            .setCategory(NotificationCompat.CATEGORY_ALARM)
            .setPriority(NotificationCompat.PRIORITY_MAX)
            .setFullScreenIntent(open, true)
            .setContentIntent(open)
            .setOngoing(true)
            .build()
        try { nm.notify(RING_ID, n) } catch (e: SecurityException) { }
        try { ctx.startActivity(Intent(ctx, RingActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) } catch (e: Exception) { }
    }

    fun endRing(ctx: Context) {
        ctx.getSystemService(NotificationManager::class.java).cancel(RING_ID)
        RingActivity.closeAll()
    }
}

/** The phone's state changed. Android starts it when the data item changes, also with the app closed. */
class WatchListener : WearableListenerService() {
    override fun onDataChanged(events: DataEventBuffer) {
        for (e in events) {
            if (e.dataItem.uri.path != Watch.STATE) continue
            DataMapItem.fromDataItem(e.dataItem).dataMap.getString("json")?.let { Watch.received(applicationContext, it) }
        }
    }
}
