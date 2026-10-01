package org.vestacorelabs.armonico

import org.json.JSONObject

/** What the screen shows: the service tells it here, and the screen listens while it is open. */
object AppState {
    @Volatile var running = false
    @Volatile var status: JSONObject = JSONObject()

    private val listeners = mutableSetOf<() -> Unit>()

    fun update(json: String) {
        status = try { JSONObject(json) } catch (e: Exception) { JSONObject() }
        tell()
    }

    fun markRunning(on: Boolean) {
        running = on
        if (!on) status = JSONObject()
        tell()
    }

    @Synchronized fun listen(l: () -> Unit) { listeners.add(l) }
    @Synchronized fun unlisten(l: () -> Unit) { listeners.remove(l) }
    @Synchronized private fun tell() { listeners.toList().forEach { it() } }
}
