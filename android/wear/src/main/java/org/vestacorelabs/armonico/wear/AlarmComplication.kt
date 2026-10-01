package org.vestacorelabs.armonico.wear

import android.app.PendingIntent
import android.content.Intent
import androidx.wear.watchface.complications.data.ComplicationData
import androidx.wear.watchface.complications.data.ComplicationType
import androidx.wear.watchface.complications.data.PlainComplicationText
import androidx.wear.watchface.complications.data.ShortTextComplicationData
import androidx.wear.watchface.complications.datasource.ComplicationDataSourceService
import androidx.wear.watchface.complications.datasource.ComplicationRequest
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * The Complication: a small item on the watch face showing the next alarm (or the song playing).
 * A tap opens the watch app. Redrawn when a new state comes from the phone (Watch.updateViews).
 */
class AlarmComplication : ComplicationDataSourceService() {

    override fun onComplicationRequest(request: ComplicationRequest, listener: ComplicationRequestListener) {
        val st = Watch.state(this)
        val next = st.optLong("next_at")
        val (text, title) = when {
            st.optBoolean("alarm") -> "⏰" to st.optString("song").replace('_', ' ')
            st.optString("song").isNotEmpty() -> "♪" to st.optString("song").replace('_', ' ')
            next > 0 -> SimpleDateFormat("HH:mm", Locale.getDefault()).format(Date(next)) to "⏰"
            else -> "--:--" to "⏰"
        }
        listener.onComplicationData(build(text, title.take(12)))
    }

    override fun getPreviewData(type: ComplicationType): ComplicationData? =
        if (type == ComplicationType.SHORT_TEXT) build("07:00", "⏰") else null

    private fun build(text: String, title: String): ComplicationData {
        val tap = PendingIntent.getActivity(this, 0, Intent(this, WatchActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        return ShortTextComplicationData.Builder(
            PlainComplicationText.Builder(text).build(),
            PlainComplicationText.Builder(getString(R.string.comp_label)).build())
            .setTitle(PlainComplicationText.Builder(title).build())
            .setTapAction(tap)
            .build()
    }
}
