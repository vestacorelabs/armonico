package org.vestacorelabs.armonico

import android.Manifest
import android.animation.ValueAnimator
import android.annotation.SuppressLint
import android.app.AlertDialog
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.res.Configuration
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.RectF
import android.media.midi.MidiDeviceInfo
import android.media.midi.MidiManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.provider.OpenableColumns
import android.text.Editable
import android.text.TextWatcher
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.WindowInsetsController
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.util.Locale

/**
 * The app's one screen, in four tabs: home, songs, settings and the log. Drawn in the shared
 * design language (Ui.kt); its structure is its own, made for a phone.
 */
class MainActivity : ComponentActivity() {

    private lateinit var ui: Ui
    private lateinit var p: Palette
    private lateinit var content: FrameLayout
    private val tabViews = mutableListOf<View>()
    private var tab = 0
    private val main = Handler(Looper.getMainLooper())
    private var lastKey = ""                 // what the screen showed last, so it is redrawn only when it changes

    // songs
    private var songs = listOf<JSONObject>()
    private var songFilter = ""

    // settings being edited, saved with the gold button
    private var draft = JSONObject()
    private var measuring: String? = null    // "stop", "low" or "high" while a key is awaited
    private var measureSince = 0.0
    private var pairingUntil = 0L
    private val refresh = mutableListOf<() -> Unit>()   // the parts of the settings tab that follow the status

    private val listener: () -> Unit = { main.post { onStatus() } }

    private val pickSongs = registerForActivityResult(ActivityResultContracts.OpenMultipleDocuments()) { uris ->
        var n = 0
        val dir = Settings.songsDir(this)
        for (uri in uris) {
            val name = displayName(uri) ?: continue
            if (!name.endsWith(".mid", true) && !name.endsWith(".midi", true)) continue
            contentResolver.openInputStream(uri)?.use { input -> File(dir, name).outputStream().use { input.copyTo(it) }; n++ }
        }
        toast(getString(R.string.added_songs, n))
        loadSongs()
    }

    // ---------------------------------------------------------------- language and theme of the app
    override fun attachBaseContext(base: Context) {
        val lang = Settings.prefs(base).getString("ui_lang", "") ?: ""
        if (lang.isEmpty()) return super.attachBaseContext(base)
        Locale.setDefault(Locale(lang))      // so text direction follows the app's language too
        val conf = Configuration(base.resources.configuration)
        conf.setLocale(Locale(lang))
        super.attachBaseContext(base.createConfigurationContext(conf))
    }

    private fun isHebrew() = resources.configuration.locales[0].language.let { it == "iw" || it == "he" }

    private fun darkTheme(): Boolean = when (Settings.prefs(this).getString("theme", "")) {
        "dark" -> true
        "light" -> false
        else -> (resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK) == Configuration.UI_MODE_NIGHT_YES
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        try { if (!Python.isStarted()) Python.start(AndroidPlatform(this)) } catch (e: Throwable) { }
        Settings.seedSongs(this)
        tab = savedInstanceState?.getInt("tab") ?: intent.getIntExtra("tab", 0)
        logMode = savedInstanceState?.getInt("log_mode") ?: intent.getIntExtra("log_mode", 0)
        p = Palette(darkTheme())
        ui = Ui(this, p)
        build()
        loadSongs()
        if (Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
        }
    }

    override fun onSaveInstanceState(out: Bundle) {
        super.onSaveInstanceState(out)
        out.putInt("tab", tab)
        out.putInt("log_mode", logMode)
    }

    override fun onResume() {
        super.onResume()
        AppState.listen(listener)
        showTab(tab)
    }

    override fun onPause() {
        AppState.unlisten(listener)
        super.onPause()
    }

    @Suppress("DEPRECATION")
    private fun build() {
        val root = ui.vbox().apply { setBackgroundColor(p.bg); fitsSystemWindows = true }
        root.addView(header(), ui.matchW())
        root.addView(View(this).apply { setBackgroundColor(p.line) }, LinearLayout.LayoutParams(-1, ui.dp(1)))
        content = FrameLayout(this)
        root.addView(content, LinearLayout.LayoutParams(-1, 0, 1f))
        root.addView(View(this).apply { setBackgroundColor(p.line) }, LinearLayout.LayoutParams(-1, ui.dp(1)))
        root.addView(tabs(), ui.matchW())
        setContentView(root)
        // the system bars in the header's color, after the window has its views
        window.statusBarColor = p.head
        window.navigationBarColor = p.head
        if (Build.VERSION.SDK_INT >= 30) {
            val light = if (p.dark) 0 else WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS or WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS
            window.insetsController?.setSystemBarsAppearance(light,
                WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS or WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS)
        } else if (!p.dark) {
            window.decorView.systemUiVisibility = View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR
        }
    }

    private fun header(): View {
        val h = ui.hbox().apply { setBackgroundColor(p.head); setPadding(ui.dp(16), 0, ui.dp(16), 0); minimumHeight = ui.dp(56) }
        val logo = ImageView(this).apply {
            setImageResource(if (p.dark) R.drawable.logo_icon_dark else R.drawable.logo_icon_light)
            adjustViewBounds = true
        }
        h.addView(logo, LinearLayout.LayoutParams(ui.dp(30), ui.dp(30)))
        h.addView(ui.text("Armonico", 19f, p.title, 800).apply { textAlignment = View.TEXT_ALIGNMENT_VIEW_START },
            LinearLayout.LayoutParams(0, -2, 1f).apply { marginStart = ui.dp(8) })
        fun tool(label: String, click: () -> Unit) = ui.text(label, 13f, p.muted, 700).apply {
            gravity = Gravity.CENTER
            background = ui.shape(p.surface, 8f, p.line)
            setPadding(ui.dp(9), 0, ui.dp(9), 0)
            minWidth = ui.dp(34)
            setOnClickListener { click() }
        }
        h.addView(tool(if (isHebrew()) "EN" else "עב") {
            Settings.prefs(this).edit().putString("ui_lang", if (isHebrew()) "en" else "iw").apply()
            recreate()
        }, LinearLayout.LayoutParams(-2, ui.dp(34)))
        h.addView(tool(if (p.dark) "☀" else "☾") {
            Settings.prefs(this).edit().putString("theme", if (p.dark) "light" else "dark").apply()
            recreate()
        }, LinearLayout.LayoutParams(-2, ui.dp(34)).apply { marginStart = ui.dp(6) })
        return h
    }

    private fun tabs(): View {
        val bar = ui.hbox().apply { setBackgroundColor(p.head); setPadding(ui.dp(8), ui.dp(6), ui.dp(8), ui.dp(10)) }
        val items = listOf(R.drawable.ic_tab_home to R.string.tab_home, R.drawable.ic_tab_songs to R.string.tab_songs,
                           R.drawable.ic_tab_settings to R.string.tab_settings, R.drawable.ic_tab_log to R.string.tab_log)
        tabViews.clear()
        items.forEachIndexed { i, (icon, label) ->
            val t = ui.vbox().apply {
                gravity = Gravity.CENTER
                setPadding(0, ui.dp(5), 0, ui.dp(5))
                addView(ImageView(this@MainActivity).apply { setImageResource(icon) }, LinearLayout.LayoutParams(ui.dp(22), ui.dp(22)))
                addView(ui.text(getString(label), 12f, p.muted, 700).apply { gravity = Gravity.CENTER; setPadding(0, ui.dp(3), 0, 0) })
                setOnClickListener { showTab(i) }
            }
            tabViews += t
            bar.addView(t, LinearLayout.LayoutParams(0, -2, 1f))
        }
        return bar
    }

    /** reload: read the settings from storage again. A redraw of the settings tab itself (the mode
     *  switch) keeps what was typed and chosen and not saved yet. */
    private fun showTab(i: Int, reload: Boolean = true) {
        if (i != 2 && measuring != null) {
            measuring = null                  // a measurement left half way: the next key is a key again
            service(BridgeService.ACTION_CAPTURE, on = false)
        }
        if (i == 1 && reload) loadSongs()
        tab = i
        tabViews.forEachIndexed { k, v ->
            val on = k == i
            v.background = if (on) ui.shape(p.chip, 10f) else null
            val color = if (on) p.title else p.muted
            ((v as LinearLayout).getChildAt(0) as ImageView).setColorFilter(color)
            (v.getChildAt(1) as TextView).setTextColor(color)
        }
        refresh.clear()
        if (i == 2 && (reload || draft.length() == 0)) loadDraft()
        val page = when (i) { 0 -> homePage(); 1 -> songsPage(); 2 -> settingsPage(); else -> logPage() }
        content.removeAllViews()
        content.addView(ScrollView(this).apply { isFillViewport = true; addView(page) })
        lastKey = statusKey()
    }

    private fun body() = ui.vbox(14).apply { setPadding(ui.dp(14), ui.dp(16), ui.dp(14), ui.dp(22)) }

    // ---------------------------------------------------------------- status from the service
    private val st get() = AppState.status

    private fun statusKey(): String {
        val s = st
        val tg = s.optJSONObject("telegram")
        return when (tab) {
            0 -> listOf(AppState.running, s.optBoolean("keyboard"), s.optString("device"), s.optBoolean("broker"),
                        s.optString("song"), s.optBoolean("alarm"), s.optBoolean("paused"), s.optString("source"),
                        tg?.optBoolean("connected"), tg?.optString("username"), ignoringBattery(),
                        s.optBoolean("lessons"), s.optString("code"), s.optString("lessons_error"), s.optString("broker_error"),
                        s.optBoolean("local_broker"), s.optString("mode"), s.optBoolean("game")).joinToString("|")
            1 -> listOf(AppState.running, s.optString("song"), s.optBoolean("keyboard"), s.optBoolean("game")).joinToString("|")
            3 -> if (logMode == 0) "commands|${AppState.running}"
                 else listOf(s.optJSONArray("log")?.toString(), s.optBoolean("live"), AppState.running).joinToString("|")
            else -> "settings"
        }
    }

    private fun onStatus() {
        if (tab == 2) {
            checkMeasure()
            refresh.forEach { it() }
            return
        }
        if (tab == 3 && logMode == 0) refresh.forEach { it() }
        val k = statusKey()
        if (k != lastKey) {
            val scroll = (content.getChildAt(0) as? ScrollView)?.scrollY ?: 0
            showTab(tab, reload = false)
            (content.getChildAt(0) as? ScrollView)?.post { (content.getChildAt(0) as? ScrollView)?.scrollTo(0, scroll) }
        }
    }

    private fun service(action: String? = null, song: String? = null, on: Boolean? = null) {
        val i = Intent(this, BridgeService::class.java)
        if (action != null) i.action = action
        if (song != null) i.putExtra(BridgeService.EXTRA_SONG, song)
        if (on != null) i.putExtra(BridgeService.EXTRA_ON, on)
        androidx.core.content.ContextCompat.startForegroundService(this, i)
    }

    // ---------------------------------------------------------------- home
    private fun homePage(): View {
        val page = ui.vbox()
        page.addView(BannerView(this, p), LinearLayout.LayoutParams(-1, ui.dp(176)))
        val b = body()
        val s = st
        val running = AppState.running

        // now
        val now = ui.card()
        now.addView(ui.kicker(getString(R.string.now)))
        val song = s.optString("song", "")
        if (!running) {
            now.addView(ui.title(getString(R.string.bridge_off_title)).apply { setPadding(0, ui.dp(8), 0, 0) })
            now.addView(ui.sub(getString(R.string.bridge_off_sub)).apply { setPadding(0, ui.dp(4), 0, 0) })
            now.addView(ui.gap(14))
            now.addView(ui.button(getString(R.string.bridge_start), Btn.GOLD, wide = true) { service() })
        } else if (song.isEmpty() && s.optBoolean("game")) {
            // a lesson: the keys belong to it, so nothing else plays and no shortcut runs
            now.addView(ui.title(getString(R.string.now_lesson)).apply { setPadding(0, ui.dp(8), 0, 0) })
            now.addView(ui.sub(getString(R.string.now_lesson_sub)).apply { setPadding(0, ui.dp(4), 0, 0) })
            now.addView(ui.gap(14))
            now.addView(ui.button(getString(R.string.lesson_stop), Btn.GOLD, wide = true) { service(BridgeService.ACTION_LESSON_STOP) })
        } else if (song.isEmpty()) {
            now.addView(ui.title(getString(R.string.now_idle)).apply { setPadding(0, ui.dp(8), 0, 0) })
            now.addView(ui.sub(getString(R.string.now_idle_sub)).apply { setPadding(0, ui.dp(4), 0, 0) })
        } else {
            val row = ui.hbox().apply { setPadding(0, ui.dp(8), 0, 0) }
            row.addView(EqView(this, p), LinearLayout.LayoutParams(ui.dp(25), ui.dp(22)))
            val col = ui.vbox()
            val what = when { s.optBoolean("alarm") -> R.string.now_alarm; s.optBoolean("paused") -> R.string.now_paused; else -> R.string.now_song }
            col.addView(ui.title(getString(what)))
            col.addView(ui.sub(song, 15f).apply { textDirection = View.TEXT_DIRECTION_LTR; textAlignment = View.TEXT_ALIGNMENT_VIEW_START; setPadding(0, ui.dp(4), 0, 0) })
            val src = s.optString("source", "")
            if (src.isNotEmpty()) col.addView(ui.sub(getString(R.string.started_by, src)))
            row.addView(col, LinearLayout.LayoutParams(0, -2, 1f).apply { marginStart = ui.dp(12) })
            now.addView(row)
            now.addView(ui.gap(14))
            val btns = ui.hbox()
            btns.addView(ui.button(getString(R.string.stop_playback), Btn.GOLD) { service(BridgeService.ACTION_STOP_PLAYBACK) },
                LinearLayout.LayoutParams(0, -2, 1f))
            // a song can be paused and resumed; the alarm rings until it is stopped, as on Linux
            if (!s.optBoolean("alarm")) btns.addView(
                ui.button(getString(if (s.optBoolean("paused")) R.string.resume else R.string.pause)) { service(BridgeService.ACTION_PAUSE) },
                LinearLayout.LayoutParams(-2, -2).apply { marginStart = ui.dp(10) })
            now.addView(btns)
        }
        b.addView(now)

        // the lesson screen, served by the engine on the phone
        if (running) {
            val lc = ui.card(padV = 14)
            lc.addView(ui.kicker(getString(R.string.lessons_kicker)))
            if (s.optBoolean("lessons")) {
                val port = s.optInt("lesson_port", 8099)
                val ip = Settings.phoneAddress(this)
                val url = ip?.let { "http://$it:$port" }
                val code = s.optString("code")
                val rows = ui.rows()
                rows.addView(ui.row(getString(R.string.address), url ?: getString(R.string.no_network), url != null,
                    if (url != null) ui.button(getString(R.string.copy), small = true) { copy(url) } else null))
                if (code.isNotEmpty()) rows.addView(ui.row(getString(R.string.screen_code), code, true,
                    ui.button(getString(R.string.copy), small = true) { copy(code) }))
                // the touch status screen, served by the engine at /status, as on Linux
                if (url != null) rows.addView(ui.row(getString(R.string.status_screen), "$url/status", true,
                    ui.button(getString(R.string.copy), small = true) { copy("$url/status") }))
                lc.addView(rows)
                lc.addView(ui.sub(getString(R.string.lessons_sub), 13f).apply { setPadding(0, ui.dp(4), 0, ui.dp(12)) })
                // one gold button on the screen: the lesson screen's only when nothing above needs stopping
                val nowHasGold = song.isNotEmpty() || s.optBoolean("game")
                lc.addView(ui.button(getString(R.string.open_lessons), if (nowHasGold) Btn.GHOST else Btn.GOLD, wide = true) {
                    startActivity(Intent(this, LessonsActivity::class.java).putExtra("port", port).putExtra("code", code))
                })
            } else {
                val err = s.optString("lessons_error")
                if (err.isEmpty()) lc.addView(ui.sub(getString(R.string.lessons_starting)).apply { setPadding(0, ui.dp(6), 0, 0) })
                else {
                    lc.addView(ui.pill(getString(R.string.lessons_failed), Pill.OFF), ui.wrap().apply { topMargin = ui.dp(8) })
                    lc.addView(ui.text(err, 13f, p.muted, 500, ltr = true).apply { setPadding(0, ui.dp(8), 0, 0) })
                    lc.addView(ui.sub(getString(R.string.lessons_failed_sub), 13f).apply { setPadding(0, ui.dp(6), 0, 0) })
                }
            }
            b.addView(lc)
        }

        // connections
        val c = ui.card(padV = 14)
        c.addView(ui.kicker(getString(R.string.connections)))
        val list = ui.rows()
        val kb = s.optBoolean("keyboard")
        list.addView(ui.row(getString(R.string.keyboard), if (kb) s.optString("device") else getString(R.string.keyboard_plug), kb,
            ui.pill(getString(if (kb) R.string.connected_f else R.string.not_connected_f), if (kb) Pill.ON else if (running) Pill.OFF else Pill.NEUTRAL)))
        val pr = Settings.prefs(this)
        if (pr.getString("mode", "local") == "local") {
            list.addView(ui.row(getString(R.string.broker), getString(R.string.on_phone), false, localBrokerPill()))
        } else {
            val br = running && s.optBoolean("broker")
            val err = s.optString("broker_error")
            val small = "${pr.getString("host", "")}:${pr.getInt("port", 1883)}" + if (running && !br && err.isNotEmpty()) "\n$err" else ""
            list.addView(ui.row(getString(R.string.broker), small, true,
                ui.pill(getString(if (br) R.string.connected_m else R.string.not_connected_m), if (br) Pill.ON else if (running) Pill.OFF else Pill.NEUTRAL)))
        }
        val tg = s.optJSONObject("telegram")
        if ((pr.getString("telegram_token", "") ?: "").isNotBlank()) {
            val ok = tg?.optBoolean("connected") == true
            val name = tg?.optString("username")?.takeIf { it.isNotEmpty() }?.let { "@$it" } ?: getString(R.string.telegram)
            list.addView(ui.row(getString(R.string.telegram), name, true,
                ui.pill(getString(if (ok) R.string.working else R.string.not_working), if (ok) Pill.ON else if (running) Pill.OFF else Pill.NEUTRAL)))
        }
        list.addView(ui.row(getString(R.string.bridge), getString(when {
                !running -> R.string.bridge_sub_off
                pr.getBoolean("autostart", true) -> R.string.bridge_sub_auto
                else -> R.string.bridge_sub }), false,
            ui.pill(getString(if (running) R.string.running else R.string.off), if (running) Pill.ON else Pill.NEUTRAL)))
        val bat = ignoringBattery()
        list.addView(ui.row(getString(R.string.background), getString(R.string.background_sub), false,
            if (bat) ui.pill(getString(R.string.allowed), Pill.ON) else ui.button(getString(R.string.allow), small = true) { askBattery() }))
        c.addView(list)
        b.addView(c)

        val foot = ui.hbox().apply { setPadding(ui.dp(4), 0, ui.dp(4), 0) }
        foot.addView(ui.sub(getString(R.string.version, versionName()), 13f), LinearLayout.LayoutParams(0, -2, 1f))
        if (running) foot.addView(ui.button(getString(R.string.bridge_stop), small = true) { stopService(Intent(this, BridgeService::class.java)) })
        b.addView(foot)
        page.addView(b)
        return page
    }

    /** The broker on the phone: off, not running in this mode, failed, or running. */
    private fun localBrokerPill(): View {
        val s = st
        return when {
            !AppState.running -> ui.pill(getString(R.string.off), Pill.NEUTRAL)
            s.optString("mode") != "local" -> ui.pill(getString(R.string.not_active), Pill.NEUTRAL)
            !s.optBoolean("local_broker") -> ui.pill(getString(R.string.failed_start), Pill.OFF)
            else -> ui.pill(getString(R.string.running), Pill.ON)
        }
    }

    // ---------------------------------------------------------------- songs
    private fun loadSongs() {
        val dir = Settings.songsDir(this).absolutePath
        Thread {
            val json = try {
                Python.getInstance().getModule("armonico_android.bridge").callAttr("song_list", dir).toString()
            } catch (e: Throwable) {
                // no Python (a test on the computer): the names, with no length
                JSONArray(Settings.songsDir(this).listFiles()?.filter { it.name.endsWith(".mid", true) }
                    ?.sortedBy { it.name.lowercase() }
                    ?.map { JSONObject().put("name", it.nameWithoutExtension).put("file", it.name).put("secs", 0) } ?: emptyList<JSONObject>()).toString()
            }
            val a = JSONArray(json)
            val list = (0 until a.length()).map { a.getJSONObject(it) }
            main.post { songs = list; if (tab == 1) showTab(1, reload = false) }
        }.start()
    }

    private fun lengthWords(secs: Int): String {
        val m = secs / 60; val s = secs % 60
        return when {
            secs <= 0 -> ""
            m == 0 -> resources.getQuantityString(R.plurals.seconds, s, s)
            s == 0 -> resources.getQuantityString(R.plurals.minutes, m, m)
            else -> getString(R.string.min_sec, resources.getQuantityString(R.plurals.minutes, m, m), resources.getQuantityString(R.plurals.seconds_and, s, s))
        }
    }

    private fun songsPage(): View {
        val b = body()
        val head = ui.vbox()
        head.addView(ui.kicker(getString(R.string.songs_kicker)))
        head.addView(ui.title(resources.getQuantityString(R.plurals.songs, songs.size, songs.size)).apply { setPadding(0, ui.dp(6), 0, 0) })
        head.addView(ui.sub(getString(R.string.songs_sub)).apply { setPadding(0, ui.dp(4), 0, 0) })
        b.addView(head)
        val search = ui.field(songFilter, getString(R.string.search_song))
        b.addView(search)
        val card = ui.card(padV = 4)
        val list = ui.rows()
        val playing = st.optString("song", "")
        fun fill() {
            list.removeAllViews()
            songs.filter { songFilter.isEmpty() || it.optString("name").contains(songFilter, true) }.forEach { o ->
                val name = o.optString("name")
                val isNow = AppState.running && name.equals(playing, true)
                val btn = if (isNow) ui.button(getString(R.string.stop), Btn.RED, small = true) { service(BridgeService.ACTION_STOP_PLAYBACK) }
                          else ui.button(getString(R.string.play), small = true) {
                              when {
                                  !AppState.running -> toast(getString(R.string.need_bridge))
                                  !st.optBoolean("keyboard") -> toast(getString(R.string.keyboard_off_toast))
                                  st.optBoolean("game") -> toast(getString(R.string.lesson_running_toast))
                                  st.optString("song").isNotEmpty() -> toast(getString(R.string.something_playing_toast))
                                  else -> service(BridgeService.ACTION_PLAY, name)
                              }
                          }
                val r = ui.hbox().apply { setPadding(0, ui.dp(10), 0, ui.dp(10)) }
                val col = ui.vbox()
                col.addView(ui.text(name, 15f, p.title, 700, ltr = true))
                val len = lengthWords(o.optInt("secs"))
                if (len.isNotEmpty()) col.addView(ui.sub(len, 13f).apply { setPadding(0, ui.dp(2), 0, 0) })
                r.addView(col, LinearLayout.LayoutParams(0, -2, 1f))
                r.addView(btn, ui.wrap().apply { marginStart = ui.dp(10) })
                r.setOnLongClickListener { confirmDelete(o.optString("file"), name); true }
                list.addView(r)
            }
        }
        fill()
        search.addTextChangedListener(object : TextWatcher {
            override fun afterTextChanged(e: Editable?) { songFilter = e?.toString()?.trim() ?: ""; fill() }
            override fun beforeTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
            override fun onTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
        })
        card.addView(list)
        b.addView(card)
        b.addView(ui.button(getString(R.string.add_songs), Btn.GOLD, wide = true) { pickSongs.launch(arrayOf("*/*")) })
        b.addView(ui.sub(getString(R.string.long_press_delete), 13f).apply { gravity = Gravity.CENTER })
        return b
    }

    private fun confirmDelete(file: String, name: String) {
        AlertDialog.Builder(this, dialogTheme())
            .setMessage(getString(R.string.delete_song, name))
            .setPositiveButton(R.string.delete) { _, _ -> File(Settings.songsDir(this), file).delete(); loadSongs() }
            .setNegativeButton(R.string.cancel, null)
            .show()
    }

    // ---------------------------------------------------------------- settings
    private fun loadDraft() {
        val pr = Settings.prefs(this)
        draft = JSONObject().apply {
            put("mode", pr.getString("mode", "local"))
            put("host", pr.getString("host", ""))
            put("port", pr.getInt("port", 1883))
            put("user", pr.getString("user", ""))
            put("password", pr.getString("password", ""))
            put("telegram_token", pr.getString("telegram_token", ""))
            put("device", pr.getString("device", ""))
            put("stop_key", pr.getInt("stop_key", 36))
            put("key_lowest", pr.getInt("key_lowest", 36))
            put("key_highest", pr.getInt("key_highest", 96))
            put("autostart", pr.getBoolean("autostart", true))
            // the language in settings.json first: the lesson screen may have changed it since
            put("lang", try { JSONObject(File(filesDir, "settings.json").readText()).optString("lang").takeIf { it == "en" || it == "he" } }
                        catch (e: Exception) { null } ?: pr.getString("lang", if (isHebrew()) "he" else "en"))
            put("admin_secret", Settings.secret(this@MainActivity))
        }
    }

    private fun saveDraft() {
        Settings.prefs(this).edit()
            .putString("mode", draft.optString("mode"))
            .putString("host", draft.optString("host").trim())
            .putInt("port", draft.optInt("port", 1883).coerceIn(1, 65535))
            .putString("user", draft.optString("user").trim())
            .putString("password", draft.optString("password"))
            .putString("telegram_token", draft.optString("telegram_token").trim())
            .putString("device", draft.optString("device"))
            .putInt("stop_key", draft.optInt("stop_key"))
            .putInt("key_lowest", draft.optInt("key_lowest"))
            .putInt("key_highest", draft.optInt("key_highest"))
            .putBoolean("autostart", draft.optBoolean("autostart"))
            .putString("lang", draft.optString("lang"))
            .putString("admin_secret", draft.optString("admin_secret").trim().ifEmpty { Settings.secret(this) })
            .apply()
        // the language of the answers lives in settings.json, which the lesson screen writes too and the
        // bridge and the lesson engine both read, as on Linux: one choice for both
        try {
            val f = File(filesDir, "settings.json")
            val j = if (f.exists()) JSONObject(f.readText()) else JSONObject()
            j.put("lang", draft.optString("lang"))
            f.writeText(j.toString(1))
        } catch (e: Exception) { }
    }

    private fun bind(f: android.widget.EditText, key: String, number: Boolean = false) =
        f.addTextChangedListener(object : TextWatcher {
            override fun afterTextChanged(e: Editable?) {
                val v = e?.toString() ?: ""
                if (number) draft.put(key, v.trim().toIntOrNull() ?: 0) else draft.put(key, v)
            }
            override fun beforeTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
            override fun onTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
        })

    private fun noteName(n: Int) = listOf("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")[n % 12] + (n / 12 - 1)

    private fun settingsPage(): View {
        val b = body()
        val local = draft.optString("mode") == "local"
        b.addView(ui.seg(listOf(getString(R.string.mode_local), getString(R.string.mode_network)), if (local) 0 else 1, fill = true) {
            draft.put("mode", if (it == 0) "local" else "external")
            showTab(2, reload = false)
        }, ui.matchW())
        if (draft.optString("mode") != Settings.prefs(this).getString("mode", "local"))
            b.addView(ui.sub(getString(R.string.mode_pending), 13f).apply { gravity = Gravity.CENTER })

        // the broker
        val broker = ui.card()
        if (local) {
            val top = ui.hbox()
            top.addView(ui.kicker(getString(R.string.broker_on_phone)), LinearLayout.LayoutParams(0, -2, 1f))
            val pillBox = FrameLayout(this)
            top.addView(pillBox)
            broker.addView(top)
            broker.addView(ui.sub(getString(R.string.broker_on_phone_sub), 13f).apply { setPadding(0, ui.dp(6), 0, 0) })
            val err = ui.text("", 13f, p.badFg, 500, ltr = true).apply { visibility = View.GONE; setPadding(0, ui.dp(6), 0, 0) }
            broker.addView(err)
            refresh += {
                pillBox.removeAllViews()
                pillBox.addView(localBrokerPill())
                val e = if (AppState.running && st.optString("mode") == "local" && !st.optBoolean("local_broker")) st.optString("broker_error") else ""
                err.text = e; err.visibility = if (e.isEmpty()) View.GONE else View.VISIBLE
            }
        } else {
            val top = ui.hbox()
            top.addView(ui.kicker(getString(R.string.broker_network)), LinearLayout.LayoutParams(0, -2, 1f))
            val pillBox = FrameLayout(this)
            top.addView(pillBox)
            broker.addView(top)
            broker.addView(ui.label(getString(R.string.address)))
            broker.addView(ui.field(draft.optString("host"), "192.168.1.10", ltr = true).also { bind(it, "host") })
            val two = ui.hbox()
            val u = ui.vbox(); u.addView(ui.label(getString(R.string.user))); u.addView(ui.field(draft.optString("user"), "", ltr = true).also { bind(it, "user") })
            val po = ui.vbox(); po.addView(ui.label(getString(R.string.port))); po.addView(ui.field(draft.optInt("port").toString(), "", number = true).also { bind(it, "port", true) })
            two.addView(u, LinearLayout.LayoutParams(0, -2, 1f))
            two.addView(po, LinearLayout.LayoutParams(ui.dp(96), -2).apply { marginStart = ui.dp(10) })
            broker.addView(two)
            broker.addView(ui.label(getString(R.string.password)))
            broker.addView(ui.field(draft.optString("password"), "", password = true).also { bind(it, "password") })
            val err = ui.text("", 13f, p.badFg, 500, ltr = true).apply { visibility = View.GONE; setPadding(0, ui.dp(8), 0, 0) }
            broker.addView(err)
            broker.addView(ui.gap(12))
            broker.addView(ui.button(getString(R.string.save_connect), wide = true) { saveAndApply(force = true) })
            refresh += {
                pillBox.removeAllViews()
                val inThisMode = AppState.running && st.optString("mode") == "external"
                val br = inThisMode && st.optBoolean("broker")
                pillBox.addView(when {
                    !AppState.running -> ui.pill(getString(R.string.off), Pill.NEUTRAL)
                    !inThisMode -> ui.pill(getString(R.string.not_active), Pill.NEUTRAL)
                    br -> ui.pill(getString(R.string.connected_m), Pill.ON)
                    else -> ui.pill(getString(R.string.not_connected_m), Pill.OFF)
                })
                val e = if (inThisMode && !br) st.optString("broker_error") else ""
                err.text = e; err.visibility = if (e.isEmpty()) View.GONE else View.VISIBLE
            }
        }
        b.addView(broker)

        // Telegram
        val tgc = ui.card()
        val tgTop = ui.hbox()
        tgTop.addView(ui.kicker(getString(R.string.telegram)), LinearLayout.LayoutParams(0, -2, 1f))
        val tgPill = FrameLayout(this)
        tgTop.addView(tgPill)
        tgc.addView(tgTop)
        tgc.addView(ui.label(getString(R.string.telegram_token)))
        // no hint: Android lays a password field out left to right, a Hebrew hint included
        tgc.addView(ui.field(draft.optString("telegram_token"), "", password = true).also { bind(it, "telegram_token") })
        val tgRows = ui.vbox()
        tgc.addView(tgRows)
        tgc.addView(ui.sub((if (local) "" else getString(R.string.telegram_token_empty) + ". ") + getString(R.string.telegram_one_reader), 13f)
            .apply { setPadding(0, ui.dp(6), 0, 0) })
        refresh += {
            val tg = st.optJSONObject("telegram")
            val typed = draft.optString("telegram_token").trim()
            val hasToken = typed.isNotEmpty()
            val saved = typed == (Settings.prefs(this).getString("telegram_token", "") ?: "").trim()
            val live = hasToken && saved && AppState.running && tg?.optBoolean("on") == true
            tgPill.removeAllViews()
            tgPill.addView(when {
                !hasToken -> ui.pill(getString(R.string.off), Pill.NEUTRAL)
                !saved -> ui.pill(getString(R.string.not_saved), Pill.NEUTRAL)
                !AppState.running -> ui.pill(getString(R.string.off), Pill.NEUTRAL)
                tg?.optBoolean("connected") == true -> ui.pill(getString(R.string.working), Pill.ON)
                else -> ui.pill(getString(R.string.not_working), Pill.OFF)
            })
            tgRows.removeAllViews()
            if (live && tg != null) {
                val user = tg.optString("username")
                val err = tg.optString("error")
                val rs = ui.rows()
                rs.addView(ui.row(getString(R.string.bot), if (user.isNotEmpty()) "@$user" else (err.ifEmpty { "-" }), user.isNotEmpty()))
                val left = ((pairingUntil - System.currentTimeMillis()) / 1000).toInt()
                val owner = tg.opt("owner")
                val chatText = when {
                    left > 0 && tg.optBoolean("pairing") -> getString(R.string.waiting_start, "%d:%02d".format(left / 60, left % 60))
                    owner != null && owner != JSONObject.NULL -> getString(R.string.chat_connected, owner.toString())
                    else -> getString(R.string.no_chat)
                }
                val btn = if (left > 0 && tg.optBoolean("pairing")) ui.button(getString(R.string.cancel), small = true) { pairingUntil = 0; refresh.forEach { it() } }
                          else ui.button(getString(if (owner != null && owner != JSONObject.NULL) R.string.change_chat else R.string.connect_chat), small = true) {
                              pairingUntil = System.currentTimeMillis() + 120_000
                              service(BridgeService.ACTION_PAIR)
                              tickPairing()
                          }
                rs.addView(ui.row(getString(R.string.bot_chat), chatText, false, btn))
                tgRows.addView(rs, ui.matchW().apply { topMargin = ui.dp(6) })
            } else if (hasToken && (!saved || !AppState.running)) {
                tgRows.addView(ui.sub(getString(if (!saved) R.string.save_to_start_bot else R.string.bot_needs_bridge), 13f)
                    .apply { setPadding(0, ui.dp(8), 0, 0) })
            }
        }
        b.addView(tgc)

        // keyboard
        val kc = ui.card(padV = 14)
        kc.addView(ui.kicker(getString(R.string.keyboard)))
        val devSmall = { draft.optString("device").ifEmpty { getString(R.string.device_first) } }
        val devRow = ui.row(getString(R.string.device), devSmall(), false, ui.button(getString(R.string.change), small = true) { pickDevice() })
        val stopRow = ui.row(getString(R.string.stop_key), "", false, ui.button(getString(R.string.press_on_piano), small = true) { measure("stop") })
        val rangeRow = ui.row(getString(R.string.range), "", false, ui.button(getString(R.string.measure), small = true) { measure("low") })
        kc.addView(ui.rows(devRow, stopRow, rangeRow))
        val smallOf = { r: LinearLayout -> (r.getChildAt(0) as LinearLayout).getChildAt(1) as TextView }
        val btnOf = { r: LinearLayout -> r.getChildAt(1) as TextView }
        refresh += {
            smallOf(devRow).text = devSmall()
            val sk = draft.optInt("stop_key"); val lo = draft.optInt("key_lowest"); val hi = draft.optInt("key_highest")
            smallOf(stopRow).text = getString(R.string.note_of, noteName(sk), sk)
            smallOf(rangeRow).text = getString(R.string.range_of, noteName(lo), noteName(hi), hi - lo + 1)
            btnOf(stopRow).text = getString(if (measuring == "stop") R.string.waiting_key else R.string.press_on_piano)
            btnOf(rangeRow).text = getString(when (measuring) { "low" -> R.string.lowest_key; "high" -> R.string.highest_key; else -> R.string.measure })
        }
        b.addView(kc)

        // general
        val gc = ui.card(padV = 14)
        gc.addView(ui.kicker(getString(R.string.general)))
        gc.addView(ui.rows(
            ui.row(getString(R.string.autostart), null, false, ui.switch(draft.optBoolean("autostart")) { draft.put("autostart", it) }),
            ui.row(getString(R.string.reply_lang), null, false,
                ui.seg(listOf("English", "עברית"), if (draft.optString("lang") == "he") 1 else 0) { draft.put("lang", if (it == 1) "he" else "en") }),
        ))
        b.addView(gc)

        // tools: what the armonico command does on Linux, run here
        val tc = ui.card(padV = 14)
        tc.addView(ui.kicker(getString(R.string.tools)))
        val recSmall = ui.text("", 13f, p.muted, 500)
        val recSwitch = ui.switch(st.optJSONObject("record")?.optBoolean("on") == true) { on ->
            if (!AppState.running) { toast(getString(R.string.need_bridge)); refresh.forEach { it() } }
            else service(BridgeService.ACTION_RECORD, on = on)
        }
        val recRow = ui.row(getString(R.string.record_title), null, false, recSwitch)
        (recRow.getChildAt(0) as LinearLayout).addView(recSmall.apply { setPadding(0, ui.dp(3), 0, 0) })
        refresh += {
            val r = st.optJSONObject("record")
            val on = r?.optBoolean("on") == true
            if (recSwitch.on != on) { recSwitch.on = on; recSwitch.invalidate() }
            recSmall.text = if (on) getString(R.string.record_on_sub, r!!.optInt("days"), r.optDouble("mb").toString())
                            else getString(R.string.record_off_sub, r?.optInt("keep", 14) ?: 14)
        }
        fun tool(label: Int, line: String) = ui.row(getString(label), line, true,
            ui.button(getString(R.string.run), small = true) { runCommand(line) })
        tc.addView(ui.rows(recRow,
            tool(R.string.tool_check, "check"),
            tool(R.string.tool_unstick, "unstick"),
            tool(R.string.tool_reset, "reset"),
            tool(R.string.tool_update, "update")))
        tc.addView(ui.sub(getString(R.string.tools_sub), 13f).apply { setPadding(0, ui.dp(6), 0, 0) })
        b.addView(tc)

        // advanced, folded
        val adv = ui.card(padV = 14)
        val advHead = ui.hbox()
        advHead.addView(ui.kicker(getString(R.string.advanced)), LinearLayout.LayoutParams(0, -2, 1f))
        val chev = ui.text("›", 18f, p.muted, 800)
        advHead.addView(chev)
        adv.addView(advHead)
        val advBody = ui.vbox().apply { visibility = View.GONE }
        advBody.addView(ui.label(getString(R.string.admin_secret)))
        val secRow = ui.hbox()
        secRow.addView(ui.field(draft.optString("admin_secret"), "", ltr = true).also { bind(it, "admin_secret") }, LinearLayout.LayoutParams(0, -2, 1f))
        secRow.addView(ui.button(getString(R.string.copy), small = true) { copy(draft.optString("admin_secret")) }, ui.wrap().apply { marginStart = ui.dp(8) })
        advBody.addView(secRow)
        advBody.addView(ui.sub(getString(if (local) R.string.admin_secret_local else R.string.admin_secret_network), 13f).apply { setPadding(0, ui.dp(6), 0, 0) })
        adv.addView(advBody)
        chev.rotation = 90f
        advHead.setOnClickListener {
            val open = advBody.visibility != View.VISIBLE
            advBody.visibility = if (open) View.VISIBLE else View.GONE
            chev.rotation = if (open) -90f else 90f
        }
        b.addView(adv)

        b.addView(ui.button(getString(R.string.save), Btn.GOLD, wide = true) { saveAndApply(force = false) })
        refresh.forEach { it() }
        return b
    }

    /** Saves the settings. A running bridge starts again with them; force also starts a bridge that was off
     *  ("Save and connect"). An address is needed for a broker on the network. */
    private fun saveAndApply(force: Boolean) {
        if (draft.optString("mode") == "external" && draft.optString("host").isBlank()) {
            toast(getString(R.string.need_host)); return
        }
        saveDraft()
        when {
            AppState.running -> service(BridgeService.ACTION_RESTART)
            force -> service()
        }
        toast(getString(if (AppState.running || force) R.string.saved else R.string.saved_off))
        showTab(2)
    }

    private fun tickPairing() {
        refresh.forEach { it() }
        if (System.currentTimeMillis() < pairingUntil && tab == 2) main.postDelayed({ tickPairing() }, 1000)
    }

    @Suppress("DEPRECATION")
    private fun pickDevice() {
        val mm = getSystemService(Context.MIDI_SERVICE) as MidiManager?
        val names = mm?.devices?.filter { it.type == MidiDeviceInfo.TYPE_USB }?.map {
            it.properties.getString(MidiDeviceInfo.PROPERTY_NAME) ?: it.properties.getString(MidiDeviceInfo.PROPERTY_PRODUCT) ?: "USB MIDI"
        } ?: emptyList()
        val values = (listOf("") + names + listOf(draft.optString("device"))).distinct()
        val labels = values.map { it.ifEmpty { getString(R.string.device_first) } }.toTypedArray()
        AlertDialog.Builder(this, dialogTheme())
            .setTitle(R.string.device)
            .setSingleChoiceItems(labels, values.indexOf(draft.optString("device")).coerceAtLeast(0)) { d, i ->
                draft.put("device", values[i]); d.dismiss(); refresh.forEach { it() }
            }
            .show()
    }

    private fun measure(what: String) {
        if (!AppState.running || !st.optBoolean("keyboard")) { toast(getString(R.string.measure_needs)); return }
        measuring = if (measuring == what) null else what
        measureSince = System.currentTimeMillis() / 1000.0
        service(BridgeService.ACTION_CAPTURE, on = measuring != null)
        refresh.forEach { it() }
    }

    private fun checkMeasure() {
        val m = measuring ?: return
        val at = st.optDouble("captured_at", 0.0)
        if (at <= measureSince || st.isNull("captured")) return
        val n = st.optInt("captured")
        when (m) {
            "stop" -> { draft.put("stop_key", n); measuring = null }
            "low" -> { draft.put("key_lowest", n); measuring = "high"; measureSince = at; service(BridgeService.ACTION_CAPTURE, on = true) }
            "high" -> {
                val lo = draft.optInt("key_lowest")
                draft.put("key_lowest", minOf(lo, n)); draft.put("key_highest", maxOf(lo, n)); measuring = null
            }
        }
    }

    // ---------------------------------------------------------------- commands and log
    private var logMode = 0               // 0 the command screen, 1 the log
    private var commandDraft = ""

    /** A line for the armonico command. restart and logs belong to the app; the rest go to the bridge. */
    private fun runCommand(line: String) {
        val l = line.trim()
        if (l.isEmpty()) return
        val word = l.substringBefore(' ').lowercase()
        when {
            word == "logs" -> { logMode = 1; showTab(3); return }
            word == "restart" -> {
                if (AppState.running) { service(BridgeService.ACTION_RESTART); toast(getString(R.string.restarting)) }
                else service()
                return
            }
            !AppState.running -> { toast(getString(R.string.need_bridge)); return }
        }
        val i = Intent(this, BridgeService::class.java).setAction(BridgeService.ACTION_COMMAND)
            .putExtra(BridgeService.EXTRA_LINE, l)
        androidx.core.content.ContextCompat.startForegroundService(this, i)
        if (tab != 3 || logMode != 0) { logMode = 0; showTab(3) }
    }

    private fun logPage(): View {
        val b = body()
        b.addView(ui.seg(listOf(getString(R.string.commands), getString(R.string.log_view)), logMode, fill = true) {
            logMode = it; showTab(3, reload = false)
        }, ui.matchW())
        if (logMode == 0) return commandsPage(b)
        val head = ui.hbox()
        val t = ui.vbox()
        t.addView(ui.kicker(getString(R.string.log)))
        t.addView(ui.title(getString(R.string.log_title)).apply { setPadding(0, ui.dp(6), 0, 0) })
        head.addView(t, LinearLayout.LayoutParams(0, -2, 1f))
        val live = st.optBoolean("live")
        head.addView(ui.pill(getString(if (live) R.string.live else R.string.here_only), if (live) Pill.ON else Pill.NEUTRAL))
        b.addView(head)
        val lines = st.optJSONArray("log")
        val text = android.text.SpannableStringBuilder()
        if (lines != null) for (i in lines.length() - 1 downTo 0) {
            val l = lines.optString(i)
            val cut = l.lastIndexOf(" · ")
            val (msg, time) = if (cut > 0) l.substring(0, cut) to l.substring(cut + 3) else l to ""
            val s = text.length
            text.append(time)
            text.setSpan(android.text.style.ForegroundColorSpan(p.muted), s, text.length, 0)
            text.append("  ").append(msg).append("\n")
        }
        val card = ui.card()
        card.addView(ui.text(if (text.isEmpty()) getString(R.string.log_empty) else text.trimEnd('\n'), 12f, p.ink, 500, ltr = true).apply {
            typeface = android.graphics.Typeface.MONOSPACE
            textAlignment = View.TEXT_ALIGNMENT_TEXT_START
            gravity = Gravity.LEFT
            setLineSpacing(0f, 1.45f)
            setTextIsSelectable(true)
        })
        b.addView(card)
        val row = ui.hbox()
        row.addView(ui.button(getString(R.string.copy), small = true) { copy(text.toString()) })
        row.addView(ui.button(getString(if (live) R.string.live_stop else R.string.live_start), small = true) {
            if (!AppState.running) toast(getString(R.string.need_bridge)) else service(BridgeService.ACTION_LIVE_LOG, on = !live)
        }, ui.wrap().apply { marginStart = ui.dp(10) })
        b.addView(row)
        return b
    }

    private fun commandsPage(b: LinearLayout): View {
        val t = ui.vbox()
        t.addView(ui.kicker(getString(R.string.commands)))
        t.addView(ui.title(getString(R.string.commands_title)).apply { setPadding(0, ui.dp(6), 0, 0) })
        t.addView(ui.sub(getString(R.string.commands_sub), 13f).apply { setPadding(0, ui.dp(4), 0, 0) })
        b.addView(t)

        // the line to type, as at a terminal
        val input = ui.field(commandDraft, "help", ltr = true).apply {
            imeOptions = android.view.inputmethod.EditorInfo.IME_ACTION_SEND
            addTextChangedListener(object : android.text.TextWatcher {
                override fun afterTextChanged(e: android.text.Editable?) { commandDraft = e?.toString() ?: "" }
                override fun beforeTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
                override fun onTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
            })
        }
        val send = {
            val line = input.text.toString()
            if (line.isNotBlank()) { input.setText(""); commandDraft = ""; runCommand(line) }
        }
        input.setOnEditorActionListener { _, _, _ -> send(); true }
        val row = ui.hbox()
        row.addView(input, LinearLayout.LayoutParams(0, -2, 1f))
        row.addView(ui.button(getString(R.string.run), Btn.GOLD) { send() }, ui.wrap().apply { marginStart = ui.dp(10) })
        b.addView(row)

        // the common ones, one tap each
        val quick = android.widget.HorizontalScrollView(this).apply { isHorizontalScrollBarEnabled = false }
        val chips = ui.hbox()
        listOf("help", "status", "check", "songs", "shortcut list", "lesson list", "record", "update").forEachIndexed { i, c ->
            chips.addView(ui.button(c, small = true) { runCommand(c) }, ui.wrap().apply { if (i > 0) marginStart = ui.dp(8) })
        }
        quick.addView(chips)
        b.addView(quick)

        // what was typed and what came back, newest at the top
        val card = ui.card()
        val out = ui.text("", 14f, p.ink, 500).apply {
            textDirection = View.TEXT_DIRECTION_FIRST_STRONG
            textAlignment = View.TEXT_ALIGNMENT_GRAVITY
            gravity = Gravity.START
            setLineSpacing(0f, 1.35f)
            setTextIsSelectable(true)
            linksClickable = true
        }
        card.addView(out)
        b.addView(card)
        var plain = ""
        val copyRow = ui.hbox()
        copyRow.addView(ui.button(getString(R.string.copy), small = true) { copy(plain) })
        b.addView(copyRow)
        var shown = -1
        val draw: () -> Unit = draw@{
            val seq = st.optInt("console_seq", 0)
            if (seq == shown) return@draw
            shown = seq
            val items = st.optJSONArray("console")
            val text = android.text.SpannableStringBuilder()
            if (items != null) {
                // one command and its answers stay together: groups from the newest down
                val groups = mutableListOf<MutableList<org.json.JSONObject>>()
                for (i in 0 until items.length()) {
                    val o = items.optJSONObject(i) ?: continue
                    if (o.optString("k") == "in" || groups.isEmpty()) groups += mutableListOf(o) else groups.last() += o
                }
                for (g in groups.asReversed()) {
                    for (o in g) {
                        val start = text.length
                        if (o.optString("k") == "in") {
                            text.append("\u200E› ").append(o.optString("t"))
                            text.setSpan(android.text.style.ForegroundColorSpan(p.link), start, text.length, 0)
                            text.setSpan(android.text.style.StyleSpan(android.graphics.Typeface.BOLD), start, text.length, 0)
                            text.setSpan(android.text.style.TypefaceSpan("monospace"), start, text.length, 0)
                            text.append("  ")
                            val ts = text.length
                            text.append(o.optString("at"))
                            text.setSpan(android.text.style.ForegroundColorSpan(p.muted), ts, text.length, 0)
                        } else text.append(o.optString("t"))
                        text.append("\n")
                    }
                    text.append("\n")
                }
            }
            plain = text.toString().trim()
            if (text.isEmpty()) {
                out.text = getString(R.string.commands_empty)
            } else {
                android.text.util.Linkify.addLinks(text, android.text.util.Linkify.WEB_URLS)
                out.text = text.trimEnd('\n')
                out.movementMethod = android.text.method.LinkMovementMethod.getInstance()
            }
        }
        refresh += { draw() }
        draw()
        return b
    }

    // ---------------------------------------------------------------- small things
    private fun ignoringBattery() =
        (getSystemService(Context.POWER_SERVICE) as PowerManager).isIgnoringBatteryOptimizations(packageName)

    @SuppressLint("BatteryLife")
    private fun askBattery() {
        if (ignoringBattery()) return
        startActivity(Intent(android.provider.Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:$packageName")))
    }

    private fun copy(s: String) {
        (getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager).setPrimaryClip(ClipData.newPlainText("Armonico", s))
        toast(getString(R.string.copied))
    }

    private fun dialogTheme() = if (p.dark) android.R.style.Theme_DeviceDefault_Dialog_Alert else android.R.style.Theme_DeviceDefault_Light_Dialog_Alert

    private fun toast(s: String) = Toast.makeText(this, s, Toast.LENGTH_SHORT).show()

    private fun versionName(): String = try { packageManager.getPackageInfo(packageName, 0).versionName ?: "" } catch (e: Exception) { "" }

    private fun displayName(uri: Uri): String? =
        contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { c ->
            if (c.moveToFirst()) c.getString(0) else null
        }?.replace("/", "_")
}

/** The four gold bars beside a song that is playing. */
class EqView(ctx: Context, private val p: Palette) : View(ctx) {
    private val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = p.gold }
    private val rect = RectF()
    private var t = 0f
    private val anim = ValueAnimator.ofFloat(0f, 1f).apply {
        duration = 1000; repeatCount = ValueAnimator.INFINITE
        addUpdateListener { t = it.animatedValue as Float; invalidate() }
    }

    override fun onAttachedToWindow() { super.onAttachedToWindow(); anim.start() }
    override fun onDetachedFromWindow() { anim.cancel(); super.onDetachedFromWindow() }

    override fun onDraw(c: Canvas) {
        val w = width.toFloat(); val h = height.toFloat()
        val bw = w / 7
        val delays = floatArrayOf(0f, 0.2f, 0.4f, 0.1f)
        for (i in 0 until 4) {
            val ph = ((t - delays[i]) % 1f + 1f) % 1f
            val k = 0.27f + 0.73f * (0.5f - 0.5f * Math.cos(ph * 2 * Math.PI).toFloat())
            val x = i * bw * 1.85f
            rect.set(x, h - h * k, x + bw, h)
            c.drawRoundRect(rect, bw / 2, bw / 2, paint)
        }
    }
}
