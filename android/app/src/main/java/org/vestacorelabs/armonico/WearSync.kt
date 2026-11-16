package org.vestacorelabs.armonico

import android.content.Context
import android.content.Intent
import android.util.Log
import com.google.android.gms.wearable.MessageEvent
import com.google.android.gms.wearable.PutDataMapRequest
import com.google.android.gms.wearable.Wearable
import com.google.android.gms.wearable.WearableListenerService
import org.json.JSONArray
import org.json.JSONObject

/**
 * The watch app (android/wear) and this phone app talk over the Wearable Data Layer, through the
 * Bluetooth link the phone and the watch already have, so no network has to be set up on the watch.
 * Both apps carry the same package name and the same signing key, which the Data Layer requires.
 *
 *  - The phone keeps one data item, /armonico/state: what the watch shows (the bridge, the keyboard,
 *    what plays, a ringing alarm, the alarms, the shortcuts and the songs). It is written again
 *    whenever something in it changes, and the watch is told at once.
 *  - The watch sends messages to /armonico/cmd, a JSON {"op": ...}, which PhoneWearService turns into
 *    the same actions the phone's own screens take.
 */
object WearSync {
    const val STATE = "/armonico/state"
    const val CMD = "/armonico/cmd"
    private var last = ""

    fun state(ctx: Context): JSONObject {
        val s = AppState.status
        val running = AppState.running
        val n = AlarmClock.nearest(ctx)
        return JSONObject()
            .put("running", running)
            .put("keyboard", running && s.optBoolean("keyboard"))
            .put("device", s.optString("device"))
            .put("song", if (running) s.optString("song") else "")
            .put("alarm", running && s.optBoolean("alarm") && s.optString("song").isNotEmpty())
            .put("paused", running && s.optBoolean("paused"))
            .put("lesson", running && s.optBoolean("game"))
            .put("alarms", JSONArray(AlarmClock.shown(ctx)))
            .put("next_at", n?.second ?: 0L)
            .put("next_song", n?.first?.optString("song") ?: "")
            .put("shortcuts", JSONArray(PhoneActions.shortcutNames(ctx)))
            .put("songs", JSONArray(Settings.songNames(ctx)))
            .put("lang", Settings.appLang(ctx))
    }

    /** Writes the state for the watch when it changed since the last time (or always, with force). */
    fun push(ctx: Context, force: Boolean = false) {
        val body = try { state(ctx).toString() } catch (e: Exception) { return }
        synchronized(this) {
            if (!force && body == last) return
            last = body
        }
        try {
            val req = PutDataMapRequest.create(STATE).apply {
                dataMap.putString("json", body)
                dataMap.putLong("t", System.currentTimeMillis())   // a new item each time, so the watch hears of it
            }.asPutDataRequest().setUrgent()
            Wearable.getDataClient(ctx).putDataItem(req)
        } catch (e: Exception) {
            Log.w("Armonico", "watch: ${e.message}")         // no Play services, or no watch: nothing to do
        }
    }

    /** A command from the watch. The bridge commands only go to a bridge that runs; the rest work with it off. */
    fun handle(ctx: Context, json: String) {
        val c = try { JSONObject(json) } catch (e: Exception) { return }
        val running = AppState.running
        fun service(action: String, song: String? = null) {
            if (running) BridgeService.start(ctx, action, song)
        }
        when (c.optString("op")) {
            "bridge_on" -> if (Settings.ready(ctx)) BridgeService.start(ctx)
            "bridge_off" -> ctx.stopService(Intent(ctx, BridgeService::class.java))
            "stop" -> service(BridgeService.ACTION_STOP_PLAYBACK)
            "snooze" -> {
                val song = AppState.status.optString("song")
                if (running && AppState.status.optBoolean("alarm") && song.isNotEmpty()) {
                    BridgeService.start(ctx, BridgeService.ACTION_STOP_PLAYBACK)
                    AlarmClock.snooze(ctx, song, c.optInt("minutes", 5).coerceIn(1, 60))
                }
            }
            "pause" -> service(BridgeService.ACTION_PAUSE)
            "play" -> service(BridgeService.ACTION_WATCH_PLAY, c.optString("song"))
            "shortcut" -> service(BridgeService.ACTION_WATCH_SHORTCUT, c.optString("sid"))
            "alarm_put" -> c.optJSONObject("alarm")?.let { AlarmClock.put(ctx, it) }
            "alarm_remove" -> AlarmClock.remove(ctx, c.optInt("id"))
        }
        push(ctx, force = true)
    }
}

/** Messages from the watch app. Android starts it when one arrives, also when the app is not running. */
class PhoneWearService : WearableListenerService() {
    override fun onMessageReceived(event: MessageEvent) {
        if (event.path != WearSync.CMD) return
        WearSync.handle(applicationContext, String(event.data, Charsets.UTF_8))
    }
}
