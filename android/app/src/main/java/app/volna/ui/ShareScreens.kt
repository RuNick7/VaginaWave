package app.volna.ui

import android.content.ClipData
import android.content.ClipboardManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.unit.dp
import app.volna.MainActivity
import app.volna.data.ShareLink
import app.volna.data.Track
import app.volna.player.PlayContext
import app.volna.player.VolnaPlayer
import java.io.IOException
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import kotlinx.coroutines.launch

/** «Поделиться»: ссылка, «Слушать без аккаунта» и отзыв ссылки. */
@Composable
fun ShareSheet(title: String, playlistId: String, onDismiss: () -> Unit) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var link by remember(playlistId) { mutableStateOf<ShareLink?>(null) }
    var failed by remember(playlistId) { mutableStateOf<String?>(null) }
    var copied by remember(playlistId) { mutableStateOf(false) }
    var sure by remember(playlistId) { mutableStateOf(false) } // отзыв — со второго нажатия
    var busy by remember(playlistId) { mutableStateOf(false) }
    LaunchedEffect(playlistId) {
        try {
            link = VolnaPlayer.api.createShare(playlistId)
        } catch (e: IOException) {
            failed = e.message ?: "Сервис VaginaWave недоступен"
        }
    }
    Sheet(onDismiss) {
        Text("Поделиться плейлистом", style = MaterialTheme.typography.titleLarge, modifier = Modifier.padding(vertical = 12.dp))
        val current = link
        when {
            failed != null -> Empty(failed.orEmpty())
            current == null -> Loading()
            else -> {
                Hint("Те, у кого есть аккаунт на этом сервере, смогут слушать плейлист и сохранить его копию себе. Остальным видно только название, обложки и список треков.")
                Spacer(Modifier.height(12.dp))
                SelectionContainer {
                    Text(
                        current.url,
                        style = MaterialTheme.typography.bodyMedium,
                        modifier = Modifier.fillMaxWidth()
                            .border(1.dp, Palette.Faint, RoundedCornerShape(12.dp))
                            .padding(horizontal = 14.dp, vertical = 12.dp),
                    )
                }
                Spacer(Modifier.height(12.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    Pill("Отправить", VIcons.Share, primary = true) { sendLink(context, current.url) }
                    Pill(if (copied) "Скопировано" else "Скопировать") {
                        context.getSystemService(ClipboardManager::class.java)
                            .setPrimaryClip(ClipData.newPlainText(title, current.url))
                        copied = true
                    }
                }
                Spacer(Modifier.height(12.dp))
                ToggleRow("Слушать без аккаунта", current.guests) { on ->
                    if (busy) return@ToggleRow
                    busy = true
                    scope.launch {
                        try {
                            link = VolnaPlayer.api.setShareGuests(playlistId, on)
                        } catch (e: IOException) {
                            VolnaPlayer.say(e.message ?: "Не удалось поменять доступ")
                        } finally {
                            busy = false
                        }
                    }
                }
                Hint(
                    if (current.guests) {
                        "Слушать может любой, у кого есть ссылка. Само выключится ${day(current.guestsUntil)}."
                    } else {
                        "Если включить, любой, у кого есть ссылка, сможет слушать эти треки без входа 30 дней. Это раздача музыки посторонним: включайте для тех, кому доверяете."
                    },
                )
                Text(
                    if (sure) "Точно отозвать? Эта ссылка перестанет работать" else "Отозвать ссылку",
                    style = MaterialTheme.typography.bodyMedium,
                    color = Palette.Muted,
                    textDecoration = TextDecoration.Underline,
                    modifier = Modifier.padding(top = 8.dp).clickable {
                        if (!sure) {
                            sure = true
                            return@clickable
                        }
                        scope.launch {
                            try {
                                VolnaPlayer.api.revokeShare(playlistId)
                                VolnaPlayer.say("Ссылка отозвана")
                                onDismiss()
                            } catch (e: IOException) {
                                VolnaPlayer.say(e.message ?: "Не удалось отозвать ссылку")
                            }
                        }
                    }.padding(vertical = 8.dp),
                )
            }
        }
    }
}

private fun sendLink(context: Context, url: String) {
    val send = Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT, url)
    context.startActivity(
        Intent.createChooser(send, null) // себя не предлагаем: VaginaWave и сам принимает ссылки
            .putExtra(Intent.EXTRA_EXCLUDE_COMPONENTS, arrayOf(ComponentName(context, MainActivity::class.java))),
    )
}

private fun day(seconds: Double): String =
    SimpleDateFormat("d MMMM", Locale.forLanguageTag("ru")).format(Date((seconds * 1000).toLong()))

/** Плейлист по ссылке от другого человека: слушать, волна по нему и своя копия. */
@Composable
fun SharedPlaylistScreen(token: String, nav: Navigator, onMore: (Track) -> Unit) {
    val playing by VolnaPlayer.state.collectAsState()
    val page = load("shared", token) { VolnaPlayer.api.shared(token) }
    var savedId by remember(token) { mutableStateOf<String?>(null) }
    var saving by remember(token) { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    LazyColumn(Modifier.fillMaxSize().statusBarsPadding(), contentPadding = PaddingValues(horizontal = 16.dp)) {
        item { PageHeader("") { nav.back() } }
        when (val s = page.value) {
            Load.Loading -> item { Loading() }
            is Load.Failed -> item { Empty(s.message) }
            is Load.Done -> {
                val shared = s.value
                val p = shared.page
                val context = PlayContext("playlist", shared.playlistId, p.item.title)
                val saved = savedId ?: shared.savedId.ifEmpty { null }
                item {
                    EntityHeader(p, "playlist")
                    Text(
                        "Поделился: ${shared.owner}",
                        style = MaterialTheme.typography.bodyMedium,
                        color = Palette.Muted,
                        textAlign = TextAlign.Center,
                        modifier = Modifier.fillMaxWidth().padding(top = 2.dp),
                    )
                }
                item {
                    PlayActions(p.tracks, context) {
                        Pill("Волна", VIcons.Wave) { VolnaPlayer.startWave("share", token); nav.go(Screen.Home) }
                        if (saved != null) {
                            Pill("В вашей коллекции", VIcons.Check) { nav.go(Screen.Entity("playlist", saved)) }
                        } else {
                            Pill(if (saving) "Сохраняю…" else "Сохранить себе", VIcons.Playlist) {
                                if (saving) return@Pill
                                saving = true
                                scope.launch {
                                    try {
                                        savedId = VolnaPlayer.api.saveShared(token)
                                        VolnaPlayer.say("«${p.item.title}» — в вашей коллекции")
                                    } catch (e: IOException) {
                                        VolnaPlayer.say(e.message ?: "Не удалось сохранить плейлист")
                                    } finally {
                                        saving = false
                                    }
                                }
                            }
                        }
                    }
                }
                itemsIndexed(p.tracks, key = { _, t -> t.id }) { i, track ->
                    TrackRow(track, playing.track?.id == track.id, onMore = onMore) { VolnaPlayer.playList(p.tracks, i, context) }
                }
            }
        }
        item { Spacer(Modifier.height(24.dp)) }
    }
    // своя ссылка: это обычная страница плейлиста
    (page.value as? Load.Done)?.value?.takeIf { it.own }?.let { shared ->
        LaunchedEffect(shared.playlistId) {
            nav.back()
            nav.go(Screen.Entity("playlist", shared.playlistId))
        }
    }
}
