package app.volna.data

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.withContext
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import java.util.concurrent.TimeUnit
import javax.net.ssl.SSLException

class ApiException(val code: Int, message: String) : IOException(message)

/** HTTP API сервиса Volna: тот же, что у веб-версии. */
class VolnaApi(val prefs: Prefs) {
    /** Нужно войти: входа ещё не было или сервис перестал принимать токен. */
    val needLogin = MutableStateFlow(!prefs.connected)

    /** Сервис отвечает. false — последний запрос не дошёл до сервера: нет сети или сервер недоступен. */
    val reachable = MutableStateFlow(true)

    /** Один клиент на всё: запросы, звук и обложки уходят в сервис с токеном входа. */
    val client: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(8, TimeUnit.SECONDS)
        .readTimeout(90, TimeUnit.SECONDS) // первая сборка профиля на большой библиотеке бывает долгой
        .addInterceptor { chain ->
            val request = chain.request()
            val ours = request.url.toString().startsWith(base) // токен не уходит на чужие адреса
            val token = prefs.token
            val response = try {
                chain.proceed(
                    if (ours && token.isNotEmpty()) request.newBuilder().header("Authorization", "Bearer $token").build() else request,
                )
            } catch (e: IOException) {
                if (ours && unreachable(e)) reachable.value = false
                throw e
            }
            if (ours) reachable.value = true
            if (ours && response.code == 401 && "/api/auth/" !in request.url.encodedPath) needLogin.value = true
            response
        }
        .build()

    val base: String get() = prefs.serviceUrl

    /** Ответы API содержат пути вида /api/stream/…: дополняем адресом сервиса. */
    fun url(path: String): String = if (path.isEmpty() || path.startsWith("http")) path else base + path

    private suspend fun call(method: String, path: String, body: JSONObject? = null): String =
        withContext(Dispatchers.IO) { send(method, base + path, body) }

    private fun send(method: String, address: String, body: JSONObject? = null): String {
        val url = address.toHttpUrlOrNull() ?: throw ApiException(0, "Неверный адрес сервиса: $address")
        val payload = if (method == "GET") null else (body ?: JSONObject()).toString().toRequestBody(JSON)
        val request = Request.Builder().url(url).method(method, payload).build()
        return client.newCall(request).execute().use { response ->
            val text = response.body.string()
            if (!response.isSuccessful) {
                // detail — строка от сервиса или список ошибок проверки данных (422) от FastAPI
                val detail = when (val d = runCatching { JSONObject(text).opt("detail") }.getOrNull()) {
                    is String -> d
                    is JSONArray -> d.optJSONObject(0)?.optString("msg")
                    else -> null
                }
                throw ApiException(response.code, detail?.takeIf { it.isNotBlank() } ?: "Ошибка ${response.code}")
            }
            text
        }
    }

    private suspend fun obj(method: String, path: String, body: JSONObject? = null) = JSONObject(call(method, path, body))
    private suspend fun get(path: String) = obj("GET", path)
    private suspend fun post(path: String, body: JSONObject = JSONObject()) = obj("POST", path, body)
    private suspend fun put(path: String, body: JSONObject) = obj("PUT", path, body)
    private fun q(value: String) = java.net.URLEncoder.encode(value, "UTF-8")

    // --- вход ---

    /**
     * Найти сервис по адресу, который ввёл человек, и войти. Обычно это адрес Navidrome:
     * сервис живёт рядом по пути /volna. Для сервиса на своём компьютере подходит IP с портом.
     */
    suspend fun connect(address: String, user: String, password: String): Unit = withContext(Dispatchers.IO) {
        var failure: Exception? = null
        for (candidate in candidates(address)) {
            val health = try {
                probe(candidate)
            } catch (e: IOException) {
                failure = failure ?: e
                null
            } ?: continue
            var token = ""
            if (health.optBoolean("auth")) {
                val body = JSONObject().put("user", user.trim()).put("password", password).put("device", "android")
                token = JSONObject(send("POST", candidate + "/api/auth/login", body)).optString("token")
            }
            prefs.serviceUrl = candidate
            prefs.address = address
            prefs.user = user.trim()
            prefs.token = token
            prefs.connected = true
            needLogin.value = false
            return@withContext
        }
        throw ApiException(
            0,
            when (failure) {
                is UnknownHostException -> "Сервер не найден: проверьте адрес"
                is SocketTimeoutException -> "Сервер не отвечает"
                is SSLException -> "Не удалось установить защищённое соединение"
                null -> "По этому адресу нет сервиса VaginaWave"
                else -> "Не удалось подключиться: ${failure?.message}"
            },
        )
    }

    suspend fun logout() {
        runCatching { post("/api/auth/logout") }
        prefs.token = ""
        prefs.connected = false
        needLogin.value = true
    }

    /** Сервис Volna отвечает на /api/health; Navidrome и прочие сайты по этому адресу — нет. */
    private fun probe(base: String): JSONObject? {
        val url = (base + "/api/health").toHttpUrlOrNull() ?: return null
        val quick = client.newBuilder().connectTimeout(5, TimeUnit.SECONDS).readTimeout(10, TimeUnit.SECONDS).build()
        quick.newCall(Request.Builder().url(url).build()).execute().use { response ->
            if (!response.isSuccessful) return null
            val json = runCatching { JSONObject(response.body.string()) }.getOrNull() ?: return null
            return json.takeIf { it.optString("service") == "volna" || it.has("mode") }
        }
    }

    // --- служебное ---

    suspend fun health(): JSONObject = get("/api/health")
    suspend fun server(): ServerInfo = ServerInfo.from(get("/api/server"))
    suspend fun testServer(form: JSONObject): JSONObject = post("/api/server/test", form)
    suspend fun saveServer(form: JSONObject): JSONObject = put("/api/server", form)
    suspend fun syncLibrary(): JSONObject = post("/api/library/sync")
    suspend fun taste(): JSONObject = get("/api/taste")

    suspend fun settings(): JSONObject = get("/api/settings").getJSONObject("settings")
    suspend fun putSettings(patch: JSONObject): JSONObject = put("/api/settings", patch).getJSONObject("settings")
    suspend fun preferences(): JSONObject = get("/api/preferences")
    suspend fun putPreferences(patch: JSONObject): JSONObject = put("/api/preferences", patch)

    // --- волна ---

    suspend fun startWave(seedType: String? = null, seedId: String? = null): WaveState {
        val body = JSONObject().put("device", "android")
        if (seedType != null && seedId != null) body.put("seed", JSONObject().put("type", seedType).put("id", seedId))
        return WaveState.from(post("/api/wave/start", body))
    }

    suspend fun advance(session: String, trackId: String, outcome: String, position: Double, duration: Double) =
        WaveState.from(
            post(
                "/api/wave/$session/advance",
                JSONObject().put("track_id", trackId).put("outcome", outcome)
                    .put("position", position).put("duration", duration),
            ),
        )

    suspend fun likeInWave(session: String, trackId: String, liked: Boolean) =
        WaveState.from(post("/api/wave/$session/like", JSONObject().put("track_id", trackId).put("liked", liked)))

    suspend fun waveSettings(session: String, patch: JSONObject) = WaveState.from(post("/api/wave/$session/settings", patch))
    suspend fun shake(session: String) = WaveState.from(post("/api/wave/$session/shake"))

    suspend fun continueList(trackIds: List<String>, title: String) = WaveState.from(
        post("/api/continue", JSONObject().put("track_ids", JSONArray(trackIds)).put("title", title).put("device", "android")),
    )

    // --- сигналы вне волны ---

    /** at — когда слушали (секунды Unix), если событие копилось без сети. */
    suspend fun event(
        trackId: String, outcome: String, position: Double, duration: Double, contextType: String, contextId: String, at: Double? = null,
    ) {
        val body = JSONObject().put("track_id", trackId).put("outcome", outcome).put("position", position)
            .put("duration", duration).put("context_type", contextType).put("context_id", contextId)
            .put("device", "android")
        if (at != null) body.put("at", at)
        post("/api/events", body)
    }

    suspend fun like(type: String, id: String, liked: Boolean) {
        post("/api/like", JSONObject().put("type", type).put("id", id).put("liked", liked))
    }

    // --- библиотека ---

    suspend fun home(): List<Shelf> {
        val shelves = get("/api/home").optJSONArray("shelves") ?: JSONArray()
        return (0 until shelves.length()).map { Shelf.from(shelves.getJSONObject(it)) }
    }

    suspend fun libraryStats(): JSONObject = get("/api/library")
    suspend fun albums(sort: String) = Item.list(get("/api/library/albums?sort=$sort&limit=5000").optJSONArray("items"))
    suspend fun artists(sort: String) = Item.list(get("/api/library/artists?sort=$sort&limit=5000").optJSONArray("items"))
    suspend fun playlists() = Item.list(get("/api/library/playlists").optJSONArray("items"))
    suspend fun liked() = Track.list(get("/api/library/liked").optJSONArray("tracks"))
    suspend fun recent() = Track.list(get("/api/library/recent").optJSONArray("tracks"))
    /** Запас на случай без сети: что вы часто и недавно слушаете. */
    suspend fun reserve(limit: Int) = Track.list(get("/api/library/reserve?limit=$limit").optJSONArray("tracks"))

    /** Адрес для загрузки трека: с качеством из настроек загрузок, а не потока. */
    fun downloadUrl(track: Track, quality: String): String = url(track.stream) + "?quality=" + q(quality)
    suspend fun page(type: String, id: String) = Page.from(get("/api/$type/${q(id)}"))
    suspend fun search(query: String) = SearchResult.from(get("/api/search?q=${q(query)}"))
    suspend fun lyrics(trackId: String) = Lyrics.from(lyricsJson(trackId))
    suspend fun lyricsJson(trackId: String): JSONObject = get("/api/lyrics/${q(trackId)}")
    suspend fun imports() = Imports.from(get("/api/imports"))
    suspend fun likeTracks(trackIds: List<String>, liked: Boolean = true): JSONObject =
        post("/api/like/tracks", JSONObject().put("track_ids", JSONArray(trackIds)).put("liked", liked))
    suspend fun discography(artistId: String) = Discography.from(get("/api/lidarr/artist/${q(artistId)}"))
    suspend fun downloadDiscography(artistId: String) = Discography.from(post("/api/lidarr/artist/${q(artistId)}"))
    suspend fun lidarrAvailable() = get("/api/lidarr").optBoolean("available")
    suspend fun findArtists(query: String) = FoundArtist.list(get("/api/lidarr/search?q=${q(query)}").optJSONArray("artists"))
    suspend fun followArtist(mbid: String): JSONObject = post("/api/lidarr/follow", JSONObject().put("mbid", mbid))
    suspend fun onboarding() = OnboardingData.from(get("/api/onboarding"))
    suspend fun similarArtists(artistId: String) = Item.list(get("/api/onboarding/similar/${q(artistId)}").optJSONArray("artists"))
    suspend fun finishOnboarding(artistIds: List<String>, settings: JSONObject): JSONObject =
        post("/api/onboarding", JSONObject().put("artists", JSONArray(artistIds)).put("settings", settings))
    suspend fun startImport(url: String) = ImportItem.from(post("/api/imports", JSONObject().put("url", url)))

    suspend fun mix(type: String, id: String, size: Int) = Mix.from(
        post("/api/mix", JSONObject().put("seed", JSONObject().put("type", type).put("id", id)).put("size", size)),
    )

    // --- другие устройства ---

    suspend fun reportDevice(state: JSONObject): JSONObject = put("/api/devices/me", state)
    suspend fun devices(deviceId: String): JSONObject = get("/api/devices?device_id=${q(deviceId)}")
    suspend fun pauseDevice(deviceId: String, by: String): JSONObject = post("/api/devices/${q(deviceId)}/pause?by=${q(by)}")

    suspend fun createPlaylist(name: String, trackIds: List<String>): JSONObject =
        post("/api/playlists", JSONObject().put("name", name).put("track_ids", JSONArray(trackIds)))

    // --- ссылки на плейлисты ---

    suspend fun createShare(playlistId: String) = ShareLink.from(post("/api/playlist/${q(playlistId)}/share"))
    suspend fun setShareGuests(playlistId: String, on: Boolean) =
        ShareLink.from(put("/api/playlist/${q(playlistId)}/share", JSONObject().put("guests", on)))
    suspend fun revokeShare(playlistId: String) { obj("DELETE", "/api/playlist/${q(playlistId)}/share") }
    suspend fun shared(token: String) = SharedPlaylist.from(get("/api/share/${q(token)}"))
    suspend fun saveShared(token: String): String = post("/api/share/${q(token)}/save").getString("id")

    companion object {
        private val JSON = "application/json".toMediaType()

        /** Запрос не дошёл до сервера: нет сети, сервер выключен или не отвечает. Отменённые запросы не считаются. */
        fun unreachable(e: IOException): Boolean = e is UnknownHostException || e is java.net.ConnectException ||
            e is java.net.NoRouteToHostException || (e is SocketTimeoutException && e.message.orEmpty().contains("connect"))

        /** Где искать сервис: рядом с Navidrome по пути /volna, в корне или на своём компьютере. */
        fun candidates(address: String): List<String> {
            val a = address.trim().trimEnd('/')
            if (a.isEmpty()) return emptyList()
            if ("://" in a) return listOf(a, "$a/volna")
            val hostPort = a.substringBefore('/')
            val host = hostPort.substringBefore(':')
            val local = host == "localhost" || host.endsWith(".local") || host.all { it.isDigit() || it == '.' }
            return if (local) {
                listOfNotNull("http://$a", if (':' !in hostPort) "http://$a:8765" else null, "https://$a/volna")
            } else {
                listOf("https://$a/volna", "https://$a")
            }
        }
    }
}
