package org.vestacorelabs.armonico.wear

import androidx.wear.protolayout.ActionBuilders
import androidx.wear.protolayout.ColorBuilders.argb
import androidx.wear.protolayout.DimensionBuilders.dp
import androidx.wear.protolayout.DimensionBuilders.expand
import androidx.wear.protolayout.DimensionBuilders.sp
import androidx.wear.protolayout.LayoutElementBuilders
import androidx.wear.protolayout.ModifiersBuilders
import androidx.wear.protolayout.ResourceBuilders
import androidx.wear.protolayout.TimelineBuilders
import androidx.wear.tiles.RequestBuilders
import androidx.wear.tiles.TileBuilders
import androidx.wear.tiles.TileService
import com.google.common.util.concurrent.Futures
import com.google.common.util.concurrent.ListenableFuture
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * The tile beside the watch face. From the top: the logo, the keyboard's state, the next alarm in large
 * gold (or the song that plays), three round buttons with the watch's own icons (the bridge on or off,
 * pause or play, stop), and the shortcut chosen in the watch's settings. A tap on a button comes back
 * here as its id; a tap anywhere else opens the watch app.
 */
class ArmonicoTile : TileService() {

    override fun onTileRequest(req: RequestBuilders.TileRequest): ListenableFuture<TileBuilders.Tile> {
        val st = Watch.state(this)
        when (req.currentState.lastClickableId) {
            "power" -> Watch.send(this, JSONObject().put("op", if (st.optBoolean("running")) "bridge_off" else "bridge_on"))
            "stop" -> Watch.send(this, JSONObject().put("op", "stop"))
            "pause" -> Watch.send(this, JSONObject().put("op", "pause"))
            "sc" -> Watch.tileShortcut(this).takeIf { it.isNotEmpty() }
                ?.let { Watch.send(this, JSONObject().put("op", "shortcut").put("sid", it)) }
        }
        val running = st.optBoolean("running")
        val song = st.optString("song").replace('_', ' ')
        val col = LayoutElementBuilders.Column.Builder()
            .setHorizontalAlignment(LayoutElementBuilders.HORIZONTAL_ALIGN_CENTER)
            .addContent(image("logo", 24f))
            .addContent(space(2f))
            .addContent(text(stateLine(st), 11f, if (running && st.optBoolean("keyboard")) GREEN else MUTED))
        // the middle: what rings or plays now, else the next alarm
        when {
            st.optBoolean("alarm") -> col.addContent(text(getString(R.string.stop_alarm), 18f, RED, bold = true))
                .addContent(text(song, 12f, INK))
            song.isNotEmpty() -> col.addContent(text(song, 17f, WHITE, bold = true))
                .addContent(text(getString(if (st.optBoolean("paused")) R.string.paused_short else R.string.playing_short), 11f, MUTED))
            st.optLong("next_at") > 0 -> {
                val at = Date(st.optLong("next_at"))
                col.addContent(text(SimpleDateFormat("HH:mm", Locale.ROOT).format(at), 30f, GOLD, bold = true))
                    .addContent(text(SimpleDateFormat("EEE", Locale.getDefault()).format(at) + " · " +
                        st.optString("next_song").replace('_', ' '), 11f, INK))
            }
            else -> col.addContent(text("--:--", 30f, MUTED, bold = true))
                .addContent(text(getString(R.string.no_alarm), 11f, MUTED))
        }
        col.addContent(space(6f)).addContent(LayoutElementBuilders.Row.Builder()
            .setVerticalAlignment(LayoutElementBuilders.VERTICAL_ALIGN_CENTER)
            .addContent(button("power", "power", if (running) GREEN else CHIP))
            .addContent(gap())
            .addContent(button("pause", if (st.optBoolean("paused")) "play" else "pause", CHIP))
            .addContent(gap())
            .addContent(button("stop", "stop", RED))
            .build())
        Watch.tileShortcut(this).takeIf { it.isNotEmpty() }?.let { col.addContent(space(5f)).addContent(pill(it.replace('_', ' '))) }
        val layout = LayoutElementBuilders.Box.Builder().setWidth(expand()).setHeight(expand())
            .setModifiers(ModifiersBuilders.Modifiers.Builder().setClickable(open()).build())
            .addContent(col.build()).build()
        return Futures.immediateFuture(TileBuilders.Tile.Builder()
            .setResourcesVersion(RES)
            .setTileTimeline(TimelineBuilders.Timeline.fromLayoutElement(layout))
            .build())
    }

    override fun onTileResourcesRequest(req: RequestBuilders.ResourcesRequest): ListenableFuture<ResourceBuilders.Resources> {
        val r = ResourceBuilders.Resources.Builder().setVersion(RES)
        mapOf("logo" to R.drawable.logo_icon_dark, "power" to R.drawable.ic_t_power, "pause" to R.drawable.ic_t_pause,
            "play" to R.drawable.ic_t_play, "stop" to R.drawable.ic_t_stop, "bolt" to R.drawable.ic_t_bolt)
            .forEach { (id, res) ->
                r.addIdToImageMapping(id, ResourceBuilders.ImageResource.Builder().setAndroidResourceByResId(
                    ResourceBuilders.AndroidImageResourceByResId.Builder().setResourceId(res).build()).build())
            }
        return Futures.immediateFuture(r.build())
    }

    private fun stateLine(st: JSONObject) = when {
        st.length() == 0 -> getString(R.string.waiting_phone)
        !st.optBoolean("running") -> getString(R.string.bridge_off)
        st.optBoolean("keyboard") -> "● " + st.optString("device")
        else -> getString(R.string.keyboard_off)
    }

    private fun open() = ModifiersBuilders.Clickable.Builder().setId("open")
        .setOnClick(ActionBuilders.LaunchAction.Builder().setAndroidActivity(
            ActionBuilders.AndroidActivity.Builder().setPackageName(packageName)
                .setClassName(WatchActivity::class.java.name).build()).build()).build()

    private fun tap(id: String) = ModifiersBuilders.Clickable.Builder().setId(id)
        .setOnClick(ActionBuilders.LoadAction.Builder().build()).build()

    private fun text(t: String, size: Float, color: Int, bold: Boolean = false) = LayoutElementBuilders.Text.Builder()
        .setText(t).setMaxLines(1)
        .setFontStyle(LayoutElementBuilders.FontStyle.Builder().setSize(sp(size)).setColor(argb(color))
            .setWeight(if (bold) LayoutElementBuilders.FONT_WEIGHT_BOLD else LayoutElementBuilders.FONT_WEIGHT_NORMAL).build())
        .build()

    private fun image(id: String, size: Float, tint: Int? = null) = LayoutElementBuilders.Image.Builder()
        .setResourceId(id).setWidth(dp(size)).setHeight(dp(size)).apply {
            if (tint != null) setColorFilter(LayoutElementBuilders.ColorFilter.Builder().setTint(argb(tint)).build())
        }.build()

    private fun space(h: Float) = LayoutElementBuilders.Spacer.Builder().setHeight(dp(h)).build()
    private fun gap() = LayoutElementBuilders.Spacer.Builder().setWidth(dp(8f)).build()

    private fun round(fill: Int, radius: Float) = ModifiersBuilders.Background.Builder().setColor(argb(fill))
        .setCorner(ModifiersBuilders.Corner.Builder().setRadius(dp(radius)).build()).build()

    /** A round button: the same size, the same icon set and the same white for all three. */
    private fun button(id: String, icon: String, fill: Int) = LayoutElementBuilders.Box.Builder()
        .setWidth(dp(44f)).setHeight(dp(44f))
        .setModifiers(ModifiersBuilders.Modifiers.Builder().setClickable(tap(id)).setBackground(round(fill, 22f)).build())
        .addContent(image(icon, 22f, WHITE)).build()

    private fun pill(name: String) = LayoutElementBuilders.Box.Builder().setHeight(dp(30f))
        .setModifiers(ModifiersBuilders.Modifiers.Builder().setClickable(tap("sc")).setBackground(round(CARD, 15f))
            .setPadding(ModifiersBuilders.Padding.Builder().setStart(dp(12f)).setEnd(dp(12f)).build()).build())
        .addContent(LayoutElementBuilders.Row.Builder()
            .setVerticalAlignment(LayoutElementBuilders.VERTICAL_ALIGN_CENTER)
            .addContent(image("bolt", 16f, GOLD))
            .addContent(LayoutElementBuilders.Spacer.Builder().setWidth(dp(5f)).build())
            .addContent(text(name, 12f, WHITE)).build())
        .build()

    companion object {
        private const val RES = "2"
        private const val GOLD = 0xffe2b04a.toInt()
        private const val GREEN = 0xff1f9d55.toInt()
        private const val MUTED = 0xffa3b3ca.toInt()
        private const val INK = 0xffdbe4f0.toInt()
        private const val WHITE = 0xffffffff.toInt()
        private const val RED = 0xffe5484d.toInt()
        private const val CHIP = 0xff19263c.toInt()
        private const val CARD = 0xff121c2d.toInt()
    }
}
