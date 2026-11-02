package app.volna.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.graphics.BlendMode
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.CompositingStrategy
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.text.font.FontWeight
import app.volna.data.Lyrics
import app.volna.data.Track
import app.volna.player.Offline
import app.volna.player.PlayContext
import app.volna.player.PlayerState
import app.volna.player.VolnaPlayer
import coil3.compose.AsyncImage

/** Главная: одна большая кнопка «Моя волна» (она же «сейчас играет») и полки ниже. */
@Composable
fun HomeScreen(nav: Navigator, onMore: (Track) -> Unit, onTune: () -> Unit) {
    val state by VolnaPlayer.state.collectAsState()
    val online by Offline.online.collectAsState()
    val shelves = load("home", online) { Offline.home() }

    BoxWithConstraints(Modifier.fillMaxSize()) {
        val heroHeight = maxHeight
        LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(horizontal = 16.dp)) {
            item { Hero(state, nav, Modifier.height(heroHeight), onMore, onTune) }
            when (val s = shelves.value) {
                Load.Loading -> item { Loading() }
                is Load.Failed -> item {
                    if (online) Empty("Полки недоступны: ${s.message}")
                    else Empty("Нет связи с сервером. Моя волна играет из скачанного, всё скачанное — в «Коллекция → Скачанное».")
                }
                is Load.Done -> items(s.value, key = { it.id }) { shelf ->
                    if (shelf.tracks.isNotEmpty()) {
                        SectionTitle(shelf.title)
                        LazyRow(horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                            items(shelf.tracks, key = { it.id }) { track ->
                                TrackCard(track) {
                                    VolnaPlayer.playList(shelf.tracks, shelf.tracks.indexOf(track), PlayContext("library", shelf.id, shelf.title))
                                }
                            }
                        }
                    } else {
                        ItemShelf(shelf.title, shelf.items) { item ->
                            if (shelf.id == "playlists") VolnaPlayer.startWave("playlist", item.id)
                            else nav.go(Screen.Entity(item.type, item.id))
                        }
                    }
                }
            }
            item { Spacer(Modifier.height(32.dp)) }
        }
    }
}

@Composable
private fun Hero(state: PlayerState, nav: Navigator, modifier: Modifier, onMore: (Track) -> Unit, onTune: () -> Unit) {
    val online by Offline.online.collectAsState()
    val forced by Offline.forced.collectAsState()
    val track = state.track
    val lyrics = if (track != null && UiPrefs.lyrics) load("lyrics", track.id) { Offline.lyrics(track.id) }.value else null
    val text = (lyrics as? Load.Done)?.value?.takeIf { it.lines.isNotEmpty() }
    Column(
        modifier.fillMaxWidth().statusBarsPadding(),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        // обложку (или текст на её месте) тоже можно листать свайпом, как название
        Box((if (track != null) Modifier.trackSwipe() else Modifier), contentAlignment = Alignment.Center) {
            if (text != null) LyricsPanel(text, state) else WaveButton(state)
        }
        Spacer(Modifier.height(24.dp))
        // название ведёт к альбому, артист — к артисту; свайп по ним листает треки
        Column(
            (if (track != null) Modifier.trackSwipe() else Modifier).fillMaxWidth(),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text(
                track?.title ?: "Моя волна",
                style = MaterialTheme.typography.headlineMedium,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                textAlign = TextAlign.Center,
                modifier = if (track != null && track.albumId.isNotEmpty()) {
                    Modifier.clickable { nav.go(Screen.Entity("album", track.albumId)) }
                } else Modifier,
            )
            Spacer(Modifier.height(6.dp))
            if (track != null) {
                ArtistLink(track, style = MaterialTheme.typography.bodyLarge)
            } else {
                Text(
                    if (state.loading) "Настраиваю волну под ваш вкус…" else "Нажмите, и музыка пойдёт сама",
                    style = MaterialTheme.typography.bodyLarge,
                    color = Palette.Muted,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
        }
        Text(
            if (track != null) state.reason else "",
            style = MaterialTheme.typography.bodySmall,
            color = Palette.Muted.copy(alpha = 0.47f),
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.padding(top = 10.dp).height(18.dp),
        )
        if (track != null) {
            Spacer(Modifier.height(16.dp))
            Progress(state)
            Spacer(Modifier.height(18.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp), verticalAlignment = Alignment.CenterVertically) {
                Control(VIcons.Dislike, "Не нравится") { VolnaPlayer.next("dislike") }
                Control(VIcons.Prev, "Назад") { VolnaPlayer.previous() }
                Control(if (state.playing) VIcons.Pause else VIcons.Play, if (state.playing) "Пауза" else "Играть", primary = true) {
                    VolnaPlayer.toggle()
                }
                Control(VIcons.Next, "Дальше") { VolnaPlayer.next("skip") }
                Control(if (track.liked) VIcons.HeartFilled else VIcons.Heart, "Нравится", pressed = track.liked) { VolnaPlayer.like() }
            }
        }
        Spacer(Modifier.height(20.dp))
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            if (track != null) {
                IconButtonV(VIcons.Lyrics, if (UiPrefs.lyrics) "Скрыть текст" else "Показать текст", 44.dp,
                    tint = if (UiPrefs.lyrics) Palette.Fg else Palette.Muted) {
                    UiPrefs.lyrics = !UiPrefs.lyrics
                    VolnaPlayer.api.prefs.lyrics = UiPrefs.lyrics
                    if (UiPrefs.lyrics && (lyrics as? Load.Done)?.value?.lines?.isEmpty() == true) VolnaPlayer.say("У этого трека нет текста")
                }
            }
            if (track != null && state.offline && forced) { // офлайн-режим включён вручную: выйти из него и в волну
                Pill("Выйти из офлайна", VIcons.Wave) {
                    Offline.setForced(false)
                    VolnaPlayer.startWave()
                }
            } else if (track != null && state.offline && !online) {
                Pill("Скачанное", VIcons.Download) { nav.go(Screen.Downloaded) }
            } else if (track != null && (state.mode == "list" || state.offline)) { // связь вернулась — снова настоящая волна
                Pill("Включить Мою волну", VIcons.Wave) { VolnaPlayer.startWave() }
            } else {
                val steered = state.mode == "wave" && state.kind == "steer" && state.title.isNotEmpty()
                val summary = waveSummary(state.settings)
                // длинная надпись сокращается, чтобы очередь и «⋯» оставались на экране
                Pill(if (steered) "По «${state.title}» · $summary" else summary, VIcons.Sliders, modifier = Modifier.weight(1f, fill = false), onClick = onTune)
            }
            if (track != null) {
                IconButtonV(VIcons.Queue, "Очередь", 44.dp, tint = if (state.queue.isNotEmpty()) Palette.Fg else Palette.Muted) {
                    nav.go(Screen.Queue)
                }
                IconButtonV(VIcons.More, "Ещё: волна и плейлист по треку", 44.dp, tint = Palette.Fg) { onMore(track) }
            }
        }
        if (track != null && state.mode == "wave" && state.kind == "steer" && state.title.isNotEmpty()) {
            Text( // волна по треку, альбому или плейлисту: назад к обычной
                "Вернуться к обычной Моей волне",
                style = MaterialTheme.typography.bodyMedium,
                modifier = Modifier.padding(top = 12.dp).clip(CircleShape).clickable { VolnaPlayer.startWave() }.padding(horizontal = 12.dp, vertical = 6.dp),
            )
        }
        state.nextUp?.let { next ->
            Text(
                (if (state.queue.isNotEmpty()) "Дальше из очереди: " else "Дальше: ") + "${next.title} · ${next.artist}",
                style = MaterialTheme.typography.bodySmall,
                color = Palette.Muted,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.padding(top = 12.dp),
            )
        }
    }
}

/**
 * Текст песни на месте круга. Синхронизированный подсвечивает текущую строку и сам
 * прокручивается; нажатие на строку перематывает к ней.
 */
@Composable
private fun LyricsPanel(lyrics: Lyrics, state: PlayerState) {
    val list = rememberLazyListState()
    val current = currentLine(lyrics, state.positionMs)
    LaunchedEffect(current) { if (current >= 0) list.animateScrollToItem((current - 2).coerceAtLeast(0)) }
    Box(contentAlignment = Alignment.TopEnd) {
        LazyColumn(
            state = list,
            horizontalAlignment = Alignment.CenterHorizontally,
            contentPadding = PaddingValues(vertical = 40.dp),
            modifier = Modifier
                .widthIn(max = 360.dp)
                .fillMaxWidth()
                .height(252.dp)
                .graphicsLayer(compositingStrategy = CompositingStrategy.Offscreen)
                .drawWithContent {
                    drawContent()
                    // края растворяются, чтобы строки уходили в фон, а не обрезались
                    drawRect(
                        Brush.verticalGradient(0f to Color.Transparent, 0.18f to Color.Black, 0.82f to Color.Black, 1f to Color.Transparent),
                        blendMode = BlendMode.DstIn,
                    )
                },
        ) {
            itemsIndexed(lyrics.lines) { i, line ->
                val active = i == current
                val seekable = lyrics.synced && line.time != null && state.durationMs > 0
                Text(
                    line.text.ifBlank { "♪" },
                    style = MaterialTheme.typography.titleMedium.copy(fontSize = 19.sp, lineHeight = 25.sp),
                    fontWeight = if (active) FontWeight.Bold else FontWeight.Medium,
                    color = when {
                        !lyrics.synced || active -> Palette.Fg
                        i < current -> Palette.Muted.copy(alpha = 0.35f)
                        else -> Palette.Muted
                    },
                    textAlign = TextAlign.Center,
                    modifier = Modifier
                        .fillMaxWidth()
                        .clickable(enabled = seekable) { VolnaPlayer.seek((line.time!! * 1000 / state.durationMs).toFloat()) }
                        .padding(horizontal = 8.dp, vertical = 6.dp),
                )
            }
        }
        // как в Spotify: развернуть текст на весь экран
        IconButtonV(VIcons.Expand, "Текст на весь экран", 40.dp, tint = Palette.Fg) { LyricsFull.show = true }
    }
}

/** Круг волны: обложка видна, пока играет; значок «играть» — только на паузе. */
@Composable
private fun WaveButton(state: PlayerState) {
    val track = state.track
    Box(contentAlignment = Alignment.Center, modifier = Modifier.size(252.dp)) {
        Box(Modifier.size(252.dp).border(1.dp, Palette.Fg.copy(alpha = 0.32f), CircleShape))
        Box(
            Modifier
                .size(220.dp)
                .clip(CircleShape)
                .background(Palette.Veil)
                .border(1.5.dp, Palette.Fg, CircleShape)
                .clickable { VolnaPlayer.toggle() },
            contentAlignment = Alignment.Center,
        ) {
            if (track != null && track.cover.isNotEmpty()) {
                AsyncImage(
                    model = VolnaPlayer.api.url(track.cover),
                    contentDescription = null,
                    contentScale = ContentScale.Crop,
                    colorFilter = if (CoverStyle.color) null else CoverStyle.grayscale,
                    modifier = Modifier.size(200.dp).clip(CircleShape).alpha(if (state.playing) 0.9f else 0.42f),
                )
            }
            if (!state.playing) VIcon(VIcons.Play, if (track == null) "Запустить «Мою волну»" else "Играть", 64.dp)
        }
        if (state.loading) CircularProgressIndicator(color = Palette.Fg, strokeWidth = 1.dp, modifier = Modifier.size(252.dp))
    }
}

@Composable
fun Progress(state: PlayerState) {
    val fraction = if (state.durationMs > 0) (state.positionMs.toFloat() / state.durationMs).coerceIn(0f, 1f) else 0f
    Box(
        Modifier
            .widthIn(max = 320.dp)
            .fillMaxWidth(0.82f)
            .height(18.dp)
            .pointerInput(Unit) { detectTapGestures { VolnaPlayer.seek(it.x / size.width) } },
        contentAlignment = Alignment.CenterStart,
    ) {
        Box(Modifier.fillMaxWidth().height(2.dp).background(Palette.Faint))
        Box(Modifier.fillMaxWidth(fraction).height(2.dp).background(Palette.Fg))
    }
}

