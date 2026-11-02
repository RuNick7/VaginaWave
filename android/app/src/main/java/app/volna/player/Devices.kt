package app.volna.player

import android.content.Context
import android.os.Build
import android.provider.Settings
import app.volna.data.Prefs
import app.volna.data.Track
import app.volna.data.VolnaApi
import app.volna.data.str
import kotlinx.coroutines.MainScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.util.UUID

/** Другое устройство того же пользователя и что у него играет. */
data class RemoteDevice(
    val id: String,
    val name: String,
    val playing: Boolean,
    val positionMs: Long,
    val wave: Boolean, // играла «Моя волна»: после переданных треков продолжим волной
    val track: Track?,
    val next: List<Track>,
)

/**
 * «Играет на X. Слушать здесь?», как Spotify Connect. Раз в 5 секунд, пока играет (и раз в
 * 10, пока приложение открыто), сообщаем сервису, что играет здесь, и узнаём, что играет на
 * других устройствах. Забрали музыку себе — тот же трек с того же места и его очередь, а
 * прежнее устройство встаёт на паузу. Так же и наоборот: забрали отсюда — здесь пауза.
 */
object Devices {
    private val scope = MainScope()
    private lateinit var api: VolnaApi
    private lateinit var prefs: Prefs
    private lateinit var name: String
    private var reporting = false

    /** Приложение на экране: тогда следим за другими устройствами и без своей музыки. */
    var visible = false

    private val _others = MutableStateFlow<List<RemoteDevice>>(emptyList())
    val others: StateFlow<List<RemoteDevice>> = _others

    fun init(context: Context, api: VolnaApi, prefs: Prefs) {
        if (::api.isInitialized) return
        this.api = api
        this.prefs = prefs
        name = runCatching { Settings.Global.getString(context.contentResolver, Settings.Global.DEVICE_NAME) }.getOrNull()
            ?.takeIf { it.isNotBlank() } ?: Build.MODEL
        scope.launch {
            while (true) {
                val playing = VolnaPlayer.state.value.playing
                if (playing || visible) report()
                delay(if (playing) 5_000 else 10_000)
            }
        }
        scope.launch { // сменился трек, пауза или play — сразу, не дожидаясь очередного отчёта
            VolnaPlayer.state.map { it.track?.id to it.playing }.distinctUntilChanged().collect { report() }
        }
    }

    /** Открыли приложение — сразу узнать, не играет ли что-то на другом устройстве. */
    fun refresh() {
        scope.launch { report() }
    }

    /** Забрать музыку с другого устройства сюда: сначала узнаём, что там играет прямо сейчас. */
    fun takeOver(device: RemoteDevice) {
        scope.launch {
            val fresh = runCatching { parse(api.devices(prefs.deviceId)).firstOrNull { it.id == device.id } }.getOrNull() ?: device
            val track = fresh.track ?: return@launch
            VolnaPlayer.handoff(listOf(track) + fresh.next, fresh.positionMs, fresh.wave, fresh.name)
            _others.value = _others.value.map { if (it.id == device.id) it.copy(playing = false) else it }
            runCatching { api.pauseDevice(device.id, name) }
        }
    }

    private suspend fun report() {
        if (reporting || !Offline.isOnline || !prefs.connected) return
        reporting = true
        try {
            val s = VolnaPlayer.state.value
            val body = JSONObject()
                .put("device_id", prefs.deviceId)
                .put("name", name)
                .put("kind", "android")
                .put("track_id", s.track?.id.orEmpty())
                .put("position", s.positionMs / 1000.0)
                .put("duration", s.durationMs / 1000.0)
                .put("playing", s.playing)
                .put("mode", if (s.mode == "wave") "wave" else if (s.mode == "list") "list" else "")
                .put("source", s.source)
                .put("next_ids", JSONArray((s.queue + s.upNext).take(30).map { it.id }))
            val answer = api.reportDevice(body)
            val commands = answer.optJSONArray("commands") ?: JSONArray()
            for (i in 0 until commands.length()) {
                val command = commands.optJSONObject(i) ?: continue
                if (command.str("action") == "pause" && VolnaPlayer.exo.isPlaying) {
                    VolnaPlayer.exo.pause()
                    VolnaPlayer.say("Музыка теперь играет на «${command.str("by").ifEmpty { "другом устройстве" }}»")
                }
            }
            _others.value = parse(answer)
        } catch (_: IOException) {
            // старый сервис без устройств или нет связи — просто не показываем
        } finally {
            reporting = false
        }
    }
}

private fun parse(answer: JSONObject): List<RemoteDevice> {
    val devices = answer.optJSONArray("devices") ?: JSONArray()
    return (0 until devices.length()).mapNotNull { devices.optJSONObject(it) }.map { d ->
        RemoteDevice(
            id = d.str("device_id"),
            name = d.str("name"),
            playing = d.optBoolean("playing"),
            positionMs = (d.optDouble("position", 0.0) * 1000).toLong(),
            wave = d.str("mode") == "wave",
            track = d.optJSONObject("track")?.let(Track::from),
            next = Track.list(d.optJSONArray("next")),
        )
    }
}

/** Свой постоянный номер устройства: по нему сервис отличает телефон от браузера. */
val Prefs.deviceId: String
    get() = device.ifEmpty { UUID.randomUUID().toString().also { device = it } }
