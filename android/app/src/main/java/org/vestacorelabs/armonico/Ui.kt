package org.vestacorelabs.armonico

import android.annotation.SuppressLint
import android.content.Context
import android.content.res.ColorStateList
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RadialGradient
import android.graphics.RectF
import android.graphics.Shader
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.graphics.drawable.RippleDrawable
import android.text.InputType
import android.text.TextUtils
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import androidx.core.content.res.ResourcesCompat

/**
 * The shared design language of Armonico (the lesson screen's), in Android views.
 * Every value here is a value of the lesson screen's CSS: the same colors in the light and the
 * dark theme, Heebo, the gold button for the main action, the pills, the switch, the cards.
 */
class Palette(val dark: Boolean) {
    private fun c(s: String) = Color.parseColor(s)
    val bg = c(if (dark) "#0a1120" else "#f2f7fd")
    val surface = c(if (dark) "#111b2e" else "#ffffff")
    val head = c(if (dark) "#0a1120" else "#ffffff")
    val chip = c(if (dark) "#19263c" else "#e4edf9")
    val chip2 = c(if (dark) "#1c2f4f" else "#d3e5fb")
    val title = c(if (dark) "#eef3fb" else "#0e2a47")
    val ink = c(if (dark) "#dbe4f0" else "#13233a")
    val muted = c(if (dark) "#a3b3ca" else "#56667d")
    val line = c(if (dark) "#223049" else "#d3e0ef")
    val link = c(if (dark) "#8fc6f2" else "#0b4cb0")
    val onBg = c(if (dark) "#8fc6f2" else "#0b4cb0")
    val onFg = c(if (dark) "#0a1120" else "#ffffff")
    val okBg = c(if (dark) "#10301f" else "#e3f6eb")
    val okFg = c(if (dark) "#6fd69d" else "#17784a")
    val badBg = c(if (dark) "#3a1416" else "#fde8e8")
    val badFg = c(if (dark) "#ff8a8d" else "#c0282d")
    val gold = c("#e2b04a")
    val goldInk = c("#1d1606")
    val ok = c("#1f9d55")
    val bad = c("#e5484d")
    val neutralDot = c("#9aa8bb")
    // the banner is the logo: the brand's blue in both themes
    val band1 = c("#16386a")
    val band2 = c("#0a1b31")
    val band3 = c("#050a14")
    val bandKeys = c("#132c52")
    val bandGap = c("#070f1e")
}

enum class Btn { GOLD, GHOST, RED }
enum class Pill { ON, OFF, NEUTRAL }

class Ui(val ctx: Context, val p: Palette) {
    private val medium: Typeface = ResourcesCompat.getFont(ctx, R.font.heebo_medium) ?: Typeface.DEFAULT
    private val bold: Typeface = ResourcesCompat.getFont(ctx, R.font.heebo_bold) ?: Typeface.DEFAULT_BOLD
    private val extra: Typeface = ResourcesCompat.getFont(ctx, R.font.heebo_extrabold) ?: Typeface.DEFAULT_BOLD

    fun dp(v: Float) = TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v, ctx.resources.displayMetrics)
    fun dp(v: Int) = dp(v.toFloat()).toInt()

    fun shape(fill: Int, radius: Float, stroke: Int? = null) = GradientDrawable().apply {
        setColor(fill)
        cornerRadius = dp(radius)
        if (stroke != null) setStroke(dp(1), stroke)
    }

    private fun pressable(fill: Int, radius: Float, stroke: Int? = null) =
        RippleDrawable(ColorStateList.valueOf(Color.argb(40, 0, 0, 0)), shape(fill, radius, stroke), null)

    fun vbox(gap: Int = 0) = LinearLayout(ctx).apply {
        orientation = LinearLayout.VERTICAL
        if (gap > 0) {
            showDividers = LinearLayout.SHOW_DIVIDER_MIDDLE
            dividerDrawable = GradientDrawable().apply { setSize(1, dp(gap)); setColor(Color.TRANSPARENT) }
        }
    }

    fun hbox() = LinearLayout(ctx).apply {
        orientation = LinearLayout.HORIZONTAL
        gravity = Gravity.CENTER_VERTICAL
    }

    fun text(s: CharSequence, sp: Float = 16f, color: Int = p.ink, weight: Int = 500, ltr: Boolean = false) =
        TextView(ctx).apply {
            text = s
            setTextSize(TypedValue.COMPLEX_UNIT_SP, sp)
            setTextColor(color)
            typeface = when { weight >= 800 -> extra; weight >= 700 -> bold; else -> medium }
            if (ltr) {
                textDirection = View.TEXT_DIRECTION_LTR
                textAlignment = View.TEXT_ALIGNMENT_VIEW_START
            }
            includeFontPadding = false
            setLineSpacing(0f, 1.25f)
        }

    fun kicker(s: String) = text(s, 12f, p.link, 800).apply { letterSpacing = 0.02f }
    fun title(s: String) = text(s, 21f, p.title, 800)
    fun sub(s: CharSequence, sp: Float = 15f) = text(s, sp, p.muted, 500)
    fun label(s: String) = text(s, 13f, p.muted, 700).apply { setPadding(0, dp(12), 0, dp(5)) }

    fun card(padH: Int = 16, padV: Int = 16) = vbox().apply {
        background = shape(p.surface, 14f, p.line)
        setPadding(dp(padH), dp(padV), dp(padH), dp(padV))
        if (!p.dark) elevation = dp(1.5f)
    }

    fun button(label: String, kind: Btn = Btn.GHOST, small: Boolean = false, wide: Boolean = false, click: () -> Unit) =
        text(label, if (small) 13f else 15f, when (kind) { Btn.GOLD -> p.goldInk; Btn.GHOST -> p.title; Btn.RED -> p.bad }, 700).apply {
            gravity = Gravity.CENTER
            background = when (kind) {
                Btn.GOLD -> pressable(p.gold, 10f)
                Btn.GHOST -> pressable(p.surface, 10f, p.line)
                Btn.RED -> pressable(p.badBg, 10f)
            }
            if (small) setPadding(dp(13), dp(7), dp(13), dp(7)) else setPadding(dp(20), dp(13), dp(20), dp(13))
            minHeight = dp(if (small) 34 else 48)
            isClickable = true
            setOnClickListener { click() }
            if (wide) layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
        }

    fun pill(label: String, state: Pill) = hbox().apply {
        val (bg, fg, dot) = when (state) {
            Pill.ON -> Triple(p.okBg, p.okFg, p.ok)
            Pill.OFF -> Triple(p.badBg, p.badFg, p.bad)
            Pill.NEUTRAL -> Triple(p.chip, p.muted, p.neutralDot)
        }
        background = shape(bg, 99f)
        setPadding(dp(12), dp(6), dp(12), dp(6))
        addView(View(ctx).apply { background = shape(dot, 99f) }, LinearLayout.LayoutParams(dp(8), dp(8)))
        addView(text(label, 13f, fg, 700).apply { maxLines = 1 },
            LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { marginStart = dp(7) })
    }

    /** The segmented choice of the lesson screen: the chosen one in blue. */
    fun seg(options: List<String>, selected: Int, fill: Boolean = false, pick: (Int) -> Unit): LinearLayout {
        val box = hbox().apply { background = shape(p.chip, 10f); setPadding(dp(3), dp(3), dp(3), dp(3)) }
        val views = mutableListOf<TextView>()
        fun paint(sel: Int) = views.forEachIndexed { i, v ->
            v.background = if (i == sel) shape(p.onBg, 8f) else null
            v.setTextColor(if (i == sel) p.onFg else p.ink)
        }
        options.forEachIndexed { i, o ->
            val v = text(o, if (fill) 14f else 13f, p.ink, 700).apply {
                gravity = Gravity.CENTER
                setPadding(dp(13), dp(if (fill) 10 else 7), dp(13), dp(if (fill) 10 else 7))
                setOnClickListener { paint(i); pick(i) }
            }
            views += v
            box.addView(v, if (fill) LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
                           else LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        }
        paint(selected)
        return box
    }

    fun switch(on: Boolean, change: (Boolean) -> Unit) = Switch(ctx, p, on, change).apply {
        layoutParams = LinearLayout.LayoutParams(dp(42), dp(24))
    }

    fun field(value: String, hint: String = "", ltr: Boolean = false, password: Boolean = false, number: Boolean = false) =
        EditText(ctx).apply {
            setText(value)
            this.hint = hint
            setHintTextColor(p.muted)
            setTextColor(p.ink)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 15f)
            background = shape(p.surface, 10f, p.line)
            setPadding(dp(13), dp(11), dp(13), dp(11))
            isSingleLine = true
            inputType = when {
                number -> InputType.TYPE_CLASS_NUMBER
                password -> InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
                ltr -> InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
                else -> InputType.TYPE_CLASS_TEXT
            }
            typeface = medium                 // after inputType, which sets a password field in monospace
            if (ltr || password || number) {
                // an address, a token or a number reads left to right; the hint of an empty one, in the
                // language of the screen
                val dir = { textDirection = if (text.isNullOrEmpty()) View.TEXT_DIRECTION_LOCALE else View.TEXT_DIRECTION_LTR }
                dir()
                addTextChangedListener(object : android.text.TextWatcher {
                    override fun afterTextChanged(e: android.text.Editable?) = dir()
                    override fun beforeTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
                    override fun onTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
                })
            }
        }

    /** A row of a settings or status list: the label and a small line under it, and a control at the end. */
    fun row(label: String, small: CharSequence? = null, smallLtr: Boolean = false, end: View? = null): LinearLayout {
        val r = hbox().apply { setPadding(0, dp(11), 0, dp(11)) }
        val left = vbox()
        left.addView(text(label, 15f, p.ink, 500))
        if (small != null) left.addView(text(small, 13f, p.muted, 500, smallLtr).apply {
            setPadding(0, dp(3), 0, 0); maxLines = 2; ellipsize = TextUtils.TruncateAt.END
        })
        r.addView(left, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        if (end != null) r.addView(end, (end.layoutParams as? LinearLayout.LayoutParams
            ?: LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT))
            .apply { marginStart = dp(10) })
        return r
    }

    /** Rows with a thin line between them, as in the lesson screen's lists. */
    fun rows(vararg items: View) = vbox().apply {
        showDividers = LinearLayout.SHOW_DIVIDER_MIDDLE
        dividerDrawable = GradientDrawable().apply { setSize(1, dp(1)); setColor(p.line) }
        items.forEach { addView(it) }
    }

    fun gap(h: Int) = View(ctx).apply { layoutParams = LinearLayout.LayoutParams(1, dp(h)) }

    fun wrap() = LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT)
    fun matchW() = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
}

/** The lesson screen's switch: a blue track when on, a white knob. It follows the reading direction. */
@SuppressLint("ViewConstructor")
class Switch(ctx: Context, private val p: Palette, var on: Boolean, private val change: (Boolean) -> Unit) : View(ctx) {
    private val paint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val rect = RectF()

    init {
        isClickable = true
        setOnClickListener { on = !on; invalidate(); change(on) }
    }

    override fun onDraw(c: Canvas) {
        val w = width.toFloat(); val h = height.toFloat()
        paint.color = if (on) p.onBg else p.chip2
        rect.set(0f, 0f, w, h)
        c.drawRoundRect(rect, h / 2, h / 2, paint)
        val r = h / 2 - h * 0.125f
        val rtl = layoutDirection == LAYOUT_DIRECTION_RTL
        val atEnd = on != rtl
        val cx = if (atEnd) w - h / 2 else h / 2
        paint.color = Color.WHITE
        paint.setShadowLayer(h * 0.08f, 0f, h * 0.04f, Color.argb(64, 0, 0, 0))
        c.drawCircle(cx, h / 2, r, paint)
        paint.clearShadowLayer()
    }
}

/** The banner: the logo over a dark keyboard, as at the top of the lesson screen. */
class BannerView(ctx: Context, private val p: Palette) : View(ctx) {
    private val paint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val logo: Bitmap = BitmapFactory.decodeResource(resources, R.drawable.logo_banner)
    private val rect = RectF()

    override fun onDraw(c: Canvas) {
        val w = width.toFloat(); val h = height.toFloat()
        val d = resources.displayMetrics.density
        paint.shader = RadialGradient(w / 2, h * 0.38f, maxOf(w, h) * 0.75f,
            intArrayOf(p.band1, p.band2, p.band3), floatArrayOf(0f, 0.62f, 1f), Shader.TileMode.CLAMP)
        c.drawRect(0f, 0f, w, h, paint)
        paint.shader = null
        // the keyboard along the bottom: white keys 35 units wide, black keys over them, as in the CSS
        val kh = 40 * d
        val top = h - kh
        paint.color = p.bandKeys
        c.drawRect(0f, top, w, h, paint)
        val unit = kh * 1.53125f / 245f
        paint.color = p.bandGap
        var x0 = 0f
        while (x0 < w) {
            for (i in 0 until 7) c.drawRect(x0 + i * 35 * unit, top, x0 + (i * 35 + 1.5f) * unit, h, paint)
            for (bx in intArrayOf(25, 60, 130, 165, 200)) {
                rect.set(x0 + bx * unit, top, x0 + (bx + 20) * unit, top + 96 * unit * (kh / (160 * unit)))
                c.drawRoundRect(rect, 3 * unit, 3 * unit, paint)
            }
            x0 += 245 * unit
        }
        // the logo, centered above the keys
        val lw = minOf(w * 0.5f, 176 * d)
        val lh = lw * logo.height / logo.width
        val ly = (top - lh) / 2
        rect.set((w - lw) / 2, ly, (w + lw) / 2, ly + lh)
        c.drawBitmap(logo, null, rect, paint)
    }
}
