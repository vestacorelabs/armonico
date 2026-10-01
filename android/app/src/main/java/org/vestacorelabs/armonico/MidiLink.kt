package org.vestacorelabs.armonico

import android.content.Context
import android.media.midi.MidiDevice
import android.media.midi.MidiDeviceInfo
import android.media.midi.MidiInputPort
import android.media.midi.MidiManager
import android.media.midi.MidiOutputPort
import android.media.midi.MidiReceiver
import android.os.Handler
import android.util.Log

/**
 * The USB keyboard, through Android's MIDI service. Android names the two directions from the
 * device's side: its OUTPUT port is what the keyboard sends (the keys), its INPUT port is what
 * it receives (the songs and the alarm).
 *
 * The Python bridge calls send, isConnected, deviceName, reopen and status on this object.
 */
@Suppress("DEPRECATION")
class MidiLink(
    private val ctx: Context,
    private val handler: Handler,
    private val wanted: String,
    private val onBytes: (ByteArray) -> Unit,
    private val onChange: (Boolean) -> Unit,
) {
    private val mm = ctx.getSystemService(Context.MIDI_SERVICE) as MidiManager?
    private var info: MidiDeviceInfo? = null
    private var device: MidiDevice? = null
    private var input: MidiInputPort? = null
    private var output: MidiOutputPort? = null
    private val lock = Any()

    private val receiver = object : MidiReceiver() {
        override fun onSend(msg: ByteArray, offset: Int, count: Int, timestamp: Long) {
            onBytes(msg.copyOfRange(offset, offset + count))
        }
    }

    private val callback = object : MidiManager.DeviceCallback() {
        override fun onDeviceAdded(d: MidiDeviceInfo) {
            if (info == null) openBest()
        }

        override fun onDeviceRemoved(d: MidiDeviceInfo) {
            if (d.id == info?.id) {
                close()
                onChange(false)
                openBest()               // another keyboard that is still plugged in
            }
        }
    }

    fun start() {
        mm?.registerDeviceCallback(callback, handler)
        openBest()
    }

    fun shutdown() {
        try { mm?.unregisterDeviceCallback(callback) } catch (e: Exception) { }
        close()
    }

    private fun nameOf(d: MidiDeviceInfo): String {
        val p = d.properties
        return p.getString(MidiDeviceInfo.PROPERTY_NAME)
            ?: p.getString(MidiDeviceInfo.PROPERTY_PRODUCT)
            ?: "USB MIDI"
    }

    private fun candidates(): List<MidiDeviceInfo> =
        mm?.devices?.filter { it.type == MidiDeviceInfo.TYPE_USB } ?: emptyList()

    fun names(): List<String> = candidates().map { nameOf(it) }

    /** The chosen keyboard by name (exact, then one that starts with it), or the first one found. */
    private fun openBest() {
        if (info != null) return
        val list = candidates()
        val pick = if (wanted.isBlank()) list.firstOrNull()
                   else list.firstOrNull { nameOf(it) == wanted } ?: list.firstOrNull { nameOf(it).startsWith(wanted) }
        pick ?: return
        mm?.openDevice(pick, { dev ->
            if (dev == null) return@openDevice
            synchronized(lock) {
                device = dev
                info = pick
                pick.ports.firstOrNull { it.type == MidiDeviceInfo.PortInfo.TYPE_OUTPUT }?.let { p ->
                    output = dev.openOutputPort(p.portNumber)?.also { it.connect(receiver) }
                }
                pick.ports.firstOrNull { it.type == MidiDeviceInfo.PortInfo.TYPE_INPUT }?.let { p ->
                    input = dev.openInputPort(p.portNumber)
                }
            }
            Log.i("Armonico", "keyboard open: ${nameOf(pick)}")
            onChange(true)
        }, handler)
    }

    private fun close() {
        synchronized(lock) {
            try { output?.disconnect(receiver) } catch (e: Exception) { }
            try { output?.close() } catch (e: Exception) { }
            try { input?.close() } catch (e: Exception) { }
            try { device?.close() } catch (e: Exception) { }
            output = null; input = null; device = null; info = null
        }
    }

    // ---- what Python calls ----

    /** Bytes to the keyboard. Android takes at most about 1 KB in one call, so longer ones go in parts. */
    fun send(data: ByteArray): Boolean {
        synchronized(lock) {
            val port = input ?: return false
            return try {
                var i = 0
                while (i < data.size) {
                    val n = minOf(1000, data.size - i)
                    port.send(data, i, n)
                    i += n
                }
                true
            } catch (e: Exception) {
                false
            }
        }
    }

    fun isConnected(): Boolean = synchronized(lock) { input != null || output != null }

    fun deviceName(): String = info?.let { nameOf(it) } ?: ""

    /** /pianoreset: the device is closed and opened again. The bridge checks the result itself
     *  afterwards, so no "gone" is reported for the moment in between. */
    fun reopen() {
        close()
        handler.post { openBest() }
    }

    fun status(json: String) = AppState.update(json)
}
