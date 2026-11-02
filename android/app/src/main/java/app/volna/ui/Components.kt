package app.volna.ui

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.spring
import androidx.compose.foundation.gestures.detectHorizontalDragGestures
import androidx.compose.foundation.systemGestureExclusion
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.text.TextStyle
import kotlin.math.abs
import kotlin.math.roundToInt
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Slider
import androidx.compose.material3.SliderDefaults
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.ColorFilter
import androidx.compose.ui.graphics.ColorMatrix
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.draw.alpha
import androidx.media3.exoplayer.offline.Download
import app.volna.data.ApiException
import app.volna.data.Item
import app.volna.data.Track
import app.volna.player.Downloads
import app.volna.player.Offline
import app.volna.player.VolnaPlayer
import coil3.compose.AsyncImage
import kotlinx.coroutines.MainScope
import kotlinx.coroutines.launch
import org.json.JSONObject
import java.util.Locale

/** Ч/б обложки по умолчанию (настройка «Цветные обложки» это выключает). */
object CoverStyle {
    var color by mutableStateOf(false)
    val grayscale = ColorFilter.colorMatrix(ColorMatrix().apply { setToSaturation(0f) })
}

@Composable
fun Cover(url: String, modifier: Modifier = Modifier, round: Boolean = false, corner: Dp = 12.dp) {
    val shape = if (round) CircleShape else RoundedCornerShape(corner)
    Box(
        modifier
            .aspectRatio(1f)
            .clip(shape)
            .background(androidx.compose.ui.graphics.Color(0xFF0D0D0D))
            .border(1.dp, Palette.Faint, shape),
    ) {
        if (url.isNotEmpty()) {
            AsyncImage(
                model = Downloads.cover(url) ?: VolnaPlayer.api.url(url), // скачанная обложка — из файла, и без сети
                contentDescription = null,
                contentScale = ContentScale.Crop,
                colorFilter = if (CoverStyle.color) null else CoverStyle.grayscale,
                modifier = Modifier.fillMaxSize(),
            )
        }
    }
}

@Composable
fun VIcon(image: ImageVector, description: String?, size: Dp = 22.dp, tint: androidx.compose.ui.graphics.Color = Palette.Fg) {
    Icon(image, contentDescription = description, tint = tint, modifier = Modifier.size(size))
}

@Composable
fun IconButtonV(image: ImageVector, description: String, size: Dp = 40.dp, tint: androidx.compose.ui.graphics.Color = Palette.Muted, onClick: () -> Unit) {
    Box(
        Modifier.size(size).clip(CircleShape).clickable(onClick = onClick),
        contentAlignment = Alignment.Center,
    ) { VIcon(image, description, tint = tint) }
}

/** Круглая кнопка управления под волной; primary — белая, для «играть/пауза». */
@Composable
fun Control(image: ImageVector, description: String, pressed: Boolean = false, primary: Boolean = false, onClick: () -> Unit) {
    Box(
        Modifier
            .size(if (primary) 64.dp else 50.dp)
            .clip(CircleShape)
            .background(if (primary) Palette.Fg else Palette.Veil)
            .border(1.dp, if (pressed || primary) Palette.Fg else Palette.Faint, CircleShape)
            .clickable(onClick = onClick),
        contentAlignment = Alignment.Center,
    ) { VIcon(image, description, if (primary) 30.dp else 24.dp, if (primary) Palette.Bg else Palette.Fg) }
}

/** Навигация для мелких элементов (имя артиста в строке трека и т. п.): задаётся в корне приложения. */
val LocalNav = staticCompositionLocalOf<Navigator?> { null }

/** Имя артиста, по нажатию — его страница. */
@Composable
fun ArtistLink(track: Track, modifier: Modifier = Modifier, style: TextStyle = MaterialTheme.typography.bodySmall) {
    val nav = LocalNav.current
    Text(
        track.artist,
        style = style,
        color = Palette.Muted,
        maxLines = 1,
        overflow = TextOverflow.Ellipsis,
        modifier = if (nav != null && track.artistId.isNotEmpty()) modifier.clickable { nav.go(Screen.Entity("artist", track.artistId)) } else modifier,
    )
}

/**
 * Свайп по названию трека: влево — дальше, вправо — назад. Текст едет за пальцем
 * и возвращается на место, если палец не дотянул до порога.
 */
@Composable
fun Modifier.trackSwipe(): Modifier {
    val offset = remember { Animatable(0f) }
    val scope = rememberCoroutineScope()
    val threshold = with(LocalDensity.current) { 64.dp.toPx() }
    return this
        .graphicsLayer {
            translationX = offset.value
            alpha = 1f - (abs(offset.value) / (threshold * 3)).coerceIn(0f, 0.6f)
        }
        .pointerInput(Unit) {
            var dragged = 0f // считаем здесь: анимация смещения обновляется асинхронно и на быстром свайпе отстаёт
            detectHorizontalDragGestures(
                onDragStart = { dragged = 0f },
                onDragEnd = {
                    when {
                        dragged <= -threshold -> VolnaPlayer.next("skip")
                        dragged >= threshold -> VolnaPlayer.previous(restartFirst = false)
                    }
                    scope.launch { offset.animateTo(0f, spring(stiffness = Spring.StiffnessMediumLow)) }
                },
                onDragCancel = { scope.launch { offset.animateTo(0f) } },
            ) { change, dx ->
                change.consume()
                dragged += dx
                scope.launch { offset.snapTo(dragged) }
            }
        }
}

@Composable
fun PageHeader(title: String, onBack: (() -> Unit)? = null) {
    Row(
        Modifier.fillMaxWidth().padding(top = 8.dp, bottom = 8.dp).height(48.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        if (onBack != null) {
            IconButtonV(VIcons.Back, "Назад", tint = Palette.Fg, onClick = onBack)
            Spacer(Modifier.width(4.dp))
        }
        Text(title, style = MaterialTheme.typography.headlineLarge, maxLines = 1, overflow = TextOverflow.Ellipsis)
    }
}

@Composable
fun SectionTitle(text: String) {
    Text(text, style = MaterialTheme.typography.titleLarge, modifier = Modifier.padding(top = 24.dp, bottom = 12.dp))
}

@Composable
fun GroupLabel(text: String) {
    Text(text.uppercase(Locale.ROOT), style = MaterialTheme.typography.labelSmall, color = Palette.Muted,
        modifier = Modifier.padding(bottom = 12.dp))
}

@Composable
fun Hint(text: String, modifier: Modifier = Modifier) {
    Text(text, style = MaterialTheme.typography.bodySmall, color = Palette.Muted, modifier = modifier.padding(top = 8.dp))
}

@Composable
fun Divider() {
    Box(Modifier.fillMaxWidth().height(1.dp).background(Palette.Faint))
}

@Composable
fun Empty(text: String) {
    Text(text, style = MaterialTheme.typography.bodyMedium, color = Palette.Muted, modifier = Modifier.padding(vertical = 24.dp))
}

@Composable
fun Loading() {
    Box(Modifier.fillMaxWidth().padding(28.dp), contentAlignment = Alignment.Center) {
        CircularProgressIndicator(color = Palette.Fg, strokeWidth = 1.5.dp, modifier = Modifier.size(24.dp))
    }
}

/** Кнопка-«пилюля». primary — белая с чёрным текстом. */
@Composable
fun Pill(text: String, icon: ImageVector? = null, primary: Boolean = false, modifier: Modifier = Modifier, onClick: () -> Unit) {
    val shape = RoundedCornerShape(50)
    Row(
        modifier
            .clip(shape)
            .background(if (primary) Palette.Fg else Palette.Veil)
            .border(1.dp, if (primary) Palette.Fg else Palette.Faint, shape)
            .clickable(onClick = onClick)
            .padding(horizontal = 18.dp, vertical = 11.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        if (icon != null) VIcon(icon, null, 18.dp, if (primary) Palette.Bg else Palette.Fg)
        Text(text, style = MaterialTheme.typography.titleMedium.copy(fontSize = MaterialTheme.typography.bodyMedium.fontSize),
            color = if (primary) Palette.Bg else Palette.Fg, maxLines = 1, overflow = TextOverflow.Ellipsis)
    }
}

@OptIn(ExperimentalFoundationApi::class, androidx.compose.foundation.layout.ExperimentalLayoutApi::class)
@Composable
fun Chips(options: List<Pair<String, String>>, value: String, onChange: (String) -> Unit) {
    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        options.forEach { (key, label) ->
            val selected = key == value
            val shape = RoundedCornerShape(50)
            Text(
                label,
                style = MaterialTheme.typography.bodyMedium,
                color = if (selected) Palette.Bg else Palette.Muted,
                modifier = Modifier
                    .clip(shape)
                    .background(if (selected) Palette.Fg else androidx.compose.ui.graphics.Color.Transparent)
                    .border(1.dp, if (selected) Palette.Fg else Palette.Faint, shape)
                    .clickable { onChange(key) }
                    .padding(horizontal = 14.dp, vertical = 8.dp),
            )
        }
    }
}

@Composable
fun ToggleRow(label: String, checked: Boolean, onChange: (Boolean) -> Unit) {
    Row(
        Modifier.fillMaxWidth().clickable { onChange(!checked) }.padding(vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(label, style = MaterialTheme.typography.bodyLarge, modifier = Modifier.weight(1f))
        Switch(
            checked = checked,
            onCheckedChange = onChange,
            colors = SwitchDefaults.colors(
                checkedThumbColor = Palette.Bg,
                checkedTrackColor = Palette.Fg,
                uncheckedThumbColor = Palette.Muted,
                uncheckedTrackColor = Palette.Bg,
                uncheckedBorderColor = Palette.Faint,
            ),
        )
    }
}

@Composable
fun SliderRow(
    label: String,
    value: Float,
    range: ClosedFloatingPointRange<Float>,
    left: String,
    right: String,
    format: (Float) -> String,
    step: Float = 0.05f,
    onChange: (Float) -> Unit,
) {
    var current by remember(value) { mutableFloatStateOf(value) }
    Column(Modifier.padding(bottom = 12.dp)) {
        Row {
            Text(label, style = MaterialTheme.typography.bodyLarge, modifier = Modifier.weight(1f))
            Text(format(current), style = MaterialTheme.typography.bodyMedium, color = Palette.Muted)
        }
        Slider(
            value = current,
            onValueChange = { current = it },
            onValueChangeFinished = { onChange(current) },
            valueRange = range,
            // шаги и отступ от края: крайние значения легко поймать, а жест «назад» не перехватывает ползунок
            steps = ((range.endInclusive - range.start) / step).roundToInt() - 1,
            modifier = Modifier.padding(horizontal = 14.dp).systemGestureExclusion(),
            colors = SliderDefaults.colors(
                thumbColor = Palette.Fg,
                activeTrackColor = Palette.Fg,
                inactiveTrackColor = Palette.Faint,
            ),
        )
        Row {
            Text(left, style = MaterialTheme.typography.bodySmall, color = Palette.Muted, modifier = Modifier.weight(1f))
            Text(right, style = MaterialTheme.typography.bodySmall, color = Palette.Muted)
        }
    }
}

// --- треки и карточки ---

fun formatDuration(seconds: Double): String {
    val s = seconds.toInt().coerceAtLeast(0)
    return "%d:%02d".format(s / 60, s % 60)
}

fun longDuration(seconds: Double): String {
    val m = (seconds / 60).toInt()
    return if (m >= 60) "${m / 60} ч ${m % 60} мин" else "$m мин"
}

fun plural(n: Int, one: String, few: String, many: String): String {
    val mod10 = n % 10
    val mod100 = n % 100
    return when {
        mod10 == 1 && mod100 != 11 -> one
        mod10 in 2..4 && (mod100 < 10 || mod100 >= 20) -> few
        else -> many
    }
}

/** Строка трека. Смахнуть вправо — в очередь, как в Spotify. */
@Composable
fun TrackRow(
    track: Track,
    current: Boolean,
    number: Int? = null,
    showCover: Boolean = true,
    queueable: Boolean = true,
    onMore: (Track) -> Unit,
    onPlay: () -> Unit,
) {
    val offset = remember { Animatable(0f) }
    val scope = rememberCoroutineScope()
    val haptics = LocalHapticFeedback.current
    val threshold = with(LocalDensity.current) { 88.dp.toPx() }
    val armed = offset.value >= threshold
    Box(Modifier.fillMaxWidth()) {
        if (offset.value > 1f) {
            Row(Modifier.matchParentSize().padding(start = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                VIcon(VIcons.Queue, null, 22.dp, if (armed) Palette.Fg else Palette.Muted)
                Spacer(Modifier.width(8.dp))
                Text("В очередь", style = MaterialTheme.typography.bodyMedium, color = if (armed) Palette.Fg else Palette.Muted)
            }
        }
        TrackRowContent(
            track, current, number, showCover, onMore, onPlay,
            Modifier
                .graphicsLayer { translationX = offset.value }
                .background(if (offset.value > 1f) Palette.Bg else androidx.compose.ui.graphics.Color.Transparent)
                .then(
                    if (!queueable) Modifier else Modifier.pointerInput(track.id) {
                        var dragged = 0f
                        detectHorizontalDragGestures(
                            onDragStart = { dragged = 0f },
                            onDragEnd = {
                                if (dragged >= threshold) VolnaPlayer.enqueue(track)
                                scope.launch { offset.animateTo(0f, spring(stiffness = Spring.StiffnessMediumLow)) }
                            },
                            onDragCancel = { scope.launch { offset.animateTo(0f) } },
                        ) { change, dx ->
                            change.consume()
                            val before = dragged
                            dragged = (dragged + dx).coerceIn(0f, threshold * 1.4f) // только вправо
                            if (before < threshold && dragged >= threshold) haptics.performHapticFeedback(HapticFeedbackType.LongPress)
                            scope.launch { offset.snapTo(dragged) }
                        }
                    },
                ),
        )
    }
}

@Composable
private fun TrackRowContent(
    track: Track,
    current: Boolean,
    number: Int?,
    showCover: Boolean,
    onMore: (Track) -> Unit,
    onPlay: () -> Unit,
    modifier: Modifier,
) {
    val states by Downloads.states.collectAsState()
    val online by Offline.online.collectAsState()
    val download = states[track.id]
    val available = online || download == Download.STATE_COMPLETED // без сети играет только скачанное
    Row(
        modifier.fillMaxWidth().alpha(if (available) 1f else 0.4f).clip(RoundedCornerShape(10.dp))
            .clickable { if (available) onPlay() else VolnaPlayer.say(Offline.NOT_DOWNLOADED) }
            .padding(vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        if (number != null) {
            Box(Modifier.width(24.dp), contentAlignment = Alignment.Center) {
                if (current) VIcon(VIcons.Wave, null, 18.dp) else Text("$number", style = MaterialTheme.typography.bodyMedium, color = Palette.Muted)
            }
        }
        if (showCover) Cover(track.cover, Modifier.size(44.dp), corner = 6.dp)
        Column(Modifier.weight(1f)) {
            Text(track.title, style = MaterialTheme.typography.bodyLarge,
                fontWeight = if (current) FontWeight.SemiBold else FontWeight.Normal,
                maxLines = 1, overflow = TextOverflow.Ellipsis)
            if (track.reason.isNotEmpty()) {
                Text(track.reason, style = MaterialTheme.typography.bodySmall, color = Palette.Muted, maxLines = 1, overflow = TextOverflow.Ellipsis)
            } else {
                ArtistLink(track)
            }
        }
        if (track.liked) VIcon(VIcons.HeartFilled, "Нравится", 16.dp, Palette.Muted)
        DownloadMark(download)
        Text(formatDuration(track.duration), style = MaterialTheme.typography.bodySmall, color = Palette.Muted)
        IconButtonV(VIcons.More, "Действия: ${track.title}", 36.dp) { onMore(track) }
    }
}

/** Значок у строки трека: скачан, качается или ждёт Wi‑Fi. */
@Composable
fun DownloadMark(state: Int?) {
    val blocked by Downloads.blocked.collectAsState()
    when (state) {
        Download.STATE_COMPLETED -> VIcon(VIcons.Downloaded, "Скачан", 16.dp, Palette.Fg)
        Download.STATE_QUEUED, Download.STATE_DOWNLOADING, Download.STATE_RESTARTING, Download.STATE_STOPPED ->
            if (blocked != null) VIcon(VIcons.Download, blocked, 16.dp, Palette.Muted)
            else CircularProgressIndicator(Modifier.size(13.dp), color = Palette.Muted, strokeWidth = 1.5.dp)
        else -> {}
    }
}

/** «1,2 ГБ», «340 МБ» — сколько места занимают загрузки. */
fun formatBytes(bytes: Long): String = when {
    bytes >= 1L shl 30 -> String.format(Locale.forLanguageTag("ru"), "%.1f ГБ", bytes / (1L shl 30).toDouble())
    bytes >= 1L shl 20 -> "${bytes shr 20} МБ"
    else -> "${(bytes shr 10).coerceAtLeast(0)} КБ"
}

@Composable
fun ItemCard(item: Item, width: Dp? = null, onClick: () -> Unit) {
    val round = item.type == "artist"
    Column(
        (if (width != null) Modifier.width(width) else Modifier.fillMaxWidth()).clickable(onClick = onClick),
        horizontalAlignment = if (round) Alignment.CenterHorizontally else Alignment.Start,
    ) {
        Cover(item.cover, Modifier.fillMaxWidth(), round = round)
        Spacer(Modifier.height(8.dp))
        Text(item.title, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.SemiBold, maxLines = 2, overflow = TextOverflow.Ellipsis)
        if (item.subtitle.isNotEmpty()) {
            val nav = LocalNav.current
            val toArtist = item.type == "album" && item.artistId.isNotEmpty() && nav != null
            Text(
                item.subtitle, style = MaterialTheme.typography.bodySmall, color = Palette.Muted, maxLines = 1, overflow = TextOverflow.Ellipsis,
                modifier = if (toArtist) Modifier.clickable { nav?.go(Screen.Entity("artist", item.artistId)) } else Modifier,
            )
        }
    }
}

@Composable
fun TrackCard(track: Track, onClick: () -> Unit) {
    Column(Modifier.width(148.dp).clickable(onClick = onClick)) {
        Cover(track.cover, Modifier.fillMaxWidth())
        Spacer(Modifier.height(8.dp))
        Text(track.title, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.SemiBold, maxLines = 2, overflow = TextOverflow.Ellipsis)
        ArtistLink(track)
    }
}

@Composable
fun ItemShelf(title: String, items: List<Item>, onItem: (Item) -> Unit) {
    if (items.isEmpty()) return
    SectionTitle(title)
    LazyRow(horizontalArrangement = Arrangement.spacedBy(14.dp), contentPadding = PaddingValues(end = 16.dp)) {
        items(items, key = { it.type + it.id }) { item -> ItemCard(item, 148.dp) { onItem(item) } }
    }
}

/** Сетка карточек внутри вертикальной прокрутки: по 2–4 в ряд в зависимости от ширины. */
@Composable
fun ItemGrid(items: List<Item>, columns: Int = 2, onItem: (Item) -> Unit) {
    Column(verticalArrangement = Arrangement.spacedBy(20.dp)) {
        items.chunked(columns).forEach { row ->
            Row(horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                row.forEach { item -> Box(Modifier.weight(1f)) { ItemCard(item) { onItem(item) } } }
                repeat(columns - row.size) { Spacer(Modifier.weight(1f)) }
            }
        }
    }
}

/** Строка-раздел: иконка, название, подпись, счётчик, шеврон (Коллекция, Настройки). */
@Composable
fun Tile(icon: ImageVector, title: String, subtitle: String = "", count: String = "", onClick: () -> Unit) {
    Column {
        Row(
            Modifier.fillMaxWidth().clickable(onClick = onClick).padding(vertical = 14.dp, horizontal = 4.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            VIcon(icon, null)
            Column(Modifier.weight(1f)) {
                Text(title, style = MaterialTheme.typography.bodyLarge)
                if (subtitle.isNotEmpty()) {
                    Text(subtitle, style = MaterialTheme.typography.bodySmall, color = Palette.Muted, maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
            }
            if (count.isNotEmpty()) Text(count, style = MaterialTheme.typography.bodyMedium, color = Palette.Muted)
            VIcon(VIcons.Chevron, null, 18.dp, Palette.Muted)
        }
        Divider()
    }
}

// --- мини-плеер и вкладки ---

@Composable
fun MiniPlayer(onOpen: () -> Unit) {
    val state by VolnaPlayer.state.collectAsState()
    val track = state.track ?: return
    val progress = if (state.durationMs > 0) state.positionMs.toFloat() / state.durationMs else 0f
    Column(Modifier.fillMaxWidth().background(Palette.Bg.copy(alpha = 0.92f))) {
        Box(Modifier.fillMaxWidth().height(1.dp).background(Palette.Faint)) {
            Box(Modifier.fillMaxWidth(progress.coerceIn(0f, 1f)).height(2.dp).background(Palette.Fg))
        }
        Row(
            Modifier.fillMaxWidth().padding(start = 12.dp, end = 4.dp, top = 8.dp, bottom = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Row(Modifier.weight(1f).clickable(onClick = onOpen), verticalAlignment = Alignment.CenterVertically) {
                Cover(track.cover, Modifier.size(44.dp), corner = 6.dp)
                Spacer(Modifier.width(12.dp))
                Column(Modifier.weight(1f).trackSwipe()) {
                    Text(track.title, style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.SemiBold, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    Text(track.artist, style = MaterialTheme.typography.bodySmall, color = Palette.Muted, maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
            }
            IconButtonV(VIcons.Prev, "Назад", tint = Palette.Fg) { VolnaPlayer.previous() }
            IconButtonV(if (state.playing) VIcons.Pause else VIcons.Play, if (state.playing) "Пауза" else "Играть", tint = Palette.Fg) { VolnaPlayer.toggle() }
            IconButtonV(VIcons.Next, "Дальше", tint = Palette.Fg) { VolnaPlayer.next("skip") }
        }
    }
}

@Composable
fun TabBar(active: String, onTab: (String) -> Unit) {
    val tabs = listOf(
        Triple("home", "Главная", VIcons.Wave),
        Triple("search", "Поиск", VIcons.Search),
        Triple("library", "Коллекция", VIcons.Library),
        Triple("settings", "Настройки", VIcons.Settings),
    )
    Column(Modifier.fillMaxWidth().background(Palette.Bg.copy(alpha = 0.94f))) {
        Divider()
        Row(Modifier.fillMaxWidth().navigationBarsPadding().height(62.dp)) {
            tabs.forEach { (key, label, icon) ->
                val color = if (key == active) Palette.Fg else Palette.Muted
                Column(
                    Modifier.weight(1f).fillMaxSize().clickable { onTab(key) },
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.Center,
                ) {
                    VIcon(icon, null, tint = color)
                    Spacer(Modifier.height(4.dp))
                    Text(label, style = MaterialTheme.typography.bodySmall.copy(fontSize = MaterialTheme.typography.labelSmall.fontSize), color = color)
                }
            }
        }
    }
}

// --- шторки ---

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun Sheet(onDismiss: () -> Unit, content: @Composable ColumnScope.() -> Unit) {
    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
        containerColor = Palette.Bg,
        contentColor = Palette.Fg,
        scrimColor = Palette.Veil,
        shape = RoundedCornerShape(topStart = 22.dp, topEnd = 22.dp),
        dragHandle = { Box(Modifier.padding(top = 10.dp).size(36.dp, 4.dp).clip(CircleShape).background(Palette.Faint)) },
    ) {
        Column(Modifier.verticalScroll(rememberScrollState()).padding(horizontal = 16.dp).padding(bottom = 24.dp), content = content)
    }
}

/** Меню «⋯» трека: волна и плейлист по треку, лайк, переходы. */
@Composable
fun TrackActions(track: Track, onDismiss: () -> Unit, navigate: (Screen) -> Unit) {
    Sheet(onDismiss) {
        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(vertical = 8.dp)) {
            Cover(track.cover, Modifier.size(44.dp), corner = 6.dp)
            Spacer(Modifier.width(12.dp))
            Column {
                Text(track.title, style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.SemiBold, maxLines = 1, overflow = TextOverflow.Ellipsis)
                Text(track.artist, style = MaterialTheme.typography.bodySmall, color = Palette.Muted, maxLines = 1)
            }
        }
        Divider()
        fun act(run: () -> Unit) { onDismiss(); run() }
        Tile(VIcons.Queue, "Играть следующим") { act { VolnaPlayer.playNext(track) } }
        Tile(VIcons.Queue, "Добавить в очередь") { act { VolnaPlayer.enqueue(track) } }
        Tile(VIcons.Wave, "Моя волна по треку") { act { VolnaPlayer.startWave("track", track.id); navigate(Screen.Home) } }
        Tile(VIcons.Mix, "Собрать плейлист по треку") { act { navigate(Screen.Mix("track", track.id)) } }
        Tile(VIcons.Heart, if (track.liked) "Убрать из «Мне нравится»" else "Нравится") { act { likeTrack(track) } }
        val saved by Downloads.saved.collectAsState()
        if (track.id in saved) {
            Tile(VIcons.Downloaded, "Удалить из загрузок") {
                act {
                    Downloads.drop(listOf(track.id))
                    VolnaPlayer.say("«${track.title}» удалён с телефона")
                }
            }
        } else {
            Tile(VIcons.Download, "Скачать", "Будет играть без сети") {
                act {
                    Downloads.add(listOf(track), Downloads.MANUAL)
                    VolnaPlayer.say(Downloads.queued(track.title))
                }
            }
        }
        if (track.albumId.isNotEmpty()) Tile(VIcons.Album, "Перейти к альбому") { act { navigate(Screen.Entity("album", track.albumId)) } }
        if (track.artistId.isNotEmpty()) Tile(VIcons.Artist, "Перейти к артисту") { act { navigate(Screen.Entity("artist", track.artistId)) } }
    }
}

private fun likeTrack(track: Track) {
    val playing = VolnaPlayer.state.value.track
    if (playing?.id == track.id) {
        VolnaPlayer.like()
        return
    }
    val liked = !track.liked
    val done = if (liked) "Добавлено в «Мне нравится»" else "Убрано из «Мне нравится»"
    Downloads.liked(listOf(track), liked)
    if (!Offline.isOnline) { // отметка уйдёт на сервер, когда будет связь
        Offline.like("track", track.id, liked)
        VolnaPlayer.say(done)
        return
    }
    MainScope().launch {
        try {
            VolnaPlayer.api.like("track", track.id, liked)
            VolnaPlayer.say(done)
        } catch (e: ApiException) {
            Downloads.liked(listOf(track), !liked)
            VolnaPlayer.say(e.message ?: "Не удалось отметить трек")
        } catch (e: java.io.IOException) {
            Offline.like("track", track.id, liked)
            VolnaPlayer.say(done)
        }
    }
}

// --- настройки волны: одни и те же в шторке на главной и в разделе настроек ---

val CHARACTERS = listOf("favorite" to "Любимое", "balanced" to "Баланс", "discover" to "Незнакомое", "forgotten" to "Забытое")
val MOODS = listOf("any" to "Любое", "energetic" to "Бодрое", "happy" to "Весёлое", "calm" to "Спокойное", "sad" to "Грустное")
val ACTIVITIES = listOf("any" to "Любое", "wake" to "Просыпаюсь", "commute" to "В дороге", "work" to "Работаю", "workout" to "Тренируюсь", "sleep" to "Засыпаю")
val CHARACTER_HINTS = mapOf(
    "favorite" to "Больше того, что вы уже любите и часто дослушиваете.",
    "balanced" to "Любимое вперемешку с новым, похожим на ваш вкус.",
    "discover" to "В основном треки, которые вы ещё не слышали.",
    "forgotten" to "Отмеченное и любимое, что давно не звучало.",
)
private val DEFAULT_TEMPERATURE = mapOf("favorite" to 0.35f, "balanced" to 0.6f, "discover" to 1.0f, "forgotten" to 0.5f)

fun waveSummary(settings: JSONObject?): String {
    if (settings == null) return "Баланс"
    val parts = mutableListOf(CHARACTERS.toMap()[settings.optString("character")] ?: "Баланс")
    MOODS.toMap()[settings.optString("mood")]?.takeIf { settings.optString("mood") != "any" }?.let(parts::add)
    ACTIVITIES.toMap()[settings.optString("activity")]?.takeIf { settings.optString("activity") != "any" }?.let(parts::add)
    if (settings.optBoolean("no_vocals")) parts.add("Без слов")
    return parts.joinToString(" · ")
}

@Composable
fun WaveSettingsPanel(onShake: (() -> Unit)? = null) {
    val state by VolnaPlayer.state.collectAsState()
    LaunchedEffect(Unit) { if (state.settings == null) VolnaPlayer.loadSettings() }
    val s = state.settings ?: JSONObject()
    fun change(key: String, value: Any?) = VolnaPlayer.changeSettings(JSONObject().put(key, value ?: JSONObject.NULL))
    val character = s.optString("character", "balanced")

    GroupLabel("Характер")
    Chips(CHARACTERS, character) { change("character", it) }
    Hint(CHARACTER_HINTS[character].orEmpty())
    Spacer(Modifier.height(16.dp))
    GroupLabel("Настроение")
    Chips(MOODS, s.optString("mood", "any")) { change("mood", it) }
    Spacer(Modifier.height(8.dp))
    ToggleRow("Без слов", s.optBoolean("no_vocals")) { change("no_vocals", it) }
    Spacer(Modifier.height(8.dp))
    GroupLabel("Тонкая настройка")
    val temperature = if (s.isNull("temperature")) DEFAULT_TEMPERATURE[character] ?: 0.6f else s.optDouble("temperature").toFloat()
    SliderRow("Смелость подбора", temperature, 0.1f..1.5f, "предсказуемо", "смело", { "%.2f".format(it) }) { change("temperature", it.toDouble()) }
    SliderRow("Разнообразие", s.optDouble("diversity", 0.5).toFloat(), 0f..1f, "одно звучание", "всё разное", { "${(it * 100).roundToInt()}%" }) {
        change("diversity", it.toDouble())
    }
    val steered = state.kind == "steer" && state.title.isNotEmpty()
    val similarOnly = s.optBoolean("similar_only")
    ToggleRow(if (steered) "Только похожее на «${state.title}»" else "Волна по треку: только похожее", similarOnly) { change("similar_only", it) }
    if (!similarOnly) {
        SliderRow("Волна по треку и плейлисту", s.optDouble("steer", 0.65).toFloat(), 0.1f..0.95f, "больше своего вкуса", "ближе к источнику",
            { "${(it * 100).roundToInt()}% похожего" }) { change("steer", it.toDouble()) }
    }
    ToggleRow("Учитывать время суток и выходные", s.optBoolean("context_aware", true)) { change("context_aware", it) }
    Spacer(Modifier.height(16.dp))
    GroupLabel("Занятие")
    Chips(ACTIVITIES, s.optString("activity", "any")) { change("activity", it) }
    if (onShake != null) {
        Spacer(Modifier.height(12.dp))
        Box(
            Modifier.fillMaxWidth().clip(RoundedCornerShape(50)).border(BorderStroke(1.dp, Palette.Fg), RoundedCornerShape(50))
                .clickable(onClick = onShake).padding(14.dp),
            contentAlignment = Alignment.Center,
        ) { Text("Встряхнуть волну", style = MaterialTheme.typography.titleMedium) }
    }
}
