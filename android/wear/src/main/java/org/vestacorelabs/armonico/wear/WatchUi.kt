package org.vestacorelabs.armonico.wear

import android.content.Context
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.core.content.res.ResourcesCompat

/** The watch's building blocks: a round-screen page, a title, small text, buttons and switches, in the
 *  phone app's colors and its font (Heebo). */
class WatchUi(private val ctx: Context) {
    val bg = Color.parseColor("#050a14")
    val ink = Color.parseColor("#dbe4f0")
    val muted = Color.parseColor("#a3b3ca")
    val chip = Color.parseColor("#19263c")
    val gold = Color.parseColor("#e2b04a")
    val red = Color.parseColor("#e5484d")
    val green = Color.parseColor("#1f9d55")
    private val medium: Typeface? = try { ResourcesCompat.getFont(ctx, R.font.heebo_medium) } catch (e: Exception) { null }
    private val bold: Typeface? = try { ResourcesCompat.getFont(ctx, R.font.heebo_bold) } catch (e: Exception) { null }

    fun dp(v: Int) = TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v.toFloat(), ctx.resources.displayMetrics).toInt()

    /** A column kept inside the round screen: wide side margins, room at the top and the bottom. */
    fun page() = LinearLayout(ctx).apply {
        orientation = LinearLayout.VERTICAL
        gravity = Gravity.CENTER_HORIZONTAL
        val w = ctx.resources.displayMetrics.widthPixels
        setPadding(w / 9, w / 7, w / 9, w / 5)
    }

    /** The page in a scroll view that also follows the rotating bezel or crown (rotary input), when focused. */
    fun scroll(content: View) = ScrollView(ctx).apply {
        setBackgroundColor(bg)
        isVerticalScrollBarEnabled = false
        addView(content)
        isFocusable = true
        isFocusableInTouchMode = true
        val factor = android.view.ViewConfiguration.get(ctx).scaledVerticalScrollFactor
        setOnGenericMotionListener { v, e ->
            if (e.action == android.view.MotionEvent.ACTION_SCROLL &&
                e.isFromSource(android.view.InputDevice.SOURCE_ROTARY_ENCODER)) {
                // the bezel turns in clicks: each one glides instead of jumping
                (v as ScrollView).smoothScrollBy(0, Math.round(-e.getAxisValue(android.view.MotionEvent.AXIS_SCROLL) * factor))
                true
            } else false
        }
        post { requestFocus() }
    }

    fun title(s: String) = TextView(ctx).apply {
        text = s; setTextColor(Color.WHITE); textSize = 17f; typeface = bold
        gravity = Gravity.CENTER; setPadding(0, 0, 0, dp(4))
    }

    fun small(s: CharSequence, color: Int = muted) = TextView(ctx).apply {
        text = s; setTextColor(color); textSize = 13f; typeface = medium
        gravity = Gravity.CENTER; setPadding(0, dp(2), 0, dp(4))
    }

    fun button(label: String, fill: Int = chip, textColor: Int = if (fill == chip) ink else Color.parseColor("#1d1606"),
               click: () -> Unit) = TextView(ctx).apply {
        text = label; setTextColor(if (fill == red) Color.WHITE else textColor); textSize = 14f; typeface = bold
        gravity = Gravity.CENTER
        background = GradientDrawable().apply { setColor(fill); cornerRadius = dp(22).toFloat() }
        setPadding(dp(10), dp(10), dp(10), dp(10))
        layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT)
            .apply { topMargin = dp(6) }
        setOnClickListener { click() }
    }

    val card = Color.parseColor("#111b2e")

    /** The app's small logo, at the top of a screen. */
    fun logo() = android.widget.ImageView(ctx).apply {
        setImageResource(R.drawable.logo_icon_dark)
        layoutParams = LinearLayout.LayoutParams(dp(26), dp(26)).apply { bottomMargin = dp(2) }
        clipToOutline = true
        background = GradientDrawable().apply { cornerRadius = dp(7).toFloat() }
    }

    /** A round button with a symbol, for the controls of what plays. */
    fun round(symbol: String, fill: Int = chip, click: () -> Unit) = TextView(ctx).apply {
        text = symbol; textSize = 16f; gravity = Gravity.CENTER
        setTextColor(if (fill == chip) ink else if (fill == gold) Color.parseColor("#1d1606") else Color.WHITE)
        background = GradientDrawable().apply { shape = GradientDrawable.OVAL; setColor(fill) }
        layoutParams = LinearLayout.LayoutParams(dp(44), dp(44)).apply { marginStart = dp(5); marginEnd = dp(5); topMargin = dp(4) }
        setOnClickListener { click() }
    }

    /** The same round button with one of the watch's icons (res/drawable/ic_t_*), so all of them match. */
    fun roundIcon(icon: Int, fill: Int = chip, click: () -> Unit) = android.widget.ImageView(ctx).apply {
        setImageResource(icon)
        setColorFilter(if (fill == gold) Color.parseColor("#1d1606") else Color.WHITE)
        scaleType = android.widget.ImageView.ScaleType.CENTER_INSIDE
        setPadding(dp(11), dp(11), dp(11), dp(11))
        background = GradientDrawable().apply { shape = GradientDrawable.OVAL; setColor(fill) }
        layoutParams = LinearLayout.LayoutParams(dp(44), dp(44)).apply { marginStart = dp(5); marginEnd = dp(5); topMargin = dp(4) }
        setOnClickListener { click() }
    }

    fun centerRow(vararg views: View) = LinearLayout(ctx).apply {
        orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER
        views.forEach { addView(it) }
    }

    /** A row of the list: a small round icon, the title, and a line under it. */
    fun item(icon: String, title: String, sub: String? = null, iconColor: Int = ink, click: () -> Unit) = LinearLayout(ctx).apply {
        orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL
        background = GradientDrawable().apply { setColor(card); cornerRadius = dp(18).toFloat() }
        setPadding(dp(10), dp(8), dp(12), dp(8))
        layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT)
            .apply { topMargin = dp(5) }
        addView(TextView(ctx).apply {
            text = icon; textSize = 12f; gravity = Gravity.CENTER; setTextColor(iconColor)
            background = GradientDrawable().apply { shape = GradientDrawable.OVAL; setColor(chip) }
        }, LinearLayout.LayoutParams(dp(26), dp(26)))
        addView(LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            addView(TextView(ctx).apply { text = title; textSize = 14f; typeface = bold; setTextColor(ink) })
            if (!sub.isNullOrEmpty()) addView(TextView(ctx).apply { text = sub; textSize = 11.5f; typeface = medium; setTextColor(muted) })
        }, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f).apply { marginStart = dp(9) })
        setOnClickListener { click() }
    }

    /** A switch row: the label, and on or off in color. */
    fun toggle(label: String, on: Boolean, change: (Boolean) -> Unit) =
        button((if (on) "● " else "○ ") + label, chip, if (on) gold else muted) { change(!on) }

    fun row(vararg views: View) = LinearLayout(ctx).apply {
        orientation = LinearLayout.HORIZONTAL
        layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT)
        views.forEachIndexed { i, v ->
            addView(v, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f).apply {
                topMargin = dp(6); if (i > 0) marginStart = dp(6)
            })
        }
    }
}
