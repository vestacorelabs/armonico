package org.vestacorelabs.armonico

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.Handler
import android.os.HandlerThread
import android.os.IBinder
import android.os.PowerManager
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import com.chaquo.python.PyObject
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform

/**
 * The bridge, kept running. A foreground service shows a lasting notification, and in exchange
 * Android does not end it to save memory or battery. Its type, connectedDevice, says it works
 * with an external device (the keyboard over USB).
 */
class BridgeService : Service() {

    companion object {
        const val ACTION_STOP_PLAYBACK = "org.vestacorelabs.armonico.STOP_PLAYBACK"
        const val ACTION_RESTART = "org.vestacorelabs.armonico.RESTART"
        const val ACTION_PLAY = "org.vestacorelabs.armonico.PLAY"
        const val ACTION_PAIR = "org.vestacorelabs.armonico.PAIR"
        const val ACTION_CAPTURE = "org.vestacorelabs.armonico.CAPTURE"
        const val ACTION_LIVE_LOG = "org.vestacorelabs.armonico.LIVE_LOG"
        const val ACTION_PAUSE = "org.vestacorelabs.armonico.PAUSE"
        const val ACTION_LESSON_STOP = "org.vestacorelabs.armonico.LESSON_STOP"
        const val ACTION_COMMAND = "org.vestacorelabs.armonico.COMMAND"
        const val ACTION_RECORD = "org.vestacorelabs.armonico.RECORD"
        const val ACTION_ALARM = "org.vestacorelabs.armonico.ALARM"
        const val ACTION_SHUTDOWN = "org.vestacorelabs.armonico.SHUTDOWN"
        const val ACTION_WATCH_PLAY = "org.vestacorelabs.armonico.WATCH_PLAY"
        const val ACTION_WATCH_SHORTCUT = "org.vestacorelabs.armonico.WATCH_SHORTCUT"
        const val EXTRA_LINE = "line"
        const val EXTRA_SONG = "song"
        const val EXTRA_ON = "on"
        private const val CHANNEL = "bridge"
        private const val NOTIFY_ID = 1

        fun start(ctx: Context, action: String? = null, song: String? = null) {
            val i = Intent(ctx, BridgeService::class.java)
            if (action != null) i.action = action
            if (song != null) i.putExtra(EXTRA_SONG, song)
            ContextCompat.startForegroundService(ctx, i)
        }
    }

    private lateinit var thread: HandlerThread
    private lateinit var handler: Handler
    private var link: MidiLink? = null
    private var py: PyObject? = null
    private var wake: PowerManager.WakeLock? = null

    override fun onBind(intent: Intent?): IBinder? = null

    // the notification in the language chosen in the app, which may differ from the phone's
    override fun attachBaseContext(base: Context) {
        val lang = Settings.prefs(base).getString("ui_lang", "") ?: ""
        if (lang.isEmpty()) return super.attachBaseContext(base)
        val conf = android.content.res.Configuration(base.resources.configuration)
        conf.setLocale(java.util.Locale(lang))
        super.attachBaseContext(base.createConfigurationContext(conf))
    }

    override fun onCreate() {
        super.onCreate()
        if (!Python.isStarted()) Python.start(AndroidPlatform(this))
        thread = HandlerThread("armonico-midi").apply { start() }
        handler = Handler(thread.looper)
        val nm = getSystemService(NotificationManager::class.java)
        if (Build.VERSION.SDK_INT >= 26) {
            nm.createNotificationChannel(
                NotificationChannel(CHANNEL, getString(R.string.channel_name), NotificationManager.IMPORTANCE_LOW)
            )
        }
        // the CPU stays awake while the bridge runs: a key or an alarm time must never wait
        // for the phone to wake up
        wake = (getSystemService(Context.POWER_SERVICE) as PowerManager)
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "armonico:bridge").apply {
                setReferenceCounted(false)
                acquire()
            }
    }

    private var shownText = ""

    /** The notification says what the bridge is doing: the alarm, a song, or listening. */
    private val statusListener: () -> Unit = {
        WearSync.push(this)                    // the watch app, when what it shows changed
        val text = notifyText()
        if (text != shownText) {
            shownText = text
            getSystemService(NotificationManager::class.java).notify(NOTIFY_ID, notification())
        }
    }

    private fun notifyText(): String {
        val s = AppState.status
        val song = s.optString("song", "")
        return when {
            song.isNotEmpty() && s.optBoolean("alarm") -> getString(R.string.notify_alarm, song)
            song.isNotEmpty() -> getString(R.string.notify_song, song)
            s.optBoolean("game") -> getString(R.string.notify_lesson)
            s.optBoolean("keyboard") -> getString(R.string.notify_listening, s.optString("device"))
            else -> getString(R.string.notify_waiting)
        }
    }

    private fun notification(): Notification {
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE
        )
        val stop = PendingIntent.getService(
            this, 1, Intent(this, BridgeService::class.java).setAction(ACTION_STOP_PLAYBACK),
            PendingIntent.FLAG_IMMUTABLE
        )
        val off = PendingIntent.getService(
            this, 2, Intent(this, BridgeService::class.java).setAction(ACTION_SHUTDOWN),
            PendingIntent.FLAG_IMMUTABLE
        )
        return NotificationCompat.Builder(this, CHANNEL)
            .setSmallIcon(R.drawable.ic_notify)
            .setContentTitle(getString(R.string.app_name))
            .setContentText(notifyText())
            .setContentIntent(open)
            .setOngoing(true)
            .addAction(0, getString(R.string.notify_stop), stop)
            .addAction(0, getString(R.string.bridge_stop), off)
            .build()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val type = if (Build.VERSION.SDK_INT >= 29) ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE else 0
        try {
            ServiceCompat.startForeground(this, NOTIFY_ID, notification(), type)
        } catch (e: Exception) {
            Log.e("Armonico", "could not start in the foreground", e)
            stopSelf()
            return START_NOT_STICKY
        }
        if (intent?.action == ACTION_SHUTDOWN) {
            // the bridge off from the notification: the service ends, and the notification goes with it.
            // It starts again from the app, with the phone (when chosen), or when a keyboard is plugged in.
            stopForeground(STOP_FOREGROUND_REMOVE)
            stopSelf()
            return START_NOT_STICKY
        }
        when (intent?.action) {
            ACTION_STOP_PLAYBACK -> py?.callAttr("local_stop", "Armonico app")
            ACTION_PLAY -> intent.getStringExtra(EXTRA_SONG)?.let { py?.callAttr("play_local", it) }
            ACTION_RESTART -> { stopBridge(); startBridge() }
            ACTION_PAIR -> {
                // a token typed and saved just now: the bot starts with the bridge, and then waits for /start
                py?.callAttr("telegram_pair", 120)
            }
            ACTION_CAPTURE -> py?.callAttr("capture_key", intent.getBooleanExtra(EXTRA_ON, true))
            ACTION_LIVE_LOG -> py?.callAttr("live_log", intent.getBooleanExtra(EXTRA_ON, true))
            ACTION_PAUSE -> py?.callAttr("pause_toggle")
            ACTION_LESSON_STOP -> py?.callAttr("lesson_stop")
            ACTION_COMMAND -> intent.getStringExtra(EXTRA_LINE)?.let { py?.callAttr("command", it) }
            ACTION_RECORD -> py?.callAttr("record", intent.getBooleanExtra(EXTRA_ON, false))
            ACTION_WATCH_PLAY -> intent.getStringExtra(EXTRA_SONG)?.let { py?.callAttr("watch_play", it) }
            ACTION_WATCH_SHORTCUT -> intent.getStringExtra(EXTRA_SONG)?.let { py?.callAttr("watch_shortcut", it) }
            ACTION_ALARM -> {
                if (py == null) startBridge()
                intent.getStringExtra(EXTRA_SONG)?.let { ringWhenReady(it, System.currentTimeMillis()) }
            }
            else -> if (py == null) startBridge()
        }
        if (py == null) startBridge()
        return START_STICKY
    }

    /** The app's own alarm clock went off. A bridge that has just started gives the keyboard up to 15 seconds
     *  to be found before the alarm rings; without it the bridge reports the alarm as failed, as always. */
    private fun ringWhenReady(song: String, since: Long) {
        val l = link
        if (l != null && !l.isConnected() && System.currentTimeMillis() - since < 15_000) {
            handler.postDelayed({ ringWhenReady(song, since) }, 500)
            return
        }
        py?.callAttr("alarm_local", song)
    }

    private fun startBridge() {
        Settings.seedSongs(this)
        val module = Python.getInstance().getModule("armonico_android.bridge")
        val wanted = Settings.prefs(this).getString("device", "") ?: ""
        val l = MidiLink(
            this, handler, wanted,
            onBytes = { bytes -> module.callAttr("midi_in", bytes) },
            onChange = { on -> module.callAttr("device_changed", on) },
        )
        link = l
        py = module
        module.callAttr("start", Settings.bridgeConfig(this), l)
        l.start()
        AppState.markRunning(true)
        WearSync.push(this)
        AppState.listen(statusListener)
    }

    private fun stopBridge() {
        try { py?.callAttr("stop") } catch (e: Exception) { Log.e("Armonico", "stop", e) }
        link?.shutdown()
        link = null
        py = null
    }

    override fun onDestroy() {
        AppState.unlisten(statusListener)
        stopBridge()
        AppState.markRunning(false)
        WearSync.push(this)
        wake?.release()
        thread.quitSafely()
        super.onDestroy()
    }
}
