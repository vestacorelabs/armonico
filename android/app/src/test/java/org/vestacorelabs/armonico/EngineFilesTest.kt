package org.vestacorelabs.armonico

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.json.JSONObject
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.annotation.Config
import java.io.File

/** The lesson engine's files come out of the app whole, where the bridge is told to look. */
@RunWith(AndroidJUnit4::class)
@Config(sdk = [35])
class EngineFilesTest {
    @Test fun unpacked_and_passed_to_the_bridge() {
        val ctx = ApplicationProvider.getApplicationContext<Context>()
        val cfg = JSONObject(Settings.bridgeConfig(ctx))
        val dir = File(cfg.getString("engine_dir"))
        println("engine_dir=$dir files=" + dir.walkTopDown().filter { it.isFile }.count())
        assertTrue(File(dir, "lessons/piano_game.py").length() > 100000)
        assertTrue(File(dir, "lessons/sounds/piano/A0.mp3").exists())
        assertTrue(File(dir, "lessons/fonts/heebo-hebrew.woff2").exists())
        assertTrue(File(dir, "screen/piano-status.html").exists())
        assertTrue(File(dir, "setup/music/fur_elise.mid").exists())
        assertTrue(File(dir, "VERSION").exists())
        assertTrue(cfg.getBoolean("lessons"))
    }
}
