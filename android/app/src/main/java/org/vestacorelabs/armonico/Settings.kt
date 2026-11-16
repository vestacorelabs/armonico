package org.vestacorelabs.armonico

import android.content.Context
import android.net.ConnectivityManager
import org.json.JSONObject
import java.net.Inet4Address
import java.io.File
import java.security.SecureRandom

/** The app's settings, kept by Android for the app alone (SharedPreferences). */
object Settings {
    private const val FILE = "armonico"

    fun prefs(ctx: Context) = ctx.getSharedPreferences(FILE, Context.MODE_PRIVATE)

    fun songsDir(ctx: Context) = File(ctx.filesDir, "songs").apply { mkdirs() }

    /** The admin secret is drawn once, 24 random bytes as the Linux installer draws its own. */
    fun secret(ctx: Context): String {
        val p = prefs(ctx)
        var s = p.getString("admin_secret", "") ?: ""
        if (s.isEmpty()) {
            val b = ByteArray(24).also { SecureRandom().nextBytes(it) }
            s = b.joinToString("") { "%02x".format(it) }
            p.edit().putString("admin_secret", s).apply()
        }
        return s
    }

    /** The two passwords of the built-in broker, drawn once each, 18 random bytes as install.sh draws them. */
    fun localPassword(ctx: Context): String = drawn(ctx, "local_password")
    fun localRecPassword(ctx: Context): String = drawn(ctx, "local_rec_password")

    private fun drawn(ctx: Context, key: String): String {
        val p = prefs(ctx)
        var s = p.getString(key, "") ?: ""
        if (s.length < 16) {
            val b = ByteArray(18).also { SecureRandom().nextBytes(it) }
            s = android.util.Base64.encodeToString(b, android.util.Base64.NO_WRAP or android.util.Base64.URL_SAFE or android.util.Base64.NO_PADDING)
            p.edit().putString(key, s).apply()
        }
        return s
    }

    /** Is the bridge set up enough to start on its own (at boot, or when a keyboard is plugged in)? */
    fun ready(ctx: Context): Boolean {
        val p = prefs(ctx)
        return p.getString("mode", "local") == "local" || (p.getString("host", "") ?: "").isNotBlank()
    }

    /** The phone's own address on the network, for the built-in broker's card. */
    fun phoneAddress(ctx: Context): String? {
        val cm = ctx.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager? ?: return null
        val net = cm.activeNetwork ?: return null
        return cm.getLinkProperties(net)?.linkAddresses
            ?.map { it.address }?.firstOrNull { it is Inet4Address && !it.isLoopbackAddress }?.hostAddress
    }

    /** The songs that come with the project, copied in once. A song deleted later stays deleted. */
    fun seedSongs(ctx: Context) {
        val p = prefs(ctx)
        if (p.getBoolean("seeded", false)) return
        val dir = songsDir(ctx)
        ctx.assets.list("songs")?.forEach { name ->
            ctx.assets.open("songs/$name").use { input ->
                File(dir, name).outputStream().use { input.copyTo(it) }
            }
        }
        p.edit().putBoolean("seeded", true).apply()
    }

    fun songNames(ctx: Context): List<String> =
        songsDir(ctx).listFiles()
            ?.filter { it.name.endsWith(".mid", true) || it.name.endsWith(".midi", true) }
            ?.map { it.nameWithoutExtension }
            ?.sortedBy { it.lowercase() }
            ?: emptyList()

    /** The lesson engine's files, unpacked from the app once per installed version: the engine
     *  reads its fonts, sounds and pages next to its own file, as on Linux. */
    fun engineDir(ctx: Context): File {
        val dir = File(ctx.filesDir, "engine")
        val stamp = try {
            val pi = ctx.packageManager.getPackageInfo(ctx.packageName, 0)
            "${pi.lastUpdateTime}"
        } catch (e: Exception) { "0" }
        val mark = File(dir, ".unpacked")
        if (mark.exists() && mark.readText() == stamp) return dir
        dir.deleteRecursively()
        fun copy(path: String, to: File) {
            val list = ctx.assets.list(path) ?: emptyArray()
            if (list.isEmpty()) {
                to.parentFile?.mkdirs()
                ctx.assets.open(path).use { i -> to.outputStream().use { i.copyTo(it) } }
            } else list.forEach { copy("$path/$it", File(to, it)) }
        }
        copy("engine", dir)
        mark.writeText(stamp)
        return dir
    }

    /** What the Python bridge reads, the same names as config.env on Linux in lower case. */
    /** The language of the app's own screens: the one chosen in the app, or the phone's. Written to a file the
     *  bridge reads, so its answers on the command screen and in the app's dialogs follow a change at once. */
    fun appLang(ctx: Context): String {
        val chosen = prefs(ctx).getString("ui_lang", "") ?: ""
        val l = chosen.ifEmpty { java.util.Locale.getDefault().language }
        return if (l == "iw" || l == "he") "he" else "en"
    }

    fun writeAppLang(ctx: Context) {
        try { File(ctx.filesDir, "app_lang").writeText(appLang(ctx)) } catch (e: Exception) { }
    }

    fun bridgeConfig(ctx: Context): String {
        writeAppLang(ctx)
        val p = prefs(ctx)
        return JSONObject().apply {
            put("mode", p.getString("mode", "local") ?: "local")
            put("local_port", p.getInt("local_port", 1883))
            put("local_user", "piano")
            put("local_password", localPassword(ctx))
            put("local_rec_password", localRecPassword(ctx))
            put("local_ws_port", 9001)
            put("record_days", p.getInt("record_days", 14))
            put("telegram_token", p.getString("telegram_token", "") ?: "")
            put("host", p.getString("host", "") ?: "")
            put("port", p.getInt("port", 1883))
            put("user", p.getString("user", "") ?: "")
            put("password", p.getString("password", "") ?: "")
            put("stop_key", p.getInt("stop_key", 36))
            put("stop_pedal", p.getBoolean("stop_pedal", false))
            put("key_lowest", p.getInt("key_lowest", 36))
            put("key_highest", p.getInt("key_highest", 96))
            put("lang", p.getString("lang", "en") ?: "en")
            put("admin_secret", secret(ctx))
            put("data_dir", ctx.filesDir.absolutePath)
            put("songs_dir", songsDir(ctx).absolutePath)
            try {
                put("engine_dir", engineDir(ctx).absolutePath)
            } catch (e: Exception) {
                put("engine_dir", "")
                put("engine_error", "the engine's files were not unpacked: ${e.javaClass.simpleName}: ${e.message}")
            }
            put("lessons", true)
            put("lesson_port", 8099)
        }.toString()
    }
}
