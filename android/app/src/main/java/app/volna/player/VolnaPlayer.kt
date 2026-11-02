package app.volna.player

import android.content.Context
import android.net.Uri
import androidx.media3.common.AudioAttributes
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.datasource.cache.CacheDataSource
import androidx.media3.datasource.okhttp.OkHttpDataSource
import androidx.media3.exoplayer.DefaultRenderersFactory
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.audio.AudioSink
import androidx.media3.exoplayer.audio.DefaultAudioSink
import androidx.media3.exoplayer.audio.TeeAudioProcessor
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import app.volna.data.ApiException
import app.volna.data.Track
import app.volna.data.VolnaApi
import app.volna.data.WaveState
import kotlinx.coroutines.MainScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import org.json.JSONObject
import java.io.IOException

/** Откуда играет список: альбом, плейлист, поиск… (для событий и подписи «почему этот трек»). */
data class PlayContext(val type: String, val id: String = "", val title: String = "")

data class PlayerState(
    val track: Track? = null,
    val nextUp: Track? = null,
    val mode: String? = null, // wave | list
    val kind: String = "wave", // wave | steer
    val title: String = "", // источник смещения волны: трек, альбом, плейлист
    val reason: String = "",
    val playing: Boolean = false,
    val loading: Boolean = false,
    val positionMs: Long = 0,
    val durationMs: Long = 0,
    val settings: JSONObject? = null,
    val message: String? = null,
    val queue: List<Track> = emptyList(), // добавлено вручную: играет раньше волны и списка
    val fromQueue: Boolean = false, // текущий трек — из очереди
    val upNext: List<Track> = emptyList(), // что будет после очереди: волна или остаток списка
    val source: String = "", // откуда upNext: «Моя волна», название альбома…
    val offline: Boolean = false, // играет волна из скачанного: связи нет
)

/**
 * Плеер приложения. Два режима, как в веб-версии:
 * * wave — очередь держит сервис Volna: на каждое окончание трека сообщаем, чем всё кончилось
 *   (дослушал, пропустил на N-й секунде, «не нравится»), и получаем следующий;
 * * list — альбом, плейлист, поиск. Прослушивания всё равно уходят в /api/events, а когда список
 *   закончился, сервис продолжает его волной, смещённой ко всему списку.
 *
 * В ExoPlayer всегда лежат текущий трек и следующий, чтобы переход был без паузы и работал
 * в фоне с выключенным экраном. «Назад» в волне ведёт по уже сыгранным трекам (history), а
 * «дальше» потом возвращает обратно (forward), не трогая сервер.
 *
 * Очередь, как в Spotify: добавленные вручную треки играют раньше волны и списка. Уходя
 * с трека волны в очередь, сообщаем серверу, чем он кончился, а его следующий трек
 * откладываем (resume) — с него волна и продолжится, когда очередь закончится.
 *
 * Без сети волна играет скачанное (список OFFLINE), списки — только скачанные треки, а
 * прослушивания и лайки копятся в Offline и уходят на сервер, когда связь вернётся.
 */
object VolnaPlayer {
    private val scope = MainScope()
    private val _state = MutableStateFlow(PlayerState())
    val state: StateFlow<PlayerState> = _state

    lateinit var api: VolnaApi
        private set
    lateinit var exo: ExoPlayer
        private set

    private var mode: String? = null
    private var session: String? = null
    private var kind = "wave"
    private var title = ""
    private var settings: JSONObject? = null
    private var upcoming: List<Track> = emptyList()
    private var list: List<Track> = emptyList()
    private var index = 0
    private var context: PlayContext? = null
    private val history = ArrayDeque<Track>()
    private val forward = ArrayDeque<Track>()
    private var current: Track? = null
    private val queue = mutableListOf<Track>()
    private var fromQueue = false
    private var resume: Track? = null // с чего продолжить волну или список после очереди
    private var resumeEnd = false // список кончился, пока играла очередь: дальше — продолжение волной
    private var busy = false
    private var handoffWave = false // музыку забрали с устройства, где играла волна: после её треков — волна
    private var loading = false
    private var message: String? = null

    private val replaying get() = mode == "wave" && forward.isNotEmpty()

    fun init(context: Context, api: VolnaApi) {
        if (::exo.isInitialized) return
        this.api = api
        // звук проходит через «тройник»: ExoPlayer играет его как обычно, а AudioLevel меряет громкость для фона
        val renderers = object : DefaultRenderersFactory(context) {
            override fun buildAudioSink(context: Context, enableFloatOutput: Boolean, enableAudioTrackPlaybackParams: Boolean): AudioSink =
                AudioLevel.Sink(DefaultAudioSink.Builder(context).setAudioProcessors(arrayOf(TeeAudioProcessor(AudioLevel))).build())
        }
        // скачанное играет из кэша загрузок, остальное — потоком с сервиса (тоже с токеном);
        // сам плеер в кэш не пишет, это делает только загрузчик
        val sources = CacheDataSource.Factory()
            .setCache(Downloads.cache)
            .setUpstreamDataSourceFactory(OkHttpDataSource.Factory(api.client))
            .setCacheWriteDataSinkFactory(null)
            .setFlags(CacheDataSource.FLAG_IGNORE_CACHE_ON_ERROR)
        exo = ExoPlayer.Builder(context, renderers)
            .setMediaSourceFactory(DefaultMediaSourceFactory(sources))
            .setAudioAttributes(
                AudioAttributes.Builder().setUsage(C.USAGE_MEDIA).setContentType(C.AUDIO_CONTENT_TYPE_MUSIC).build(),
                true, // аудиофокус: звонок и навигатор ставят на паузу
            )
            .setHandleAudioBecomingNoisy(true) // выдернули наушники — пауза
            .build()
        exo.addListener(object : Player.Listener {
            override fun onIsPlayingChanged(isPlaying: Boolean) = publish()

            override fun onPlaybackStateChanged(playbackState: Int) {
                if (playbackState == Player.STATE_ENDED) next("complete")
                publish()
            }

            override fun onMediaItemTransition(mediaItem: MediaItem?, reason: Int) {
                if (reason == Player.MEDIA_ITEM_TRANSITION_REASON_AUTO) onAutoAdvance(mediaItem)
            }

            override fun onPlayerError(error: PlaybackException) {
                val network = error.errorCode == PlaybackException.ERROR_CODE_IO_NETWORK_CONNECTION_FAILED ||
                    error.errorCode == PlaybackException.ERROR_CODE_IO_NETWORK_CONNECTION_TIMEOUT
                if ((network || !Offline.isOnline) && this@VolnaPlayer.context != OFFLINE && Downloads.hasAny()) {
                    startOffline(Offline.switchNote)
                    return
                }
                say("Трек не воспроизводится, беру следующий")
                next("error")
            }
        })
        scope.launch {
            while (true) {
                if (exo.isPlaying) publish()
                delay(500)
            }
        }
    }

    // --- запуск ---

    /** Вышли из аккаунта: останавливаем музыку и забываем очередь. */
    fun reset() {
        exo.stop()
        exo.clearMediaItems()
        mode = null
        session = null
        upcoming = emptyList()
        list = emptyList()
        index = 0
        context = null
        history.clear()
        forward.clear()
        current = null
        settings = null
        queue.clear()
        leaveQueue()
        _state.value = PlayerState()
    }

    fun startWave(seedType: String? = null, seedId: String? = null) {
        // волну просят явно — пробуем сервис, если сеть есть; не ответит — сыграем скачанное
        if (!Offline.canTry) return startOffline(if (seedType != null) "Нет связи: играю скачанное" else null)
        handoffWave = false
        history.clear()
        forward.clear()
        leaveQueue() // новая волна начинается сразу; добавленное в очередь сыграет после её первого трека
        loading = true
        publish()
        scope.launch {
            try {
                applyWave(api.startWave(seedType, seedId))
            } catch (e: ApiException) {
                say(problem(e))
            } catch (e: IOException) {
                if (Downloads.hasAny()) startOffline(Offline.switchNote) else say(problem(e))
            } finally {
                loading = false
                publish()
            }
        }
    }

    fun playList(tracks: List<Track>, start: Int, context: PlayContext) {
        if (tracks.isEmpty()) return
        var from = start.coerceIn(0, tracks.lastIndex)
        var playable = tracks
        if (!Offline.isOnline) { // без сети — только скачанное, начиная с выбранного или следующего за ним
            playable = tracks.filter { Downloads.isDone(it.id) }
            if (playable.isEmpty()) return say(Offline.NOT_DOWNLOADED)
            val after = tracks.drop(from).firstOrNull { Downloads.isDone(it.id) }
            from = playable.indexOf(after).coerceAtLeast(0)
        }
        mode = "list"
        session = null
        handoffWave = false
        history.clear()
        forward.clear()
        list = playable
        index = from
        this.context = context
        leaveQueue()
        load(list[index])
    }

    fun shuffle(tracks: List<Track>, context: PlayContext) =
        playList((if (Offline.isOnline) tracks else tracks.filter { Downloads.isDone(it.id) }).shuffled(), 0, context)

    /** Музыку забрали с другого устройства: тот же трек с того же места, дальше — его очередь. */
    fun handoff(tracks: List<Track>, positionMs: Long, wave: Boolean, from: String) {
        playList(tracks, 0, PlayContext("other", "handoff", from))
        if (current?.id == tracks.firstOrNull()?.id && positionMs > 0) exo.seekTo(positionMs)
        handoffWave = wave
    }

    /** Волна без сети: скачанное вперемешку, лайкнутое и запас — чаще. */
    fun startOffline(note: String? = null) {
        val tracks = Downloads.wave(history.map { it.id }.toSet() + listOfNotNull(current?.id))
        if (tracks.isEmpty()) {
            if (exo.isPlaying) exo.pause()
            say("Нет связи, а скачанных треков нет: скачайте музыку, пока сеть есть")
            return
        }
        playList(tracks, 0, OFFLINE)
        note?.let(::say)
    }

    /** Связь пропала посреди волны: скачанный текущий трек доигрывает, дальше — скачанное. */
    private fun goOffline(playing: Track?) {
        if (playing == null || !Downloads.isDone(playing.id)) return startOffline(Offline.switchNote)
        mode = "list"
        session = null
        forward.clear()
        context = OFFLINE
        list = listOf(playing) + Downloads.wave(history.map { it.id }.toSet()).filter { it.id != playing.id }
        index = 0
        current = playing
        refreshTail()
        publish()
        say(if (Offline.forced.value) "Офлайн-режим — дальше скачанное" else "Нет связи — дальше скачанное")
    }
    // --- переключение ---

    /** Трек закончился, пропущен, «не нравится» или не играет (error — без обучения). */
    fun next(outcome: String = "skip") {
        val track = current ?: return
        if (busy) return
        busy = true
        val position = exo.currentPosition / 1000.0
        val duration = durationSeconds(track)
        scope.launch {
            try {
                when {
                    replaying -> {
                        report(track, outcome, position, duration, PlayContext("other"))
                        remember(track)
                        load(forward.removeLast())
                    }
                    fromQueue -> {
                        report(track, outcome, position, duration, QUEUE)
                        remember(track)
                        afterQueued()
                    }
                    mode == "wave" && !Offline.isOnline -> {
                        report(track, outcome, position, duration, PlayContext("other"))
                        remember(track)
                        startOffline(Offline.switchNote)
                    }
                    mode == "wave" -> {
                        val data = api.advance(session ?: return@launch, track.id, outcome, position, duration)
                        remember(track)
                        if (queue.isNotEmpty()) {
                            stash(data)
                            playQueued()
                        } else {
                            applyWave(data)
                        }
                    }
                    else -> {
                        report(track, outcome, position, duration, context)
                        if (queue.isNotEmpty()) {
                            index += 1
                            resume = list.getOrNull(index)
                            resumeEnd = resume == null
                            playQueued()
                        } else if (index + 1 < list.size) {
                            index += 1
                            load(list[index])
                        } else {
                            continueAfterList()
                        }
                    }
                }
            } catch (e: ApiException) {
                if (e.code == 404 && mode == "wave") startWave() else say(problem(e))
            } catch (e: IOException) {
                if (mode == "wave" && !replaying && !fromQueue) { // волна не ответила: сервер узнает о треке позже
                    report(track, outcome, position, duration, PlayContext("other"))
                    remember(track)
                }
                if (Downloads.hasAny()) startOffline(Offline.switchNote) else say(problem(e))
            } finally {
                busy = false
            }
        }
    }

    /** «Назад»: в начало трека, если он играет дольше 3 секунд, иначе — к предыдущему. */
    /** «Назад»: после первых трёх секунд кнопка сначала перематывает трек в начало, свайп — нет. */
    fun previous(restartFirst: Boolean = true) {
        val track = current ?: return
        if (fromQueue || (restartFirst && exo.currentPosition > 3000)) { // трек из очереди — в начало
            exo.seekTo(0)
            return
        }
        if (mode == "list" && !replaying) {
            if (index == 0) {
                exo.seekTo(0)
                return
            }
            index -= 1
            load(list[index])
            return
        }
        val back = history.removeLastOrNull()
        if (back == null) {
            exo.seekTo(0)
            return
        }
        forward.addLast(track)
        load(back)
    }

    fun toggle() {
        when {
            current == null -> startWave()
            exo.isPlaying -> exo.pause()
            else -> exo.play()
        }
    }

    fun seek(fraction: Float) {
        val duration = exo.duration
        if (duration > 0) exo.seekTo((duration * fraction.coerceIn(0f, 1f)).toLong())
    }

    // --- реакции и настройки ---

    fun like() {
        val track = current ?: return
        val liked = !track.liked
        current = track.copy(liked = liked)
        Downloads.liked(listOf(track), liked)
        publish()
        if (!Offline.isOnline) return Offline.like("track", track.id, liked) // уйдёт на сервер, когда будет связь
        scope.launch {
            try {
                val session = session
                if (mode == "wave" && !replaying && !fromQueue && session != null) {
                    applyWave(api.likeInWave(session, track.id, liked))
                } else {
                    api.like("track", track.id, liked)
                }
            } catch (e: ApiException) {
                current = current?.copy(liked = !liked)
                Downloads.liked(listOf(track), !liked)
                publish()
                say(problem(e))
            } catch (e: IOException) {
                Offline.like("track", track.id, liked)
            }
        }
    }

    fun shake() {
        val session = session
        if (mode != "wave" || session == null) return startWave()
        scope.launch {
            try {
                applyWave(api.shake(session))
                say("Волна встряхнута: дальше будет иначе")
            } catch (e: IOException) {
                say(problem(e))
            }
        }
    }

    /** Поменять настройки волны: на ходу в текущей сессии или просто сохранить. */
    fun changeSettings(patch: JSONObject) {
        scope.launch {
            try {
                val session = session
                if (mode == "wave" && session != null) {
                    applyWave(api.waveSettings(session, patch))
                } else {
                    settings = api.putSettings(patch)
                    publish()
                }
            } catch (e: ApiException) {
                if (e.code == 404) {
                    settings = api.putSettings(patch)
                    publish()
                } else {
                    say(problem(e))
                }
            } catch (e: IOException) {
                say(problem(e))
            }
        }
    }

    fun loadSettings() {
        scope.launch {
            runCatching { api.settings() }.onSuccess {
                settings = it
                publish()
            }
        }
    }

    fun say(text: String) {
        message = text
        publish()
    }

    fun consumeMessage() {
        message = null
        publish()
    }

    // --- очередь ---

    /** В конец очереди; если ничего не играет — сразу. */
    fun enqueue(track: Track) {
        queue.add(track)
        if (current == null) {
            fromQueue = true
            load(queue.removeAt(0))
        } else {
            refreshTail()
        }
        say("«${track.title}» — в очереди")
    }

    /** Сразу после текущего трека. */
    fun playNext(track: Track) {
        if (current == null) return enqueue(track)
        queue.add(0, track)
        refreshTail()
        say("«${track.title}» сыграет следующим")
    }

    fun moveInQueue(from: Int, to: Int) {
        if (from !in queue.indices || to !in queue.indices || from == to) return
        queue.add(to, queue.removeAt(from))
        refreshTail()
        publish()
    }

    fun removeFromQueue(position: Int) {
        if (position !in queue.indices) return
        queue.removeAt(position)
        refreshTail()
        publish()
    }

    fun clearQueue() {
        queue.clear()
        refreshTail()
        publish()
    }

    /** Нажали на трек в очереди: всё, что было перед ним, пропускаем, и он играет сейчас. */
    fun playFromQueue(position: Int) {
        if (position !in queue.indices) return
        repeat(position) { queue.removeAt(0) }
        next("skip")
    }

    // --- внутреннее ---

    private fun playQueued() {
        fromQueue = true
        load(queue.removeAt(0))
    }

    /** Трек из очереди доиграл: следующий из очереди или туда, где остановились волна и список. */
    private suspend fun afterQueued() {
        val back = resume
        when {
            queue.isNotEmpty() -> playQueued()
            resumeEnd -> {
                leaveQueue()
                continueAfterList()
            }
            back != null -> {
                leaveQueue()
                load(back)
            }
            mode == "wave" -> {
                leaveQueue()
                startWave()
            }
            else -> {
                leaveQueue()
                load(null)
            }
        }
    }

    private fun leaveQueue() {
        fromQueue = false
        resume = null
        resumeEnd = false
    }

    /** Волна ушла на трек вперёд, но сначала доиграет очередь. */
    private fun stash(data: WaveState) {
        session = data.session
        kind = data.kind
        title = data.title
        upcoming = data.upcoming
        data.settings?.let { settings = it }
        resume = data.track
    }

    private suspend fun continueAfterList() {
        if (!Offline.isOnline) return startOffline(if (context == OFFLINE) null else "Список закончился — дальше скачанное")
        if (context == OFFLINE) return startWave() // связь вернулась: снова настоящая волна
        if (handoffWave) return startWave() // на том устройстве играла волна — она и продолжится
        val keepGoing = runCatching { api.preferences().optBoolean("continue_after_end", true) }.getOrDefault(true)
        if (!keepGoing) {
            exo.pause()
            exo.seekTo(0)
            return
        }
        val title = context?.title.orEmpty()
        loading = true
        publish()
        try {
            val data = api.continueList(list.map { it.id }, title)
            say(if (title.isNotEmpty()) "Список закончился — дальше похожее на «$title»" else "Список закончился — дальше похожее")
            applyWave(data)
        } catch (e: IOException) {
            say("Список закончился. ${problem(e)}")
            exo.pause()
        } finally {
            loading = false
            publish()
        }
    }

    private fun applyWave(data: WaveState) {
        mode = "wave"
        context = null // источник списка (альбом, скачанное) к волне не относится
        session = data.session
        kind = data.kind
        title = data.title
        upcoming = data.upcoming
        data.settings?.let { settings = it }
        if (fromQueue) { // играет очередь: волна обновилась (настройки, «встряхнуть») и подождёт
            resume = data.track
            refreshTail()
            publish()
            return
        }
        if (replaying) { // переслушиваем прошлое: очередь сервера обновилась, но играет сейчас не она
            refreshTail()
            publish()
            return
        }
        val track = data.track
        if (track != null && track.id == current?.id) { // тот же трек: лайк, настройки, «встряхнуть»
            current = track
            refreshTail()
            publish()
        } else {
            load(track)
        }
    }

    /** ExoPlayer сам перешёл на заранее подгруженный следующий трек. */
    private fun onAutoAdvance(item: MediaItem?) {
        val finished = current ?: return
        val duration = durationSeconds(finished)
        val intoQueue = !replaying && item != null && queue.firstOrNull()?.id == item.mediaId
        when {
            fromQueue -> {
                report(finished, "complete", duration, duration, QUEUE)
                remember(finished)
                if (intoQueue) {
                    current = queue.removeAt(0)
                } else {
                    current = resume
                    leaveQueue()
                }
                refreshTail()
                publish()
            }
            intoQueue && mode == "wave" -> {
                remember(finished)
                current = queue.removeAt(0)
                fromQueue = true
                refreshTail()
                publish()
                val session = session ?: return
                scope.launch { // волне — что её трек дослушан; её следующий подождёт очереди
                    try {
                        stash(api.advance(session, finished.id, "complete", duration, duration))
                        refreshTail()
                        publish()
                    } catch (e: IOException) {
                        say(problem(e))
                    }
                }
            }
            intoQueue -> {
                report(finished, "complete", duration, duration, context)
                index += 1
                resume = list.getOrNull(index)
                resumeEnd = resume == null
                current = queue.removeAt(0)
                fromQueue = true
                refreshTail()
                publish()
            }
            replaying -> {
                report(finished, "complete", duration, duration, PlayContext("other"))
                remember(finished)
                current = forward.removeLast()
                refreshTail()
                publish()
            }
            mode == "wave" && !Offline.isOnline -> {
                report(finished, "complete", duration, duration, PlayContext("other"))
                remember(finished)
                goOffline(upcoming.firstOrNull { it.id == item?.mediaId })
            }
            mode == "wave" -> {
                remember(finished)
                val session = session ?: return
                scope.launch {
                    try {
                        val data = api.advance(session, finished.id, "complete", duration, duration)
                        if (data.track?.id == item?.mediaId) { // сервер ведёт ту же очередь — уже играет нужный трек
                            this@VolnaPlayer.session = data.session
                            upcoming = data.upcoming
                            current = data.track
                            refreshTail()
                            publish()
                        } else {
                            applyWave(data)
                        }
                    } catch (e: ApiException) {
                        say(problem(e))
                    } catch (e: IOException) {
                        report(finished, "complete", duration, duration, PlayContext("other"))
                        goOffline(upcoming.firstOrNull { it.id == item?.mediaId })
                    }
                }
            }
            else -> {
                report(finished, "complete", duration, duration, context)
                index += 1
                current = list.getOrNull(index)
                refreshTail()
                publish()
            }
        }
    }

    private fun load(track: Track?) {
        current = track
        if (track == null) {
            exo.clearMediaItems()
            publish()
            return
        }
        exo.setMediaItems(listOfNotNull(track, nextUp()).map(::mediaItem), 0, 0L)
        exo.prepare()
        exo.play()
        publish()
    }

    /** Заменить заранее подгруженный следующий трек, если он поменялся. */
    private fun refreshTail() {
        val next = nextUp()
        val currentIndex = exo.currentMediaItemIndex
        val queued = if (exo.mediaItemCount > currentIndex + 1) exo.getMediaItemAt(currentIndex + 1).mediaId else null
        if (queued == next?.id) return
        if (exo.mediaItemCount > currentIndex + 1) exo.removeMediaItems(currentIndex + 1, exo.mediaItemCount)
        if (next != null) exo.addMediaItem(mediaItem(next))
    }

    private fun nextUp(): Track? = when {
        replaying -> forward.lastOrNull()
        queue.isNotEmpty() -> queue.first()
        fromQueue -> if (resumeEnd) null else resume
        mode == "wave" -> upcoming.firstOrNull()
        else -> list.getOrNull(index + 1)
    }

    /** Что заиграет, когда кончится очередь. */
    private fun upNext(): List<Track> = when {
        mode == "wave" -> listOfNotNull(resume.takeIf { fromQueue }) + upcoming
        fromQueue -> list.drop(index) // index уже указывает на трек, с которого список продолжится
        else -> list.drop(index + 1)
    }

    private fun source(): String = when {
        mode == "wave" && kind == "steer" && title.isNotEmpty() -> "Моя волна по «$title»"
        mode == "wave" -> "Моя волна"
        else -> context?.title.orEmpty()
    }

    private fun remember(track: Track) {
        history.addLast(track)
        if (history.size > 50) history.removeFirst()
    }

    private fun report(track: Track, outcome: String, position: Double, duration: Double, context: PlayContext?) {
        if (outcome == "error") return
        val type = context?.type ?: "other"
        val id = context?.id.orEmpty()
        if (!Offline.isOnline) return Offline.event(track.id, outcome, position, duration, type, id)
        scope.launch {
            try {
                api.event(track.id, outcome, position, duration, type, id)
            } catch (_: ApiException) {
                // сервис не принял — повторять незачем
            } catch (_: IOException) {
                Offline.event(track.id, outcome, position, duration, type, id)
            }
        }
    }

    private fun durationSeconds(track: Track): Double {
        val duration = exo.duration
        return if (duration > 0 && exo.currentMediaItem?.mediaId == track.id) duration / 1000.0 else track.duration
    }

    private fun mediaItem(track: Track): MediaItem {
        val item = MediaItem.Builder()
            .setMediaId(track.id)
            .setUri(api.url(track.stream))
        // скачан до конца — играет из загрузок; недокачанный не читаем, чтобы не смешать качество
        if (Downloads.isDone(track.id)) item.setCustomCacheKey(Downloads.key(track.id))
        val artwork = Downloads.cover(track.cover)?.let(Uri::fromFile) ?: track.cover.takeIf { it.isNotEmpty() }?.let { Uri.parse(api.url(it)) }
        return item.setMediaMetadata(
            MediaMetadata.Builder()
                .setTitle(track.title)
                .setArtist(track.artist)
                .setAlbumTitle(track.album)
                .setArtworkUri(artwork)
                .build(),
        ).build()
    }

    private fun reason(): String {
        val context = context
        if (mode == "list" && context == OFFLINE && !replaying && !fromQueue) return "Без сети · из скачанного"
        if (mode == "list" && context?.id == "handoff" && !replaying && !fromQueue) return "Продолжаю с «${context.title}»"
        if (mode == "list" && !replaying && context != null && context.title.isNotEmpty()) {
            val label = mapOf("album" to "Альбом", "playlist" to "Плейлист", "artist" to "Артист", "search" to "Поиск", "library" to "Коллекция")
            return "${label[context.type] ?: "Сейчас"}: ${context.title}"
        }
        return current?.reason.orEmpty()
    }

    private fun publish() {
        if (!::exo.isInitialized) return
        _state.value = PlayerState(
            track = current,
            nextUp = nextUp(),
            mode = mode,
            kind = kind,
            title = title,
            reason = reason(),
            playing = exo.isPlaying || (exo.playWhenReady && exo.playbackState == Player.STATE_BUFFERING),
            loading = loading,
            positionMs = exo.currentPosition,
            durationMs = exo.duration.takeIf { it > 0 } ?: ((current?.duration ?: 0.0) * 1000).toLong(),
            settings = settings,
            message = message,
            queue = queue.toList(),
            fromQueue = fromQueue,
            upNext = upNext().take(20),
            source = source(),
            offline = mode == "list" && context == OFFLINE,
        )
    }

    private val QUEUE = PlayContext("queue")
    private val OFFLINE = PlayContext("library", "offline", "Скачанное")

    private fun problem(e: IOException): String = when (e) {
        is ApiException -> e.message ?: "Ошибка сервиса"
        else -> "Сервис VaginaWave недоступен: проверьте адрес в настройках"
    }
}
