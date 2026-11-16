package org.vestacorelabs.armonico

import android.app.NotificationManager
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraManager
import android.media.AudioManager
import android.net.Uri
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import android.view.KeyEvent
import org.json.JSONObject
import java.io.File
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * What a piano shortcut does on the phone itself, with no other app needed.
 *
 * Each shortcut (by its name, as in shortcuts.conf) can have one action, kept in its own
 * preferences file as JSON. The bridge publishes the shortcut on MQTT as always, and then asks
 * this object to run the phone's action too (MidiLink.shortcut).
 *
 * Kinds:
 *   app        open an app (package)
 *   link       open an address (data): a web page, a map, tel:, mailto:
 *   activity   an intent to an activity, as App Inventor's Activity Starter: action, package,
 *              class, data, type, extras (one key=value a line)
 *   broadcast  a broadcast intent, for an automation app if one is ever used: action, package, extras
 *   torch      the flashlight, on or off
 *   volume_up, volume_down, mute           the media volume
 *   media_play_pause, media_next, media_previous   the media player that plays now
 *   dnd        Do Not Disturb, on or off
 *
 * Opening a screen while the app is in the background needs "Display over other apps"
 * (SYSTEM_ALERT_WINDOW): Android blocks it otherwise since Android 10. Do Not Disturb needs the
 * notification policy access. The rest needs nothing.
 */
object PhoneActions {
    private const val FILE = "phone_actions"
    val KINDS = listOf(
        "none", "app", "link", "activity", "broadcast", "torch",
        "volume_up", "volume_down", "mute", "media_play_pause", "media_next", "media_previous", "dnd",
    )
    private val SCREEN = setOf("app", "link", "activity")

    private fun prefs(ctx: Context) = ctx.getSharedPreferences(FILE, Context.MODE_PRIVATE)

    fun get(ctx: Context, sid: String): JSONObject? =
        prefs(ctx).getString(sid, null)?.let { try { JSONObject(it) } catch (e: Exception) { null } }

    fun set(ctx: Context, sid: String, action: JSONObject?) {
        val e = prefs(ctx).edit()
        if (action == null || action.optString("kind", "none") == "none") e.remove(sid) else e.putString(sid, action.toString())
        e.apply()
    }

    fun all(ctx: Context): Map<String, JSONObject> =
        prefs(ctx).all.keys.mapNotNull { k -> get(ctx, k)?.let { k to it } }.toMap()

    /** The shortcut names saved on the phone, in the order of shortcuts.conf. */
    fun shortcutNames(ctx: Context): List<String> = try {
        File(ctx.filesDir, "shortcuts.conf").readLines()
            .filter { it.isNotBlank() && !it.startsWith("#") && "|" in it }
            .map { it.substringAfter("|").trim() }
            .filter { it.isNotEmpty() }
            .distinct()
    } catch (e: Exception) { emptyList() }

    /** Forgets the actions of shortcuts that were deleted (from Telegram or the command screen), so a new
     *  shortcut taught later with the same name starts with nothing. Only when shortcuts.conf can be read. */
    fun prune(ctx: Context) {
        if (!File(ctx.filesDir, "shortcuts.conf").canRead()) return
        val names = shortcutNames(ctx).toSet()
        val gone = prefs(ctx).all.keys.filter { it !in names }
        if (gone.isNotEmpty()) prefs(ctx).edit().apply { gone.forEach { remove(it) } }.apply()
    }

    fun needsScreen(kind: String) = kind in SCREEN

    fun canOpenScreens(ctx: Context) = Build.VERSION.SDK_INT < 23 || android.provider.Settings.canDrawOverlays(ctx)

    fun canChangeDnd(ctx: Context) =
        (ctx.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager).isNotificationPolicyAccessGranted

    /** Xiaomi, Redmi and Poco block a screen from the background with a switch of their own, beside Android's. */
    fun xiaomi() = Build.MANUFACTURER.equals("xiaomi", true)

    fun overlaySettings(ctx: Context) =
        Intent(android.provider.Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:" + ctx.packageName))

    fun dndSettings() = Intent(android.provider.Settings.ACTION_NOTIFICATION_POLICY_ACCESS_SETTINGS)

    /** MIUI's own permission page for the app; the app's details page where it does not exist. */
    fun xiaomiSettings(ctx: Context): Intent {
        val miui = Intent("miui.intent.action.APP_PERM_EDITOR")
            .setClassName("com.miui.securitycenter", "com.miui.permcenter.permissions.PermissionsEditorActivity")
            .putExtra("extra_pkgname", ctx.packageName)
        return if (miui.resolveActivity(ctx.packageManager) != null) miui
        else Intent(android.provider.Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:" + ctx.packageName))
    }

    /** Runs the action of a shortcut, if it has one. Returns a line for the log, or "" when there is none.
     *  Called from the bridge's thread; the work is done on the main thread, waited for up to 3 seconds. */
    fun run(ctx: Context, sid: String): String {
        val a = get(ctx, sid) ?: return ""
        var out = ""
        val done = CountDownLatch(1)
        Handler(Looper.getMainLooper()).post {
            out = try { log(perform(ctx, a, sid), sid, a.optString("kind")) } catch (e: Exception) {
                Log.e("Armonico", "phone action", e); "❌ $sid: ${e.javaClass.simpleName}: ${e.message ?: ""}"
            }
            done.countDown()
        }
        done.await(3, TimeUnit.SECONDS)
        return out
    }

    /** A line for the bridge's log, which is in English on every platform. */
    private fun log(code: String, sid: String, kind: String): String = when (code) {
        "" -> ""
        "overlay" -> "⚠️ $sid: needs \"Display over other apps\""
        "empty" -> "⚠️ $sid: the action is not filled in"
        "noapp" -> "❌ $sid: no app opens this"
        "noaction" -> "⚠️ $sid: a broadcast needs an action"
        "notorch" -> "❌ $sid: this phone has no flashlight"
        "torch_on" -> "📱 $sid: flashlight on"
        "torch_off" -> "📱 $sid: flashlight off"
        "dndperm" -> "⚠️ $sid: needs Do Not Disturb access"
        "dnd_on" -> "📱 $sid: Do Not Disturb on"
        "dnd_off" -> "📱 $sid: Do Not Disturb off"
        else -> "📱 $sid: $kind"
    }

    /** The same, in the language of the app's screens, for the Test button. Empty when it simply worked. */
    private fun shown(ctx: Context, code: String, sid: String): String = when (code) {
        "overlay" -> ctx.getString(R.string.pa_overlay, sid)
        "empty" -> ctx.getString(R.string.pa_empty, sid)
        "noapp" -> ctx.getString(R.string.pa_noapp, sid)
        "noaction" -> ctx.getString(R.string.pa_noaction, sid)
        "notorch" -> ctx.getString(R.string.pa_notorch, sid)
        "dndperm" -> ctx.getString(R.string.pa_dndperm, sid)
        else -> ""
    }

    /** Runs an action now, from the app's own screen (the Test button). Called on the main thread. */
    fun runNow(ctx: Context, a: JSONObject, sid: String): String =
        try { shown(ctx, perform(ctx, a, sid), sid) } catch (e: Exception) { "❌ ${e.javaClass.simpleName}: ${e.message ?: ""}" }

    /** Builds the intent of an app, link or activity action, without starting it. */
    fun intentOf(ctx: Context, a: JSONObject): Intent? {
        val kind = a.optString("kind")
        val pkg = a.optString("package").trim()
        val data = a.optString("data").trim()
        val i = when (kind) {
            "app" -> if (pkg.isEmpty()) null else ctx.packageManager.getLaunchIntentForPackage(pkg)
                ?: Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER).setPackage(pkg)
            "link" -> if (data.isEmpty()) null else Intent(Intent.ACTION_VIEW, Uri.parse(data))
            "activity", "broadcast" -> {
                val x = Intent()
                a.optString("action").trim().takeIf { it.isNotEmpty() }?.let { x.action = it }
                val cls = a.optString("class").trim()
                if (pkg.isNotEmpty() && cls.isNotEmpty()) x.setClassName(pkg, if (cls.startsWith(".")) pkg + cls else cls)
                else if (pkg.isNotEmpty()) x.setPackage(pkg)
                val type = a.optString("type").trim()
                when {
                    data.isNotEmpty() && type.isNotEmpty() -> x.setDataAndType(Uri.parse(data), type)
                    data.isNotEmpty() -> x.data = Uri.parse(data)
                    type.isNotEmpty() -> x.type = type
                }
                extras(a.optString("extras")).forEach { (k, v) -> x.putExtra(k, v) }
                x
            }
            else -> null
        } ?: return null
        if (kind != "broadcast") i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        return i
    }

    /** key=value, one a line. A line with no = is skipped. */
    fun extras(text: String): List<Pair<String, String>> = text.lines().mapNotNull { line ->
        val k = line.substringBefore("=", "").trim()
        if (k.isEmpty()) null else k to line.substringAfter("=").trim()
    }

    /** Does the action; returns what happened, as a code that log() and shown() turn into words. */
    private fun perform(ctx: Context, a: JSONObject, sid: String): String {
        val kind = a.optString("kind")
        val audio = ctx.getSystemService(Context.AUDIO_SERVICE) as AudioManager
        fun media(code: Int) {
            val t = SystemClock.uptimeMillis()
            audio.dispatchMediaKeyEvent(KeyEvent(t, t, KeyEvent.ACTION_DOWN, code, 0))
            audio.dispatchMediaKeyEvent(KeyEvent(t, t, KeyEvent.ACTION_UP, code, 0))
        }
        when (kind) {
            "app", "link", "activity" -> {
                if (!canOpenScreens(ctx)) return "overlay"
                val i = intentOf(ctx, a) ?: return "empty"
                try { ctx.startActivity(i) } catch (e: ActivityNotFoundException) { return "noapp" }
            }
            "broadcast" -> {
                val i = intentOf(ctx, a) ?: return "empty"
                if (i.action == null) return "noaction"
                ctx.sendBroadcast(i.putExtra("shortcut", sid))
            }
            "torch" -> {
                val cm = ctx.getSystemService(Context.CAMERA_SERVICE) as CameraManager
                val id = cm.cameraIdList.firstOrNull {
                    cm.getCameraCharacteristics(it).get(CameraCharacteristics.FLASH_INFO_AVAILABLE) == true
                } ?: return "notorch"
                if (!watching) {                       // follows the flashlight turned on or off elsewhere too
                    watching = true
                    cm.registerTorchCallback(object : CameraManager.TorchCallback() {
                        override fun onTorchModeChanged(cameraId: String, enabled: Boolean) { if (cameraId == id) torch = enabled }
                    }, Handler(Looper.getMainLooper()))
                }
                torch = !torch
                cm.setTorchMode(id, torch)
                return if (torch) "torch_on" else "torch_off"
            }
            "volume_up" -> audio.adjustStreamVolume(AudioManager.STREAM_MUSIC, AudioManager.ADJUST_RAISE, AudioManager.FLAG_SHOW_UI)
            "volume_down" -> audio.adjustStreamVolume(AudioManager.STREAM_MUSIC, AudioManager.ADJUST_LOWER, AudioManager.FLAG_SHOW_UI)
            "mute" -> audio.adjustStreamVolume(AudioManager.STREAM_MUSIC, AudioManager.ADJUST_TOGGLE_MUTE, AudioManager.FLAG_SHOW_UI)
            "media_play_pause" -> media(KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE)
            "media_next" -> media(KeyEvent.KEYCODE_MEDIA_NEXT)
            "media_previous" -> media(KeyEvent.KEYCODE_MEDIA_PREVIOUS)
            "dnd" -> {
                if (!canChangeDnd(ctx)) return "dndperm"
                val nm = ctx.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
                val on = nm.currentInterruptionFilter == NotificationManager.INTERRUPTION_FILTER_ALL
                nm.setInterruptionFilter(if (on) NotificationManager.INTERRUPTION_FILTER_PRIORITY else NotificationManager.INTERRUPTION_FILTER_ALL)
                return if (on) "dnd_on" else "dnd_off"
            }
            else -> return ""
        }
        return "done"
    }

    // the flashlight's state, kept by a callback from the first use on
    private var torch = false
    private var watching = false
}
