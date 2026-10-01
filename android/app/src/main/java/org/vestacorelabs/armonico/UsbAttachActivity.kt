package org.vestacorelabs.armonico

import android.app.Activity
import android.os.Bundle

/** A keyboard was plugged in: make sure the bridge runs, and show nothing. */
class UsbAttachActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (Settings.ready(this)) BridgeService.start(this)
        finish()
    }
}
