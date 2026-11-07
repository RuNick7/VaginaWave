package app.volna.ui

import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.GridItemSpan
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.clickable
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import androidx.media3.exoplayer.offline.Download
import app.volna.data.Discography
import app.volna.data.Item
import app.volna.data.Mix
import app.volna.data.Page
import app.volna.data.SearchResult
import app.volna.data.Track
import app.volna.player.Downloads
import app.volna.player.Offline
import app.volna.player.PlayContext
import app.volna.player.VolnaPlayer
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.io.IOException

@Composable
fun textFieldColors() = OutlinedTextFieldDefaults.colors(
    focusedBorderColor = Palette.Fg,
    unfocusedBorderColor = Palette.Faint,
    cursorColor = Palette.Fg,
    focusedTextColor = Palette.Fg,
    unfocusedTextColor = Palette.Fg,
    focusedLabelColor = Palette.Fg,
    unfocusedLabelColor = Palette.Muted,
    focusedPlaceholderColor = Palette.Muted,
    unfocusedPlaceholderColor = Palette.Muted,
    focusedContainerColor = Palette.Veil,
    unfocusedContainerColor = Palette.Veil,
)

// --- поиск ---

@Composable
fun SearchScreen(nav: Navigator, onMore: (Track) -> Unit) {
    val playing by VolnaPlayer.state.collectAsState()
    val query = SearchMemory.query
    // вернулись из альбома или с другой вкладки — найденное уже есть, не ищем заново
    var result by remember { mutableStateOf(SearchMemory.result.takeIf { SearchMemory.resultFor == SearchMemory.query.trim() }) }
    var error by remember { mutableStateOf<String?>(null) }
    val focus = LocalFocusManager.current
    LaunchedEffect(query) {
        val q = query.trim()
        if (q.isEmpty()) {
            result = null
            error = null
            return@LaunchedEffect
        }
        if (result != null && SearchMemory.resultFor == q) return@LaunchedEffect
        delay(250)
        try {
            val found = Offline.search(q) // без сети ищет в скачанном
            result = found
            SearchMemory.result = found
            SearchMemory.resultFor = q
            error = null
        } catch (e: java.io.IOException) {
            error = e.message
        }
    }
    fun open(item: Item) {
        SearchHistory.add(item)
        nav.go(Screen.Entity(item.type, item.id))
    }
    LazyColumn(Modifier.fillMaxSize().statusBarsPadding(), contentPadding = PaddingValues(horizontal = 16.dp)) {
        item { PageHeader("Поиск") }
        item {
            OutlinedTextField(
                value = query,
                onValueChange = { SearchMemory.query = it },
                placeholder = { Text("Трек, альбом, артист или плейлист") },
                leadingIcon = { VIcon(VIcons.Search, null, tint = Palette.Muted) },
                trailingIcon = if (query.isNotEmpty()) ({ IconButtonV(VIcons.Close, "Очистить", 36.dp) { SearchMemory.query = "" } }) else null,
                singleLine = true,
                shape = RoundedCornerShape(50),
                colors = textFieldColors(),
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
                keyboardActions = KeyboardActions(onSearch = {
                    SearchHistory.addQuery(query)
                    focus.clearFocus()
                }),
                modifier = Modifier.fillMaxWidth(),
            )
        }
        val r = result
        when {
            query.isBlank() && SearchHistory.entries.isNotEmpty() -> {
                item {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Box(Modifier.weight(1f)) { SectionTitle("История поиска") }
                        Text(
                            "Очистить",
                            style = MaterialTheme.typography.bodyMedium,
                            color = Palette.Muted,
                            modifier = Modifier.padding(top = 12.dp).clickable { SearchHistory.clear() }.padding(8.dp),
                        )
                    }
                }
                items(SearchHistory.entries.toList(), key = { it.key }) { entry ->
                    HistoryRow(entry, onRemove = { SearchHistory.remove(entry) }) {
                        when {
                            entry.track != null -> {
                                SearchHistory.add(entry.track)
                                VolnaPlayer.playList(listOf(entry.track), 0, PlayContext("search", entry.track.title, "Поиск"))
                            }
                            entry.item != null -> open(entry.item)
                            else -> {
                                SearchHistory.addQuery(entry.query)
                                SearchMemory.query = entry.query
                            }
                        }
                    }
                }
            }
            query.isBlank() -> item { Empty("Ищите по названию, артисту или альбому. С артиста можно сразу запустить волну.") }
            error != null -> item { Empty(error.orEmpty()) }
            r == null -> item { Loading() }
            r.tracks.isEmpty() && r.albums.isEmpty() && r.artists.isEmpty() && r.playlists.isEmpty() -> item { Empty("Ничего не нашлось.") }
            else -> {
                item { ItemShelf("Артисты", r.artists, ::open) }
                if (r.tracks.isNotEmpty()) item { SectionTitle("Треки") }
                itemsIndexed(r.tracks, key = { _, t -> "t" + t.id }) { i, track ->
                    TrackRow(track, playing.track?.id == track.id, onMore = onMore) {
                        SearchHistory.add(track)
                        VolnaPlayer.playList(r.tracks, i, PlayContext("search", query, "«$query»"))
                    }
                }
                item { ItemShelf("Альбомы", r.albums, ::open) }
                item { ItemShelf("Плейлисты", r.playlists, ::open) }
            }
        }
        item { Spacer(Modifier.height(24.dp)) }
    }
}

/** Строка истории: запрос или то, что открывали; крестик убирает её из истории. */
@Composable
private fun HistoryRow(entry: SearchEntry, onRemove: () -> Unit, onOpen: () -> Unit) {
    val item = entry.item
    val track = entry.track
    val kind = mapOf("album" to "Альбом", "artist" to "Артист", "playlist" to "Плейлист")
    val (title, subtitle) = when {
        track != null -> track.title to "Трек · ${track.artist}"
        item != null -> item.title to listOfNotNull(kind[item.type], item.subtitle.takeIf { it.isNotEmpty() && item.type != "artist" }).joinToString(" · ")
        else -> entry.query to ""
    }
    Row(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(10.dp)).clickable(onClick = onOpen).padding(vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        if (track != null || item != null) {
            Cover(track?.cover ?: item?.cover.orEmpty(), Modifier.size(44.dp), round = item?.type == "artist", corner = 6.dp)
        } else {
            Box(Modifier.size(44.dp), contentAlignment = Alignment.Center) { VIcon(VIcons.Clock, null, 22.dp, Palette.Muted) }
        }
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.bodyLarge, maxLines = 1, overflow = TextOverflow.Ellipsis)
            if (subtitle.isNotEmpty()) {
                Text(subtitle, style = MaterialTheme.typography.bodySmall, color = Palette.Muted, maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
        }
        IconButtonV(VIcons.Close, "Убрать из истории", 36.dp, onClick = onRemove)
    }
}

// --- коллекция ---

@Composable
fun LibraryScreen(nav: Navigator) {
    val online by Offline.online.collectAsState()
    val saved by Downloads.saved.collectAsState()
    val states by Downloads.states.collectAsState()
    val stats = load("library-stats", online) { VolnaPlayer.api.libraryStats() }
    val newest = load("library-newest", online) { if (online) VolnaPlayer.api.albums("newest").take(12) else Downloads.albums().take(12) }
    val counts = (stats.value as? Load.Done)?.value
    val downloaded = saved.keys.count { states[it] == Download.STATE_COMPLETED }
    LazyColumn(Modifier.fillMaxSize().statusBarsPadding(), contentPadding = PaddingValues(horizontal = 16.dp)) {
        item { PageHeader("Коллекция") }
        if (!online) item { Hint("Нет связи с сервером: играет и находится только скачанное.", Modifier.padding(bottom = 8.dp)) }
        item {
            Column {
                Tile(VIcons.Heart, "Мне нравится", count = counts?.optInt("liked")?.toString().orEmpty()) { nav.go(Screen.LibraryList("liked")) }
                Tile(
                    VIcons.Download,
                    "Скачанное",
                    if (saved.isEmpty()) "Музыка для прослушивания без сети" else "${formatBytes(Downloads.bytes())} на телефоне",
                    count = if (saved.isEmpty()) "" else downloaded.toString(),
                ) { nav.go(Screen.Downloaded) }
                Tile(VIcons.Playlist, "Плейлисты", count = counts?.optInt("playlists")?.toString().orEmpty()) { nav.go(Screen.LibraryList("playlists")) }
                Tile(VIcons.Album, "Альбомы", count = counts?.optInt("albums")?.toString().orEmpty()) { nav.go(Screen.LibraryList("albums")) }
                Tile(VIcons.Artist, "Артисты", count = counts?.optInt("artists")?.toString().orEmpty()) { nav.go(Screen.LibraryList("artists")) }
                Tile(VIcons.Clock, "Недавно слушали") { nav.go(Screen.LibraryList("recent")) }
                Tile(VIcons.Playlist, "Импорт из Spotify", "Плейлист или альбом по ссылке") { nav.go(Screen.Import()) }
                Tile(VIcons.Artist, "Добавить исполнителя", "Скачать дискографию через Lidarr") { nav.go(Screen.AddArtist) }
            }
        }
        item { SectionTitle(if (online) "Недавно добавлены" else "Скачанные альбомы") }
        item {
            when (val s = newest.value) {
                Load.Loading -> Loading()
                is Load.Failed -> Empty("Библиотека недоступна: ${s.message}")
                is Load.Done -> ItemGrid(s.value, columns = 3) { nav.go(Screen.Entity(it.type, it.id)) }
            }
        }
        item { Spacer(Modifier.height(24.dp)) }
    }
}

private val ALBUM_SORTS = listOf("alpha" to "А–Я", "newest" to "Новые", "recent" to "Недавно слушали", "frequent" to "Часто", "year" to "По году", "liked" to "Любимые")
private val ARTIST_SORTS = listOf("alpha" to "А–Я", "frequent" to "Часто слушаете", "liked" to "Любимые")

@Composable
fun LibraryListScreen(kind: String, nav: Navigator, onMore: (Track) -> Unit) {
    when (kind) {
        "liked", "recent" -> TrackListScreen(kind, nav, onMore)
        else -> GridListScreen(kind, nav)
    }
}

@Composable
private fun TrackListScreen(kind: String, nav: Navigator, onMore: (Track) -> Unit) {
    val title = if (kind == "liked") "Мне нравится" else "Недавно слушали"
    val playing by VolnaPlayer.state.collectAsState()
    val tracks = load(kind) { if (kind == "liked") Offline.liked() else VolnaPlayer.api.recent() }
    var query by rememberSaveable(kind) { mutableStateOf("") }
    LazyColumn(Modifier.fillMaxSize().statusBarsPadding(), contentPadding = PaddingValues(horizontal = 16.dp)) {
        item { PageHeader(title) { nav.back() } }
        item { FilterField(query, "Найти в «$title»") { query = it } }
        when (val s = tracks.value) {
            Load.Loading -> item { Loading() }
            is Load.Failed -> item { Empty(s.message) }
            is Load.Done -> {
                val list = s.value.filter { matches(query, it.title, it.artist, it.album) }
                val context = PlayContext("library", kind, title)
                if (s.value.isEmpty()) item { Empty(if (kind == "liked") "Отмечайте треки сердцем — они соберутся здесь." else "История пока пуста.") }
                else if (list.isEmpty()) item { Empty("Ничего не нашлось.") }
                else item { PlayActions(list, context) { if (kind == "liked") LikedDownloadPill(s.value) } }
                itemsIndexed(list, key = { _, t -> t.id }) { i, track ->
                    TrackRow(track, playing.track?.id == track.id, onMore = onMore) { VolnaPlayer.playList(list, i, context) }
                }
            }
        }
        item { Spacer(Modifier.height(24.dp)) }
    }
}

@Composable
private fun GridListScreen(kind: String, nav: Navigator) {
    val title = mapOf("albums" to "Альбомы", "artists" to "Артисты", "playlists" to "Плейлисты")[kind] ?: kind
    val sorts = when (kind) {
        "albums" -> ALBUM_SORTS
        "artists" -> ARTIST_SORTS
        else -> emptyList()
    }
    var sort by rememberSaveable(kind) { mutableStateOf("alpha") }
    var query by rememberSaveable(kind) { mutableStateOf("") }
    val items = load(kind, sort) {
        when (kind) {
            "albums" -> VolnaPlayer.api.albums(sort)
            "artists" -> VolnaPlayer.api.artists(sort)
            else -> VolnaPlayer.api.playlists()
        }
    }
    LazyVerticalGrid(
        GridCells.Adaptive(140.dp),
        Modifier.fillMaxSize().statusBarsPadding(),
        contentPadding = PaddingValues(horizontal = 16.dp, vertical = 0.dp),
        horizontalArrangement = Arrangement.spacedBy(14.dp),
        verticalArrangement = Arrangement.spacedBy(20.dp),
    ) {
        item(span = { GridItemSpan(maxLineSpan) }) { PageHeader(title) { nav.back() } }
        if (kind == "playlists") {
            item(span = { GridItemSpan(maxLineSpan) }) {
                Row(Modifier.padding(bottom = 12.dp)) { Pill("Импорт из Spotify", VIcons.Playlist) { nav.go(Screen.Import()) } }
            }
        }
        item(span = { GridItemSpan(maxLineSpan) }) { FilterField(query, "Найти в «$title»") { query = it } }
        if (sorts.isNotEmpty()) item(span = { GridItemSpan(maxLineSpan) }) { Chips(sorts, sort) { sort = it } }
        when (val s = items.value) {
            Load.Loading -> item(span = { GridItemSpan(maxLineSpan) }) { Loading() }
            is Load.Failed -> item(span = { GridItemSpan(maxLineSpan) }) { Empty(s.message) }
            is Load.Done -> {
                val found = s.value.filter { matches(query, it.title, it.subtitle) }
                if (s.value.isEmpty()) item(span = { GridItemSpan(maxLineSpan) }) { Empty("Здесь пока пусто.") }
                else if (found.isEmpty()) item(span = { GridItemSpan(maxLineSpan) }) { Empty("Ничего не нашлось.") }
                items(found, key = { it.type + it.id }) { item -> ItemCard(item) { nav.go(Screen.Entity(item.type, item.id)) } }
            }
        }
        item(span = { GridItemSpan(maxLineSpan) }) { Spacer(Modifier.height(24.dp)) }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun PlayActions(tracks: List<Track>, context: PlayContext, extra: @Composable () -> Unit = {}) {
    FlowRow(
        Modifier.padding(vertical = 12.dp),
        horizontalArrangement = Arrangement.spacedBy(10.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Pill("Слушать", VIcons.Play, primary = true) { VolnaPlayer.playList(tracks, 0, context) }
        Pill("Перемешать", VIcons.Shuffle) { VolnaPlayer.shuffle(tracks, context) }
        extra()
    }
}

// --- альбом, артист, плейлист ---

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun EntityScreen(type: String, id: String, nav: Navigator, onMore: (Track) -> Unit) {
    val playing by VolnaPlayer.state.collectAsState()
    val page = load(type, id) { Offline.page(type, id) } // без сети — из скачанного
    var liked by remember(type, id) { mutableStateOf<Boolean?>(null) }
    var allTracks by rememberSaveable(type, id) { mutableStateOf(false) }
    var allAlbums by rememberSaveable(type, id) { mutableStateOf(false) }
    var allLiked by remember(type, id) { mutableStateOf(false) } // «Лайкнуть все» уже нажато
    var sharing by remember(type, id) { mutableStateOf(false) }
    val online by Offline.online.collectAsState()
    val scope = rememberCoroutineScope()
    LazyColumn(Modifier.fillMaxSize().statusBarsPadding(), contentPadding = PaddingValues(horizontal = 16.dp)) {
        item { PageHeader("") { nav.back() } }
        when (val s = page.value) {
            Load.Loading -> item { Loading() }
            is Load.Failed -> item { Empty(s.message) }
            is Load.Done -> {
                val p: Page = s.value
                val isLiked = liked ?: p.item.liked
                item { EntityHeader(p, type) }
                val context = PlayContext(type, id, p.item.title)
                // у артиста — все треки по популярности: сначала пять, остальные по кнопке
                val tracks = (if (type == "artist") p.tracks.ifEmpty { p.top } else p.tracks)
                    .let { list -> if (allLiked) list.map { it.copy(liked = true) } else list }
                item {
                    if (type == "artist") {
                        FlowRow(Modifier.padding(vertical = 12.dp), horizontalArrangement = Arrangement.spacedBy(10.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                            Pill("Волна по артисту", VIcons.Wave, primary = true) { VolnaPlayer.startWave("artist", id); nav.go(Screen.Home) }
                            Pill("Популярное", VIcons.Play) { VolnaPlayer.playList(tracks, 0, context) }
                            Pill("Плейлист", VIcons.Mix) { nav.go(Screen.Mix("artist", id)) }
                            LikePill(isLiked) {
                                liked = !isLiked
                                scope.launch { runCatching { VolnaPlayer.api.like(type, id, !isLiked) }.onFailure { liked = isLiked } }
                            }
                        }
                    } else {
                        PlayActions(tracks, context) {
                            Pill("Волна", VIcons.Wave) { VolnaPlayer.startWave(type, id); nav.go(Screen.Home) }
                            if (type == "playlist" && online) Pill("Поделиться", VIcons.Share) { sharing = true }
                            if (tracks.any { !it.liked } && !allLiked) {
                                Pill("Лайкнуть все", VIcons.Heart) {
                                    allLiked = true // сердечки сразу, Navidrome — следом
                                    scope.launch {
                                        runCatching { VolnaPlayer.api.likeTracks(tracks.map { it.id }) }
                                            .onSuccess {
                                                Downloads.liked(tracks, true)
                                                VolnaPlayer.say("Все ${tracks.size} ${plural(tracks.size, "трек", "трека", "треков")} в «Мне нравится»")
                                            }
                                            .onFailure { allLiked = false; VolnaPlayer.say(it.message ?: "Не удалось отметить треки") }
                                    }
                                }
                            }
                            DownloadPill(p.item, tracks)
                            if (type == "album") {
                                LikePill(isLiked) {
                                    liked = !isLiked
                                    scope.launch { runCatching { VolnaPlayer.api.like(type, id, !isLiked) }.onFailure { liked = isLiked } }
                                }
                            }
                        }
                    }
                }
                if (type == "artist") item { DiscographyRow(id) }
                if (type == "artist" && tracks.isNotEmpty()) item { SectionTitle("Популярные треки") }
                val shown = if (type == "artist" && !allTracks) tracks.take(ARTIST_TRACKS) else tracks
                itemsIndexed(shown, key = { _, t -> t.id }) { i, track ->
                    TrackRow(
                        track,
                        playing.track?.id == track.id,
                        number = if (type == "album") (track.trackNo.takeIf { it > 0 } ?: (i + 1)) else null,
                        showCover = type != "album",
                        onMore = onMore,
                    ) { VolnaPlayer.playList(tracks, i, context) }
                }
                if (type == "artist" && tracks.size > ARTIST_TRACKS) {
                    item { ShowMore(allTracks, "Все треки · ${tracks.size}") { allTracks = !allTracks } }
                }
                if (type == "artist" && p.albums.isNotEmpty()) {
                    item { SectionTitle("Альбомы") }
                    item {
                        ItemGrid(if (allAlbums) p.albums else p.albums.take(ARTIST_ALBUMS), columns = 2) { nav.go(Screen.Entity(it.type, it.id)) }
                    }
                    if (p.albums.size > ARTIST_ALBUMS) item { ShowMore(allAlbums, "Все альбомы · ${p.albums.size}") { allAlbums = !allAlbums } }
                }
            }
        }
        item { Spacer(Modifier.height(24.dp)) }
    }
    if (sharing) {
        ShareSheet((page.value as? Load.Done)?.value?.item?.title.orEmpty(), id) { sharing = false }
    }
}

/** Дискография артиста через Lidarr: кнопка «Скачать» или сколько альбомов уже скачано. */
@Composable
private fun DiscographyRow(artistId: String) {
    val loaded = load("discography", artistId) { VolnaPlayer.api.discography(artistId) }
    var state by remember(artistId) { mutableStateOf<Discography?>(null) }
    var busy by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    val d = state ?: (loaded.value as? Load.Done)?.value ?: return
    if (!d.available) return
    if (d.following) {
        Hint(
            if (d.albums == 0) "Дискография на докачке: Lidarr уточняет список альбомов"
            else "Дискография на докачке: есть ${d.have} из ${d.albums} ${plural(d.albums, "альбома", "альбомов", "альбомов")}",
            Modifier.padding(bottom = 4.dp),
        )
        return
    }
    Row(Modifier.padding(bottom = 4.dp)) {
        Pill(if (busy) "Ставлю на докачку…" else "Скачать дискографию", VIcons.Album) {
            if (busy) return@Pill
            busy = true
            scope.launch {
                try {
                    state = VolnaPlayer.api.downloadDiscography(artistId)
                    VolnaPlayer.say("Дискография поставлена на докачку")
                } catch (e: IOException) {
                    VolnaPlayer.say(e.message ?: "Lidarr не принял исполнителя")
                } finally {
                    busy = false
                }
            }
        }
    }
}

private const val ARTIST_TRACKS = 5
private const val ARTIST_ALBUMS = 4

/** «Показать все · N» / «Свернуть» под длинным списком. */
@Composable
private fun ShowMore(expanded: Boolean, label: String, onClick: () -> Unit) {
    Box(Modifier.fillMaxWidth().padding(top = 8.dp), contentAlignment = Alignment.Center) {
        Pill(if (expanded) "Свернуть" else label, onClick = onClick)
    }
}

/** Скачать альбом или плейлист целиком; потом — сколько уже на телефоне, нажатие удаляет. */
@Composable
private fun DownloadPill(item: Item, tracks: List<Track>) {
    val lists by Downloads.lists.collectAsState()
    val states by Downloads.states.collectAsState()
    if (tracks.isEmpty()) return
    if ("${item.type}:${item.id}" !in lists) {
        Pill("Скачать", VIcons.Download) {
            Downloads.saveList(item, tracks)
            VolnaPlayer.say(Downloads.queued(item.title))
        }
        return
    }
    val done = tracks.count { states[it.id] == Download.STATE_COMPLETED }
    Pill(if (done >= tracks.size) "Скачано" else "Скачано $done из ${tracks.size}", VIcons.Downloaded) {
        Downloads.removeList(item.type, item.id)
        VolnaPlayer.say("«${item.title}» удалён с телефона")
    }
}

/** «Мне нравится»: включить загрузку всех лайков — и новые будут качаться сами. */
@Composable
private fun LikedDownloadPill(liked: List<Track>) {
    val saved by Downloads.saved.collectAsState()
    val states by Downloads.states.collectAsState()
    var on by remember { mutableStateOf(Offline.likedDownloads) }
    if (!on) {
        Pill("Скачать", VIcons.Download) {
            on = true
            Offline.setLikedDownloads(true, liked)
            VolnaPlayer.say("«Мне нравится» скачивается, новые лайки тоже будут скачиваться")
        }
        return
    }
    val mine = saved.values.filter { Downloads.LIKED in it.reasons }
    val done = mine.count { states[it.track.id] == Download.STATE_COMPLETED }
    Pill(if (done >= mine.size) "Скачано" else "Скачано $done из ${mine.size}", VIcons.Downloaded) {
        on = false
        Offline.setLikedDownloads(false)
        VolnaPlayer.say("«Мне нравится» больше не скачивается")
    }
}

/** Поиск внутри раздела коллекции: фильтрует то, что уже загружено, сразу при вводе. */
@Composable
fun FilterField(query: String, placeholder: String, onChange: (String) -> Unit) {
    OutlinedTextField(
        value = query,
        onValueChange = onChange,
        singleLine = true,
        placeholder = { Text(placeholder, maxLines = 1, overflow = TextOverflow.Ellipsis) },
        leadingIcon = { VIcon(VIcons.Search, null, 20.dp, Palette.Muted) },
        trailingIcon = if (query.isNotEmpty()) ({ IconButtonV(VIcons.Close, "Очистить", 36.dp) { onChange("") } }) else null,
        keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search, autoCorrectEnabled = false),
        colors = textFieldColors(),
        shape = RoundedCornerShape(50),
        modifier = Modifier.fillMaxWidth().padding(bottom = 12.dp),
    )
}

fun matches(query: String, vararg fields: String): Boolean {
    val q = query.trim().lowercase().replace('ё', 'е')
    return q.isEmpty() || fields.any { it.lowercase().replace('ё', 'е').contains(q) }
}

@Composable
private fun LikePill(liked: Boolean, onClick: () -> Unit) {
    Box(
        Modifier.border(1.dp, if (liked) Palette.Fg else Palette.Faint, RoundedCornerShape(50)),
    ) { IconButtonV(if (liked) VIcons.HeartFilled else VIcons.Heart, "Нравится", 42.dp, Palette.Fg, onClick) }
}

@Composable
fun EntityHeader(p: Page, type: String) {
    val kind = mapOf("album" to "Альбом", "artist" to "Артист", "playlist" to "Плейлист")[type].orEmpty()
    val meta = when (type) {
        "artist" -> listOfNotNull(p.item.subtitle.ifEmpty { null }, p.plays.takeIf { it > 0 }?.let { "$it ${plural(it, "прослушивание", "прослушивания", "прослушиваний")}" })
        else -> listOfNotNull(
            p.item.year.takeIf { it > 0 }?.toString(),
            "${p.tracks.size} ${plural(p.tracks.size, "трек", "трека", "треков")}",
            longDuration(p.duration),
        )
    }
    Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally) {
        Cover(p.item.cover, Modifier.widthIn(max = 240.dp).fillMaxWidth(0.6f), round = type == "artist")
        Spacer(Modifier.height(16.dp))
        Text(kind.uppercase(), style = MaterialTheme.typography.labelSmall, color = Palette.Muted)
        Text(p.item.title, style = MaterialTheme.typography.headlineMedium, modifier = Modifier.padding(top = 6.dp))
        if (type == "album" && p.item.subtitle.isNotEmpty()) {
            val nav = LocalNav.current
            Text(
                p.item.subtitle,
                style = MaterialTheme.typography.bodyLarge,
                modifier = Modifier.padding(top = 4.dp).then(
                    if (nav != null && p.item.artistId.isNotEmpty()) Modifier.clickable { nav.go(Screen.Entity("artist", p.item.artistId)) } else Modifier,
                ),
            )
        }
        Text(meta.joinToString(" · "), style = MaterialTheme.typography.bodyMedium, color = Palette.Muted, modifier = Modifier.padding(top = 4.dp))
    }
}

// --- плейлист по треку или артисту ---

@Composable
fun MixScreen(type: String, id: String, nav: Navigator, onMore: (Track) -> Unit) {
    val playing by VolnaPlayer.state.collectAsState()
    var size by rememberSaveable { mutableIntStateOf(25) }
    var rebuild by remember { mutableIntStateOf(0) }
    val mix = load(type, id, size, rebuild) { VolnaPlayer.api.mix(type, id, size) }
    var saving by remember { mutableStateOf(false) }
    var name by remember { mutableStateOf("") }
    val scope = rememberCoroutineScope()
    LazyColumn(Modifier.fillMaxSize().statusBarsPadding(), contentPadding = PaddingValues(horizontal = 16.dp)) {
        item { PageHeader("") { nav.back() } }
        when (val s = mix.value) {
            Load.Loading -> item { Loading() }
            is Load.Failed -> item { Empty(s.message) }
            is Load.Done -> {
                val m: Mix = s.value
                val context = PlayContext("other", "mix:$type:$id", m.title)
                item {
                    Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally) {
                        Cover(m.tracks.firstOrNull()?.cover.orEmpty(), Modifier.widthIn(max = 240.dp).fillMaxWidth(0.6f))
                        Spacer(Modifier.height(16.dp))
                        Text(if (type == "artist") "ПЛЕЙЛИСТ ПО АРТИСТУ" else "ПЛЕЙЛИСТ ПО ТРЕКУ", style = MaterialTheme.typography.labelSmall, color = Palette.Muted)
                        Text(m.title, style = MaterialTheme.typography.headlineMedium, modifier = Modifier.padding(top = 6.dp))
                        Text(
                            "${m.tracks.size} ${plural(m.tracks.size, "трек", "трека", "треков")} · ${longDuration(m.tracks.sumOf { it.duration })}",
                            style = MaterialTheme.typography.bodyMedium, color = Palette.Muted, modifier = Modifier.padding(top = 4.dp),
                        )
                        Hint("Похожее на источник с поправкой на ваш вкус: без нелюбимого и пропускаемого.")
                    }
                }
                item {
                    PlayActions(m.tracks, context) {
                        Pill("Сохранить", VIcons.Mix) {
                            if (name.isEmpty()) name = m.title
                            saving = !saving
                        }
                        Pill("Пересобрать", VIcons.Sync) { rebuild++ }
                    }
                }
                if (saving) {
                    item {
                        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                            OutlinedTextField(
                                value = name, onValueChange = { name = it }, singleLine = true,
                                label = { Text("Название") }, colors = textFieldColors(), modifier = Modifier.weight(1f),
                            )
                            Pill("Сохранить", primary = true) {
                                scope.launch {
                                    try {
                                        val saved = VolnaPlayer.api.createPlaylist(name.trim(), m.tracks.map { it.id })
                                        VolnaPlayer.say("Плейлист «${saved.optString("name")}» сохранён в Navidrome")
                                        saving = false
                                        saved.optString("id").takeIf { it.isNotEmpty() }?.let { nav.go(Screen.Entity("playlist", it)) }
                                    } catch (e: java.io.IOException) {
                                        VolnaPlayer.say(e.message ?: "Не удалось сохранить")
                                    }
                                }
                            }
                        }
                    }
                }
                item {
                    Box(Modifier.padding(bottom = 8.dp)) {
                        Chips(listOf("15" to "15 треков", "25" to "25", "50" to "50"), size.toString()) { size = it.toInt() }
                    }
                }
                itemsIndexed(m.tracks, key = { _, t -> t.id }) { i, track ->
                    TrackRow(track, playing.track?.id == track.id, onMore = onMore) { VolnaPlayer.playList(m.tracks, i, context) }
                }
            }
        }
        item { Spacer(Modifier.height(24.dp)) }
    }
}
