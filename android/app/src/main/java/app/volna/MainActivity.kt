package app.volna

import android.Manifest
import android.app.Activity
import android.content.ComponentName
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveableStateHolder
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.media3.session.MediaController
import androidx.media3.session.SessionToken
import app.volna.data.Track
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.width
import app.volna.player.Devices
import app.volna.player.Offline
import app.volna.player.RemoteDevice
import app.volna.player.PlaybackService
import app.volna.player.VolnaPlayer
import app.volna.ui.ConnectScreen
import app.volna.ui.DownloadedScreen
import app.volna.ui.Pill
import app.volna.ui.VIcon
import app.volna.ui.VIcons
import app.volna.ui.AddArtistScreen
import app.volna.ui.CoverStyle
import app.volna.ui.EntityScreen
import app.volna.ui.HomeScreen
import app.volna.ui.ImportScreen
import app.volna.ui.LibraryListScreen
import app.volna.ui.LibraryScreen
import app.volna.ui.LocalNav
import app.volna.ui.MiniPlayer
import app.volna.ui.LyricsFull
import app.volna.ui.LyricsScreen
import app.volna.ui.MixScreen
import app.volna.ui.Onboarding
import app.volna.ui.OnboardingScreen
import app.volna.ui.Navigator
import app.volna.ui.Palette
import app.volna.ui.QueueScreen
import app.volna.ui.Screen
import app.volna.ui.SearchScreen
import app.volna.ui.SettingsPage
import app.volna.ui.SettingsScreen
import app.volna.ui.SharedPlaylistScreen
import app.volna.ui.Sheet
import app.volna.ui.TabBar
import app.volna.ui.Topo
import app.volna.ui.TrackActions
import app.volna.ui.UpdateSheet
import app.volna.ui.UiPrefs
import app.volna.ui.VolnaTheme
import app.volna.ui.WaveSettingsPanel
import com.google.common.util.concurrent.ListenableFuture
import kotlinx.coroutines.delay

class MainActivity : ComponentActivity() {
    private var controller: ListenableFuture<MediaController>? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        val app = application as VolnaApp
        CoverStyle.color = app.prefs.colorCovers
        UiPrefs.background = app.prefs.background
        UiPrefs.react = app.prefs.react
        UiPrefs.lyrics = app.prefs.lyrics
        if (savedInstanceState == null) takeShared(intent) // после поворота экрана заново не открываем
        setContent {
            VolnaTheme {
                val needLogin by app.api.needLogin.collectAsState()
                if (needLogin) {
                    ConnectScreen()
                } else {
                    Box(Modifier.fillMaxSize()) {
                        VolnaRoot()
                        if (LyricsFull.show) LyricsScreen { LyricsFull.show = false }
                        if (Onboarding.show) OnboardingScreen { Onboarding.show = false }
                    }
                }
                if (Updates.sheet) UpdateSheet { Updates.sheet = false }
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        takeShared(intent)
    }

    override fun onResume() {
        super.onResume()
        Devices.visible = true
        Devices.refresh() // не играет ли что-то на другом устройстве
        Updates.resume() // вернулись из настроек, где разрешили установку, — ставим обновление
    }

    /**
     * Откуда пришла ссылка: «Открыть в приложении» со страницы плейлиста; «Поделиться → VaginaWave»
     * с такой же ссылкой или ссылкой на плейлист Spotify и Apple Music (её откроет импорт).
     */
    private fun takeShared(intent: Intent?) {
        if (intent?.action == Updates.ACTION) Updates.sheet = true // нажали на уведомление об обновлении
        if (intent?.action == Intent.ACTION_VIEW && intent.data?.scheme == "vaginawave") {
            Shared.playlist = intent.data?.lastPathSegment
            return
        }
        if (intent?.action != Intent.ACTION_SEND) return
        val url = Regex("""https://\S+""").find(intent.getStringExtra(Intent.EXTRA_TEXT).orEmpty())?.value
        val token = url?.let { Regex("""/s/([\w-]+)/?$""").find(it)?.groupValues?.get(1) }
        if (token != null) Shared.playlist = token else Shared.url = url
    }

    override fun onStart() {
        super.onStart()
        // подключаемся к сервису воспроизведения: так музыка продолжит играть в фоне и в уведомлении
        val token = SessionToken(this, ComponentName(this, PlaybackService::class.java))
        controller = MediaController.Builder(this, token).buildAsync()
    }

    override fun onPause() {
        Devices.visible = false
        super.onPause()
    }

    override fun onStop() {
        controller?.let(MediaController::releaseFuture)
        controller = null
        super.onStop()
    }
}

@Composable
private fun VolnaRoot() {
    val nav = remember { Navigator() }
    val holder = rememberSaveableStateHolder()
    val kept = remember { mutableSetOf<String>() }
    val alive = nav.stack.mapIndexed { i, screen -> "$i:$screen" }
    LaunchedEffect(alive) { // экраны, снятые со стека, забываем
        kept += alive
        kept.filter { it !in alive }.forEach {
            holder.removeState(it)
            kept -= it
        }
    }
    val state by VolnaPlayer.state.collectAsState()
    var actionsFor by remember { mutableStateOf<Track?>(null) }
    var tuneOpen by remember { mutableStateOf(false) }
    val activity = LocalContext.current as? Activity
    LaunchedEffect(Unit) { // первый вход в Volna: сначала любимые артисты и предпочтения
        Offline.sync() // что держать скачанным: «Мне нравится», запас (не чаще раза в 6 часов)
        if (!Offline.isOnline) return@LaunchedEffect
        runCatching { VolnaPlayer.api.onboarding() }.onSuccess { if (!it.done) Onboarding.show = true }
    }
    LaunchedEffect(Unit) { // нашлась новая версия — предложим один раз, дальше она ждёт в «О приложении»
        Updates.available.collect { if (it != null) Updates.offer() }
    }
    LaunchedEffect(Shared.url) {
        Shared.url?.let { nav.go(Screen.Import(it)) }
        Shared.url = null
    }
    LaunchedEffect(Shared.playlist) {
        Shared.playlist?.let { nav.go(Screen.SharedPlaylist(it)) }
        Shared.playlist = null
    }

    // «назад» на корне главной сворачивает приложение, а не закрывает: музыка продолжает играть
    BackHandler { if (!nav.back()) activity?.moveTaskToBack(true) }
    AskNotifications()

    CompositionLocalProvider(LocalNav provides nav) { Box(Modifier.fillMaxSize().background(Palette.Bg)) {
        Topo(Modifier.fillMaxSize(), mode = UiPrefs.background, dim = nav.current != Screen.Home, playing = state.playing)
        Column(Modifier.fillMaxSize()) {
            Box(Modifier.weight(1f)) {
                // у каждого экрана в стеке своё сохранённое состояние: «назад» возвращает прокрутку,
                // фильтры и поиск такими, какими их оставили
                holder.SaveableStateProvider("${nav.stack.lastIndex}:${nav.current}") { when (val screen = nav.current) {
                    Screen.Home -> HomeScreen(nav, onMore = { actionsFor = it }, onTune = { tuneOpen = true })
                    Screen.Search -> SearchScreen(nav) { actionsFor = it }
                    Screen.Library -> LibraryScreen(nav)
                    Screen.Settings -> SettingsScreen(nav)
                    is Screen.LibraryList -> LibraryListScreen(screen.kind, nav) { actionsFor = it }
                    is Screen.Entity -> EntityScreen(screen.type, screen.id, nav) { actionsFor = it }
                    is Screen.SharedPlaylist -> SharedPlaylistScreen(screen.token, nav) { actionsFor = it }
                    is Screen.Mix -> MixScreen(screen.type, screen.id, nav) { actionsFor = it }
                    Screen.Queue -> QueueScreen(nav) { actionsFor = it }
                    is Screen.Import -> ImportScreen(screen.url, nav)
                    Screen.AddArtist -> AddArtistScreen(nav)
                    Screen.Downloaded -> DownloadedScreen(nav) { actionsFor = it }
                    is Screen.SettingsPage -> SettingsPage(screen.key, nav)
                } }
            }
            RemoteBar()
            OfflineBar { nav.go(Screen.Downloaded) }
            if (nav.current != Screen.Home && state.track != null) MiniPlayer { nav.go(Screen.Home) }
            TabBar(nav.tab) { nav.tab(it) }
        }
        Toast(state.message, Modifier.align(Alignment.BottomCenter).padding(bottom = 150.dp))
    } }

    actionsFor?.let { track -> TrackActions(track, onDismiss = { actionsFor = null }, navigate = nav::go) }
    if (tuneOpen) {
        Sheet(onDismiss = { tuneOpen = false }) {
            Text("Настроить волну", style = MaterialTheme.typography.titleLarge, modifier = Modifier.padding(vertical = 12.dp))
            WaveSettingsPanel(onShake = {
                tuneOpen = false
                VolnaPlayer.shake()
            })
        }
    }
}

/** «Играет на X»: на другом устройстве идёт музыка, а здесь тихо — можно забрать её сюда. */
@Composable
private fun RemoteBar() {
    val others by Devices.others.collectAsState()
    val state by VolnaPlayer.state.collectAsState()
    val device: RemoteDevice = others.firstOrNull { it.playing && it.track != null } ?: return
    val track = device.track ?: return
    if (state.playing) return
    Row(
        Modifier.fillMaxWidth().background(Palette.Veil).padding(start = 16.dp, end = 12.dp, top = 8.dp, bottom = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        VIcon(VIcons.Playback, null, 20.dp, Palette.Muted)
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text("Играет на «${device.name}»", style = MaterialTheme.typography.bodySmall, color = Palette.Muted, maxLines = 1)
            Text(
                "${track.title} · ${track.artist}",
                style = MaterialTheme.typography.bodyMedium,
                fontWeight = FontWeight.SemiBold,
                maxLines = 1,
                overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis,
            )
        }
        Spacer(Modifier.width(8.dp))
        Pill("Слушать здесь", primary = true) { Devices.takeOver(device) }
    }
}

/** Полоска над мини-плеером, пока нет связи или включён офлайн-режим. */
@Composable
private fun OfflineBar(onOpen: () -> Unit) {
    val online by Offline.online.collectAsState()
    val forced by Offline.forced.collectAsState()
    if (online) return
    Row(
        Modifier.fillMaxWidth().background(Palette.Veil).clickable(onClick = onOpen).padding(horizontal = 16.dp, vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.Center,
    ) {
        VIcon(VIcons.Offline, null, 16.dp, Palette.Muted)
        Spacer(Modifier.width(8.dp))
        Text(
            if (forced) "Офлайн-режим · играет скачанное" else "Нет связи · играет скачанное",
            style = MaterialTheme.typography.bodySmall,
            color = Palette.Muted,
        )
    }
}

/** Ссылка, которой поделились с Volna: url подхватит экран импорта, playlist — токен плейлиста по ссылке. */
object Shared {
    var url by mutableStateOf<String?>(null)
    var playlist by mutableStateOf<String?>(null)
}

@Composable
private fun Toast(message: String?, modifier: Modifier) {
    if (message == null) return
    LaunchedEffect(message) {
        delay(3500)
        VolnaPlayer.consumeMessage()
    }
    Text(
        message,
        style = MaterialTheme.typography.bodyMedium,
        fontWeight = FontWeight.SemiBold,
        color = Palette.Bg,
        textAlign = TextAlign.Center,
        modifier = modifier
            .padding(horizontal = 16.dp)
            .clip(RoundedCornerShape(50))
            .background(Palette.Fg)
            .padding(horizontal = 18.dp, vertical = 10.dp),
    )
}

/** Android 13+: без разрешения на уведомления не будет управления музыкой в шторке. */
@Composable
private fun AskNotifications() {
    if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return
    val context = LocalContext.current
    val launcher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {}
    LaunchedEffect(Unit) {
        val granted = ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED
        if (!granted) launcher.launch(Manifest.permission.POST_NOTIFICATIONS)
    }
}
