package app.volna.player

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import app.volna.data.ApiException
import app.volna.data.Lyrics
import app.volna.data.Page
import app.volna.data.Prefs
import app.volna.data.SearchResult
import app.volna.data.Shelf
import app.volna.data.Track
import app.volna.data.VolnaApi
import app.volna.data.str
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.MainScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.IOException

/**
 * Работа без сети. online — сеть есть, сервис отвечает и офлайн-режим не включён вручную.
 *
 * Без сети волна играет скачанное, лайки и прослушивания копятся здесь и уходят на сервер,
 * когда связь вернётся (со временем, когда слушали), а «Мне нравится», страницы альбомов и
 * плейлистов, поиск и тексты песен берутся из загрузок. Со связью раз в 6 часов сверяем с
 * сервисом, что держать скачанным: «Мне нравится», запас и скачанные альбомы и плейлисты.
 */
object Offline {
    const val NO_NETWORK = "Нет связи с сервером: доступно только скачанное"
    const val NOT_DOWNLOADED = "Трек не скачан: без сети он не сыграет"
    private const val SYNC_EVERY = 6 * 3600_000L
    private const val OUTBOX_LIMIT = 3000

    private val scope = MainScope()
    private lateinit var api: VolnaApi
    private lateinit var prefs: Prefs
    private lateinit var outboxFile: File
    private val outbox = mutableListOf<JSONObject>()
    private var flushing = false
    private var syncing = false

    private val network = MutableStateFlow(true)
    private val unmetered = MutableStateFlow(true) // Wi‑Fi или другая сеть без оплаты за трафик
    private val _forced = MutableStateFlow(false)
    val forced: StateFlow<Boolean> = _forced
    private val _online = MutableStateFlow(true)
    val online: StateFlow<Boolean> = _online
    val isOnline: Boolean get() = _online.value

    fun init(context: Context, api: VolnaApi, prefs: Prefs) {
        if (::api.isInitialized) return
        this.api = api
        this.prefs = prefs
        _forced.value = prefs.forceOffline
        outboxFile = File(context.filesDir, "outbox.json")
        readOutbox()

        val connectivity = context.getSystemService(ConnectivityManager::class.java)
        val active = connectivity.activeNetwork
        network.value = active != null
        unmetered.value = connectivity.getNetworkCapabilities(active)?.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_METERED) == true
        connectivity.registerDefaultNetworkCallback(object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(n: Network) {
                scope.launch {
                    network.value = true
                    api.reachable.value = true // сеть вернулась — пробуем сервис снова
                }
            }

            override fun onCapabilitiesChanged(n: Network, caps: NetworkCapabilities) {
                val free = caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_METERED)
                scope.launch { unmetered.value = free }
            }

            override fun onLost(n: Network) {
                scope.launch { network.value = false }
            }
        })
        Downloads.network(isOnlineNow(), unmetered.value)
        scope.launch {
            combine(network, api.reachable, _forced) { net, reachable, forced -> net && reachable && !forced }
                .distinctUntilChanged()
                .collect { on ->
                    _online.value = on
                    if (on) {
                        flush()
                        sync()
                    }
                }
        }
        scope.launch { // загрузки идут, пока сервис доступен, а с «только по Wi‑Fi» — пока сеть бесплатная
            combine(_online, unmetered) { on, free -> on to free }.distinctUntilChanged().collect { (on, free) -> Downloads.network(on, free) }
        }
        scope.launch { // сеть есть, а сервис не отвечал: проверяем раз в полминуты
            while (true) {
                delay(30_000)
                if (network.value && !api.reachable.value && !_forced.value && prefs.connected) runCatching { api.health() }
            }
        }
    }

    private fun isOnlineNow() = network.value && api.reachable.value && !_forced.value

    // --- настройки ---

    val likedDownloads: Boolean get() = prefs.offlineLiked

    fun setForced(on: Boolean) {
        prefs.forceOffline = on
        _forced.value = on
        if (!on) api.reachable.value = true // вышли из офлайна — пробуем сервис снова, не дожидаясь проверки
        _online.value = isOnlineNow() // сразу: следом может стартовать волна, ей надо знать, есть ли связь
    }

    /** Почему волна перешла на скачанное — для подсказки. */
    val switchNote: String get() = if (_forced.value) "Офлайн-режим — играю скачанное" else "Нет связи — играю скачанное"

    /** Стоит пробовать сервис: сеть есть и офлайн не включён вручную, даже если прошлый запрос не дошёл. */
    val canTry: Boolean get() = network.value && !_forced.value

    /** Включить или выключить загрузку «Мне нравится»; tracks — уже известный список лайков. */
    fun setLikedDownloads(on: Boolean, tracks: List<Track>? = null) {
        prefs.offlineLiked = on
        if (!on) return Downloads.setReason(Downloads.LIKED, emptyList())
        if (tracks != null) Downloads.setReason(Downloads.LIKED, tracks) else sync(force = true)
    }

    fun setReserve(size: Int) {
        prefs.reserve = size
        if (size == 0) Downloads.setReason(Downloads.RESERVE, emptyList()) else sync(force = true)
    }

    /** Сверить с сервисом, что держать скачанным. Без force — не чаще раза в 6 часов. */
    fun sync(force: Boolean = false) {
        if (!isOnline || syncing || !prefs.connected) return
        if (!force && System.currentTimeMillis() - prefs.offlineSynced < SYNC_EVERY) return
        syncing = true
        scope.launch {
            try {
                Downloads.setReason(Downloads.LIKED, if (prefs.offlineLiked) api.liked() else emptyList())
                val reserve = if (prefs.reserve > 0) {
                    try {
                        api.reserve(prefs.reserve)
                    } catch (e: ApiException) {
                        if (e.code == 404) emptyList() else throw e // старый сервис без запаса
                    }
                } else {
                    emptyList()
                }
                Downloads.setReason(Downloads.RESERVE, reserve)
                for (list in Downloads.lists.value.values) { // плейлисты меняются — докачиваем новое
                    try {
                        val page = api.page(list.item.type, list.item.id)
                        Downloads.saveList(page.item, page.tracks)
                    } catch (e: ApiException) {
                        if (e.code == 404) Downloads.removeList(list.item.type, list.item.id)
                    }
                }
                prefs.offlineSynced = System.currentTimeMillis()
            } catch (_: IOException) {
                // попробуем, когда сеть вернётся
            } finally {
                syncing = false
            }
        }
    }

    // --- лайки и прослушивания без сети ---

    fun event(trackId: String, outcome: String, position: Double, duration: Double, contextType: String, contextId: String) {
        push(
            JSONObject().put("kind", "event").put("track_id", trackId).put("outcome", outcome).put("position", position)
                .put("duration", duration).put("context_type", contextType).put("context_id", contextId)
                .put("at", System.currentTimeMillis() / 1000.0),
        )
    }

    fun like(type: String, id: String, liked: Boolean) {
        outbox.removeAll { it.optString("kind") == "like" && it.str("type") == type && it.str("id") == id } // важна последняя отметка
        push(JSONObject().put("kind", "like").put("type", type).put("id", id).put("liked", liked))
    }

    private fun push(entry: JSONObject) {
        outbox += entry
        while (outbox.size > OUTBOX_LIMIT) outbox.removeAt(0)
        writeOutbox()
        if (isOnline) flush()
    }

    private fun flush() {
        if (flushing || outbox.isEmpty()) return
        flushing = true
        scope.launch {
            try {
                while (outbox.isNotEmpty() && isOnline) {
                    val e = outbox.first()
                    try {
                        when (e.optString("kind")) {
                            "event" -> api.event(
                                e.str("track_id"), e.str("outcome"), e.optDouble("position"), e.optDouble("duration"),
                                e.str("context_type"), e.str("context_id"), e.optDouble("at"),
                            )
                            "like" -> api.like(e.str("type"), e.str("id"), e.optBoolean("liked"))
                        }
                    } catch (x: ApiException) {
                        if (x.code == 401 || x.code >= 500) break // войти заново или сервер занят — позже
                        // остальное (трек удалили из библиотеки) не повторяем
                    }
                    outbox.removeAt(0)
                }
            } catch (_: IOException) {
                // связь снова пропала
            } finally {
                flushing = false
                writeOutbox()
            }
        }
    }

    private fun readOutbox() {
        val a = runCatching { JSONArray(outboxFile.readText()) }.getOrNull() ?: return
        for (i in 0 until a.length()) a.optJSONObject(i)?.let { outbox += it }
    }

    private fun writeOutbox() {
        val text = JSONArray(outbox).toString()
        scope.launch(Dispatchers.IO) { runCatching { outboxFile.writeText(text) } }
    }

    /** Выход из аккаунта: чужие лайки и прослушивания новому пользователю не отправляем. */
    fun forget() {
        outbox.clear()
        writeOutbox()
        prefs.offlineSynced = 0
    }

    // --- данные для экранов: с сервиса, а без сети — из загрузок ---

    private suspend fun <T> orLocal(remote: suspend () -> T, local: () -> T?): T {
        if (!isOnline) return local() ?: throw IOException(NO_NETWORK)
        return try {
            remote()
        } catch (e: ApiException) {
            throw e
        } catch (e: IOException) {
            local() ?: throw e
        }
    }

    suspend fun home(): List<Shelf> = if (isOnline) api.home() else throw IOException(NO_NETWORK)

    suspend fun liked(): List<Track> = orLocal({ api.liked() }) { Downloads.likedTracks() }

    suspend fun page(type: String, id: String): Page = orLocal({ api.page(type, id) }) { Downloads.page(type, id) }

    suspend fun search(query: String): SearchResult = orLocal({ api.search(query) }) { Downloads.search(query) }

    suspend fun lyrics(trackId: String): Lyrics = orLocal(
        { api.lyricsJson(trackId).also { Downloads.keepLyrics(trackId, it) } },
        { Downloads.lyrics(trackId) },
    ).let { withContext(Dispatchers.Default) { Lyrics.from(it) } }
}
