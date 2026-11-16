package org.vestacorelabs.armonico

import android.content.Context
import android.content.Intent
import android.os.Looper
import androidx.test.core.app.ActivityScenario
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.github.takahirom.roborazzi.captureRoboImage
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/** Pictures of every tab, in both themes and both languages, drawn on the computer. */
@RunWith(AndroidJUnit4::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(sdk = [35], qualifiers = "w384dp-h832dp-xxhdpi")
class ScreensTest {

    private val log = JSONArray(listOf(
        "✅ Piano bridge starting (Android) · 09:30:54", "✅ Keyboard connected: Yamaha PSR-E363 · 09:30:54",
        "🔁 Connected to the broker · 09:30:55", "✅ Shortcut recognized: good night · 09:38:12",
        "⏰ Alarm started by Alarm: eine kleine nachtmusik · 09:41:06"))

    private fun shot(name: String, tab: Int, lang: String, theme: String, mode: String = "local", alarm: Boolean = true, bottom: Boolean = false,
                     tap: String? = null, logMode: Int = 0) {
        val ctx = ApplicationProvider.getApplicationContext<Context>()
        Settings.prefs(ctx).edit().clear()
            .putString("ui_lang", lang).putString("theme", theme).putString("mode", mode)
            .putString("host", "192.168.0.20").putString("user", "piano")
            .putString("telegram_token", if (mode == "local") "123:ABC" else "")
            .putString("lang", if (lang == "iw") "he" else "en").apply()
        AppState.markRunning(true)
        AppState.update(JSONObject().apply {
            put("broker", true); put("keyboard", true); put("device", "Yamaha PSR-E363")
            put("song", if (alarm) "eine_kleine_nachtmusik" else ""); put("alarm", alarm); put("paused", false)
            put("source", "Home Assistant"); put("mode", mode); put("live", true)
            put("lessons", true); put("lesson_port", 8099); put("code", "482913")
            put("telegram", JSONObject().put("on", mode == "local").put("connected", true).put("username", "my_piano_bot")
                .put("paired", true).put("owner", 5551234).put("pairing", false))
            put("log", log)
            put("ws", mode == "local"); put("local_broker", mode == "local")
            put("record", JSONObject().put("on", true).put("days", 3).put("mb", 1.2).put("keep", 14))
            put("console_seq", 4)
            put("console", org.json.JSONArray()
                .put(JSONObject().put("k", "in").put("t", "songs elise").put("at", "09:42:10"))
                .put(JSONObject().put("k", "out").put("t", if (lang == "iw") "🎵 נמצא שיר אחד:\nfur_elise" else "🎵 1 song found:\nfur_elise"))
                .put(JSONObject().put("k", "in").put("t", "record").put("at", "09:42:31"))
                .put(JSONObject().put("k", "out").put("t", if (lang == "iw") "הקלטה: פועלת\nקבצים: 3 ימים, \u20661.2 MB\u2069, היום \u20662026-10-31.log\u2069\nנשמר: 14 ימים"
                                                          else "Recorder: on\nFiles: 3 days, 1.2 MB, today 2026-10-31.log\nKept: 14 days")))
        }.toString())
        val intent = Intent(ctx, MainActivity::class.java).putExtra("tab", tab).putExtra("log_mode", logMode)
        ActivityScenario.launch<MainActivity>(intent).use { sc ->
            repeat(5) { shadowOf(Looper.getMainLooper()).idle(); Thread.sleep(100) }
            if (tap != null) sc.onActivity { a ->
                fun find(v: android.view.View): android.widget.TextView? =
                    (v as? android.widget.TextView)?.takeIf { it.text.toString() == tap }
                        ?: (v as? android.view.ViewGroup)?.let { g -> (0 until g.childCount).firstNotNullOfOrNull { find(g.getChildAt(it)) } }
                find(a.window.decorView)!!.performClick()
            }
            repeat(2) { shadowOf(Looper.getMainLooper()).idle() }
            if (bottom) sc.onActivity { a ->
                fun find(v: android.view.View): android.widget.ScrollView? = v as? android.widget.ScrollView
                    ?: (v as? android.view.ViewGroup)?.let { g -> (0 until g.childCount).firstNotNullOfOrNull { find(g.getChildAt(it)) } }
                find(a.window.decorView)?.let { it.scrollTo(0, it.getChildAt(0).height) }
            }
            repeat(2) { shadowOf(Looper.getMainLooper()).idle() }
            sc.onActivity { it.window.decorView.captureRoboImage("build/shots/$name.png") }
        }
    }

    @Test fun home_he_light() = shot("1-home-he-light", 0, "iw", "light")
    @Test fun songs_he_light() = shot("2-songs-he-light", 1, "iw", "light", alarm = false)
    @Test fun settings_local_he_light() = shot("3-settings-local-he-light", 2, "iw", "light")
    @Test fun settings_network_he_light() = shot("4-settings-network-he-light", 2, "iw", "light", mode = "external")
    @Test fun log_he_light() = shot("5-log-he-light", 3, "iw", "light", logMode = 1)
    @Test fun commands_he_light() = shot("12-commands-he-light", 3, "iw", "light")
    @Test fun commands_en_dark() = shot("13-commands-en-dark", 3, "en", "dark")
    @Test fun home_he_dark() = shot("6-home-he-dark", 0, "iw", "dark")
    @Test fun settings_he_dark() = shot("7-settings-he-dark", 2, "iw", "dark")
    @Test fun settings_bottom_he_light() = shot("9-settings-bottom-he-light", 2, "iw", "light", bottom = true)
    @Test fun songs_en_dark() = shot("10-songs-en-dark", 1, "en", "dark", alarm = false)
    @Test fun settings_switch_to_network() = shot("11-settings-tapped-network", 2, "iw", "light", tap = "ברוקר ברשת")
    @Test fun home_en_light() = shot("8-home-en-light", 0, "en", "light", alarm = false)
}
