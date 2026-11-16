package org.vestacorelabs.armonico.wear

import android.app.Activity
import android.media.AudioAttributes
import android.media.Ringtone
import android.media.RingtoneManager
import android.os.Bundle
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager
import android.view.WindowManager
import org.json.JSONObject

/**
 * The alarm ringing on the piano, shown on the watch: it vibrates (and sounds, when chosen) until the
 * alarm stops. Stop ends the alarm on the piano itself; Quiet only stops the watch.
 */
class RingActivity : Activity() {
    companion object {
        private val open = mutableListOf<RingActivity>()
        fun closeAll() = synchronized(open) { open.toList() }.forEach { it.runOnUiThread { it.finish() } }
    }

    private var vibrator: Vibrator? = null
    private var tone: Ringtone? = null
    private lateinit var ui: WatchUi

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        synchronized(open) { open += this }
        ui = WatchUi(this)
        val st = Watch.state(this)
        if (!st.optBoolean("alarm")) { finish(); return }
        val page = ui.page()
        page.addView(ui.logo())
        page.addView(ui.small("⏰ " + st.optString("song").replace('_', ' '), ui.ink))
        page.addView(android.widget.TextView(this).apply {
            text = getString(R.string.stop); textSize = 15f; gravity = android.view.Gravity.CENTER
            setTextColor(android.graphics.Color.WHITE)
            background = android.graphics.drawable.GradientDrawable().apply {
                shape = android.graphics.drawable.GradientDrawable.OVAL; setColor(ui.red) }
            layoutParams = android.widget.LinearLayout.LayoutParams(ui.dp(92), ui.dp(92)).apply { topMargin = ui.dp(6) }
            setOnClickListener { act("stop") }
        })
        page.addView(ui.button(getString(R.string.snooze_n, Watch.snoozeMin(this)), ui.gold) { act("snooze") })
        page.addView(ui.small(getString(R.string.quiet_watch)).apply { setOnClickListener { act("quiet") }; setPadding(0, ui.dp(8), 0, 0) })
        setContentView(ui.scroll(page))
        if (Watch.vibrate(this)) {
            vibrator = (getSystemService(VibratorManager::class.java)).defaultVibrator
            vibrator?.vibrate(VibrationEffect.createWaveform(longArrayOf(0, 600, 400), 0))
        }
        if (Watch.sound(this)) {
            tone = RingtoneManager.getRingtone(this, RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM))?.apply {
                audioAttributes = AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_ALARM).build()
                play()
            }
        }
    }

    /** Stop the alarm on the piano, snooze it, or only quiet the watch. */
    private fun act(what: String) {
        when (what) {
            "stop" -> Watch.send(this, JSONObject().put("op", "stop"))
            "snooze" -> Watch.send(this, JSONObject().put("op", "snooze").put("minutes", Watch.snoozeMin(this)))
        }
        quiet()
        if (what != "quiet") finish()
    }

    // the watch's own buttons while it rings (as the clock app does): the action chosen in the settings
    override fun onKeyDown(keyCode: Int, event: android.view.KeyEvent): Boolean {
        if (event.repeatCount == 0 && keyCode in listOf(android.view.KeyEvent.KEYCODE_STEM_1,
                android.view.KeyEvent.KEYCODE_STEM_2, android.view.KeyEvent.KEYCODE_STEM_3,
                android.view.KeyEvent.KEYCODE_STEM_PRIMARY, android.view.KeyEvent.KEYCODE_BACK)) {
            act(Watch.buttons(this)); return true
        }
        return super.onKeyDown(keyCode, event)
    }

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() = act(Watch.buttons(this))

    private fun quiet() {
        vibrator?.cancel(); vibrator = null
        tone?.stop(); tone = null
    }

    override fun onDestroy() {
        quiet()
        synchronized(open) { open -= this }
        super.onDestroy()
    }
}
