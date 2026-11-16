package org.vestacorelabs.armonico.wear

import android.Manifest
import android.app.Activity
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Bundle
import android.widget.LinearLayout
import android.widget.Toast
import androidx.wear.remote.interactions.RemoteActivityHelper
import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.Executors

/**
 * The watch app's screens, one activity for each (the extra "screen"), so a swipe goes back one step:
 * home, songs, shortcuts, alarms, alarm (one alarm being set), pick (a song for it) and settings.
 * Everything is drawn from the phone's last state and redrawn when a new one arrives.
 */
class WatchActivity : Activity() {
    private lateinit var ui: WatchUi
    private val screen get() = intent.getStringExtra("screen") ?: "home"
    private val redraw: () -> Unit = {
        runOnUiThread {
            val now = Watch.state(this).apply { remove("t") }.toString()
            if (now != drawn) { drawn = now; draw() }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        ui = WatchUi(this)
        if (screen == "home" && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED)
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
    }

    override fun onResume() {
        super.onResume()
        Watch.listen(redraw)
        if (screen == "home") Watch.refresh(this)
        // opened while the alarm rings (from the app list, or the Quick button set to this app): the ringing screen
        if (screen == "home" && Watch.state(this).optBoolean("alarm"))
            startActivity(Intent(this, RingActivity::class.java))
        draw()
    }

    override fun onPause() {
        Watch.unlisten(redraw)
        super.onPause()
    }

    private fun open(screen: String, extra: (Intent) -> Unit = {}) =
        startActivity(Intent(this, WatchActivity::class.java).putExtra("screen", screen).also(extra))

    private fun cmd(op: String, put: (JSONObject) -> Unit = {}) {
        Watch.send(this, JSONObject().put("op", op).also(put)) { ok ->
            if (!ok) runOnUiThread { Toast.makeText(this, R.string.no_phone, Toast.LENGTH_SHORT).show() }
        }
    }

    // set by a screen that uses the bezel for itself (the alarm's time); null: the bezel scrolls
    private var rotary: ((Int) -> Unit)? = null

    override fun onGenericMotionEvent(e: android.view.MotionEvent): Boolean {
        val r = rotary
        if (r != null && e.action == android.view.MotionEvent.ACTION_SCROLL &&
            e.isFromSource(android.view.InputDevice.SOURCE_ROTARY_ENCODER)) {
            val v = -e.getAxisValue(android.view.MotionEvent.AXIS_SCROLL)
            if (v != 0f) r(if (v > 0) 1 else -1)
            return true
        }
        return super.onGenericMotionEvent(e)
    }

    private fun draw() {
        rotary = null
        val st = Watch.state(this)
        val page = ui.page()
        when (screen) {
            "songs" -> songs(page, st)
            "shortcuts" -> shortcuts(page, st)
            "alarms" -> alarms(page, st)
            "alarm" -> alarm(page, st)
            "pick" -> pick(page, st)
            "settings" -> settings(page)
            else -> home(page, st)
        }
        // one scroll view for the whole visit: a new state or a tap redraws its content and keeps
        // the place, instead of a new view that starts at the top
        val sv = scrollView ?: ui.scroll(page).also { scrollView = it; setContentView(it) }
        val keep = sv.scrollY
        if (sv.getChildAt(0) !== page) { sv.removeAllViews(); sv.addView(page) }
        sv.post { sv.scrollTo(0, keep) }
        sv.isFocusable = rotary == null
        if (rotary == null) sv.requestFocus()
    }

    private var scrollView: android.widget.ScrollView? = null
    private var drawn = ""          // the state last drawn: the same state again is not redrawn

    // ---------------------------------------------------------------- home
    private fun home(p: LinearLayout, st: JSONObject) {
        p.addView(ui.logo())
        if (st.length() == 0) p.addView(ui.small(getString(R.string.waiting_phone)))
        val running = st.optBoolean("running")
        val kb = running && st.optBoolean("keyboard")
        p.addView(ui.small(when {
            !running -> "○ " + getString(R.string.bridge_off)
            kb -> "● " + st.optString("device")
            else -> "○ " + getString(R.string.keyboard_off)
        }, if (kb) ui.green else ui.muted))
        // what happens now, with its controls
        when {
            st.optBoolean("alarm") -> p.addView(ui.button(getString(R.string.stop_alarm), ui.red) { cmd("stop") })
            st.optString("song").isNotEmpty() -> {
                p.addView(ui.title(st.optString("song").replace('_', ' ')))
                p.addView(ui.small(getString(if (st.optBoolean("paused")) R.string.paused_short else R.string.playing_short)))
                p.addView(ui.centerRow(
                    ui.roundIcon(if (st.optBoolean("paused")) R.drawable.ic_t_play else R.drawable.ic_t_pause) { cmd("pause") },
                    ui.roundIcon(R.drawable.ic_t_stop, ui.red) { cmd("stop") },
                ))
            }
            st.optBoolean("lesson") -> p.addView(ui.small(getString(R.string.lesson), ui.ink))
        }
        // the list, scrolled with the bezel
        val next = st.optLong("next_at")
        val nextText = if (next > 0) SimpleDateFormat("EEE HH:mm", Locale.getDefault()).format(Date(next)) +
            " · " + st.optString("next_song").replace('_', ' ') else getString(R.string.no_alarm)
        p.addView(ui.item("⏰", getString(R.string.alarm_clock), nextText) { open("alarms") })
        p.addView(ui.item("♪", getString(R.string.songs)) { open("songs") })
        p.addView(ui.item("⚡", getString(R.string.shortcuts)) { open("shortcuts") })
        p.addView(ui.item("●", getString(if (running) R.string.bridge_turn_off else R.string.bridge_turn_on),
            getString(if (running) R.string.bridge_on_now else R.string.bridge_off),
            if (running) ui.green else ui.muted) { cmd(if (running) "bridge_off" else "bridge_on") })
        p.addView(ui.item("📱", getString(R.string.on_phone)) { openOnPhone() })
        p.addView(ui.item("⚙", getString(R.string.settings)) { open("settings") })
    }

    /** Opens the phone app on the phone (its armonico://open link). */
    private fun openOnPhone() {
        val i = Intent(Intent.ACTION_VIEW).addCategory(Intent.CATEGORY_BROWSABLE).setData(Uri.parse("armonico://open"))
        try {
            RemoteActivityHelper(this, Executors.newSingleThreadExecutor()).startRemoteActivity(i)
            Toast.makeText(this, R.string.opened_on_phone, Toast.LENGTH_SHORT).show()
        } catch (e: Exception) { Toast.makeText(this, R.string.no_phone, Toast.LENGTH_SHORT).show() }
    }

    // ---------------------------------------------------------------- songs and shortcuts
    private fun names(st: JSONObject, key: String): List<String> {
        val a = st.optJSONArray(key) ?: return emptyList()
        return List(a.length()) { a.optString(it) }
    }

    private fun songs(p: LinearLayout, st: JSONObject) {
        p.addView(ui.title(getString(R.string.songs)))
        if (!st.optBoolean("running")) p.addView(ui.small(getString(R.string.needs_bridge)))
        names(st, "songs").forEach { s -> p.addView(ui.button(s.replace('_', ' ')) { cmd("play") { it.put("song", s) }; finish() }) }
    }

    private fun shortcuts(p: LinearLayout, st: JSONObject) {
        p.addView(ui.title(getString(R.string.shortcuts)))
        if (!st.optBoolean("running")) p.addView(ui.small(getString(R.string.needs_bridge)))
        val list = names(st, "shortcuts")
        if (list.isEmpty()) p.addView(ui.small(getString(R.string.no_shortcuts)))
        list.forEach { s -> p.addView(ui.button(s.replace('_', ' ')) { cmd("shortcut") { it.put("sid", s) } }) }
    }

    // ---------------------------------------------------------------- the alarm clock (the phone app's)
    private fun alarmsOf(st: JSONObject): List<JSONObject> {
        val a = st.optJSONArray("alarms") ?: return emptyList()
        return List(a.length()) { a.getJSONObject(it) }.sortedBy { it.optInt("h") * 60 + it.optInt("m") }
    }

    private fun hm(a: JSONObject) = String.format(Locale.ROOT, "%02d:%02d", a.optInt("h"), a.optInt("m"))

    private fun daysText(a: JSONObject): String {
        val d = a.optJSONArray("days")
        val on = (0..6).filter { d?.optBoolean(it) == true }
        val letters = resources.getStringArray(R.array.days_short)
        return when (on.size) { 0 -> getString(R.string.once); 7 -> getString(R.string.every_day); else -> on.joinToString(" ") { letters[it] } }
    }

    private fun alarms(p: LinearLayout, st: JSONObject) {
        p.addView(ui.title(getString(R.string.alarm_clock)))
        alarmsOf(st).forEach { a ->
            val label = hm(a) + "  " + daysText(a) + if (a.optBoolean("on")) "" else "  ·  " + getString(R.string.off)
            p.addView(ui.button(label, ui.chip, if (a.optBoolean("on")) ui.gold else ui.muted) {
                draft(a); open("alarm")
            })
        }
        p.addView(ui.button(getString(R.string.add_alarm), ui.gold) {
            val songs = names(st, "songs")
            if (songs.isEmpty()) { Toast.makeText(this, R.string.no_songs, Toast.LENGTH_SHORT).show(); return@button }
            draft(JSONObject().put("h", 7).put("m", 0).put("on", true).put("song", songs.first())
                .put("days", JSONArray(List(7) { false })))
            open("alarm")
        })
    }

    // the alarm being set lives in a draft until Save
    private fun prefs() = getSharedPreferences("watch", Context.MODE_PRIVATE)
    private fun draft(): JSONObject = try { JSONObject(prefs().getString("draft", "{}") ?: "{}") } catch (e: Exception) { JSONObject() }
    private fun draft(a: JSONObject) = prefs().edit().putString("draft", a.toString()).apply()

    private var editing = "h"                  // the part of the time the bezel changes: h or m

    private fun alarm(p: LinearLayout, st: JSONObject) {
        val a = draft()
        fun move(min: Int) {
            val t = ((a.optInt("h") * 60 + a.optInt("m") + min) % 1440 + 1440) % 1440
            a.put("h", t / 60).put("m", t % 60); draft(a); draw()
        }
        rotary = { steps -> move(if (editing == "h") steps * 60 else steps) }
        p.addView(ui.small(getString(R.string.alarm_clock)))
        val time = android.widget.TextView(this).apply {
            val hh = String.format(Locale.ROOT, "%02d", a.optInt("h")); val mm = String.format(Locale.ROOT, "%02d", a.optInt("m"))
            val sp = android.text.SpannableString("$hh:$mm")
            val (s0, s1) = if (editing == "h") 0 to 2 else 3 to 5
            sp.setSpan(android.text.style.UnderlineSpan(), s0, s1, 0)
            sp.setSpan(android.text.style.ForegroundColorSpan(ui.gold), s0, s1, 0)
            text = sp; textSize = 36f; setTextColor(android.graphics.Color.WHITE); gravity = android.view.Gravity.CENTER
            textDirection = android.view.View.TEXT_DIRECTION_LTR
            setOnClickListener { editing = if (editing == "h") "m" else "h"; draw() }
        }
        p.addView(time)
        p.addView(ui.small(getString(R.string.bezel_hint)))
        val d = a.optJSONArray("days") ?: JSONArray(List(7) { false })
        val letters = resources.getStringArray(R.array.days_short)
        val days = ui.centerRow(*Array(7) { i ->
            android.widget.TextView(this).apply {
                text = letters[i]; textSize = 10.5f; gravity = android.view.Gravity.CENTER
                setTextColor(if (d.optBoolean(i)) android.graphics.Color.parseColor("#1d1606") else ui.muted)
                background = android.graphics.drawable.GradientDrawable().apply {
                    shape = android.graphics.drawable.GradientDrawable.OVAL; setColor(if (d.optBoolean(i)) ui.gold else ui.chip) }
                layoutParams = LinearLayout.LayoutParams(ui.dp(22), ui.dp(22)).apply { marginStart = ui.dp(2); marginEnd = ui.dp(2) }
                setOnClickListener { d.put(i, !d.optBoolean(i)); a.put("days", d); draft(a); draw() }
            }
        }).apply { layoutDirection = android.view.View.LAYOUT_DIRECTION_LOCALE }
        p.addView(days)
        p.addView(ui.small(daysText(a)))
        p.addView(ui.item("♪", a.optString("song").replace('_', ' ')) { open("pick") })
        p.addView(ui.toggle(getString(R.string.alarm_on), a.optBoolean("on", true)) { a.put("on", it); draft(a); draw() })
        p.addView(ui.button(getString(R.string.save), ui.gold) {
            cmd("alarm_put") { it.put("alarm", a) }
            Toast.makeText(this, R.string.saved, Toast.LENGTH_SHORT).show(); finish()
        })
        if (a.has("id")) p.addView(ui.button(getString(R.string.delete), ui.red) {
            cmd("alarm_remove") { it.put("id", a.optInt("id")) }; finish()
        })
    }

    private fun pick(p: LinearLayout, st: JSONObject) {
        p.addView(ui.title(getString(R.string.song)))
        names(st, "songs").forEach { s ->
            p.addView(ui.button(s.replace('_', ' ')) { draft(draft().put("song", s)); finish() })
        }
    }

    // ---------------------------------------------------------------- the watch's own settings
    private fun settings(p: LinearLayout) {
        p.addView(ui.title(getString(R.string.settings)))
        p.addView(ui.toggle(getString(R.string.set_ring), Watch.ring(this)) { Watch.set(this, "ring", it); draw() })
        p.addView(ui.toggle(getString(R.string.set_vibrate), Watch.vibrate(this)) { Watch.set(this, "vibrate", it); draw() })
        p.addView(ui.toggle(getString(R.string.set_sound), Watch.sound(this)) { Watch.set(this, "sound", it); draw() })
        val snz = Watch.snoozeMin(this)
        p.addView(ui.button(getString(R.string.set_snooze, snz)) {
            Watch.setInt(this, "snooze", when (snz) { 5 -> 10; 10 -> 15; else -> 5 }); draw()
        })
        val btn = Watch.buttons(this)
        p.addView(ui.button(getString(R.string.set_buttons, getString(when (btn) {
            "stop" -> R.string.btn_stop; "quiet" -> R.string.btn_quiet; else -> R.string.btn_snooze }))) {
            Watch.setStr(this, "buttons", when (btn) { "snooze" -> "stop"; "stop" -> "quiet"; else -> "snooze" }); draw()
        })
        // the shortcut the tile runs: cycles through the phone's shortcuts, then none
        val scs = names(Watch.state(this), "shortcuts")
        val cur = Watch.tileShortcut(this)
        p.addView(ui.item("⚡", getString(R.string.set_tile_shortcut),
            if (cur.isEmpty()) getString(R.string.none_short) else cur.replace('_', ' ')) {
            val i = scs.indexOf(cur)
            Watch.setStr(this, "tile_sc", if (i + 1 < scs.size) scs[i + 1] else "")
            Watch.updateViews(this); draw()
        })
        p.addView(ui.small(getString(R.string.set_note)))
    }
}
