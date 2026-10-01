package org.vestacorelabs.armonico

import android.annotation.SuppressLint
import android.os.Bundle
import android.view.View
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import org.json.JSONObject

/**
 * The lesson screen, from the engine on this phone, in a window of the app. It is the same page
 * as in a browser at the phone's address; here it is let in with the screen code by itself.
 */
class LessonsActivity : ComponentActivity() {
    private lateinit var web: WebView
    private var signedIn = false

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val port = intent.getIntExtra("port", 8099)
        val code = intent.getStringExtra("code") ?: ""
        web = WebView(this).apply {
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            settings.mediaPlaybackRequiresUserGesture = false     // the screen's piano sounds and clicks
            setBackgroundColor(0)
            webViewClient = object : WebViewClient() {
                override fun onPageFinished(view: WebView, url: String) {
                    if (!signedIn && code.isNotEmpty()) {
                        signedIn = true
                        // the same request the screen's code box sends; the pass it answers with is kept
                        val body = JSONObject().put("code", code).toString()
                        view.evaluateJavascript(
                            "fetch('/code',{method:'POST',headers:{'Content-Type':'application/json'},body:'" +
                                body.replace("'", "\\'") + "'}).then(r=>{if(r.ok)location.reload()})", null)
                    }
                }
            }
            systemUiVisibility = View.SYSTEM_UI_FLAG_LAYOUT_STABLE
        }
        setContentView(web)
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() { if (web.canGoBack()) web.goBack() else finish() }
        })
        web.loadUrl("http://127.0.0.1:$port/")
    }

    override fun onDestroy() {
        web.destroy()
        super.onDestroy()
    }
}
