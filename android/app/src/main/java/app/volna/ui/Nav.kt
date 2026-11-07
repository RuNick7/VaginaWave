package app.volna.ui

import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import org.json.JSONException
import java.io.IOException

sealed interface Screen {
    data object Home : Screen
    data object Search : Screen
    data object Library : Screen
    data object Settings : Screen
    data class LibraryList(val kind: String) : Screen // liked | recent | albums | artists | playlists
    data class Entity(val type: String, val id: String) : Screen // album | artist | playlist
    data class SharedPlaylist(val token: String) : Screen // плейлист по чужой ссылке
    data class Mix(val type: String, val id: String) : Screen // плейлист по треку или артисту
    data class SettingsPage(val key: String) : Screen
    data object Queue : Screen
    data class Import(val url: String = "") : Screen // плейлист из Spotify или Apple Music по ссылке
    data object AddArtist : Screen // исполнитель из MusicBrainz на докачку через Lidarr
    data object Downloaded : Screen // скачанное для прослушивания без сети
}

private fun Screen.tab(): String? = when (this) {
    Screen.Home -> "home"
    Screen.Search -> "search"
    Screen.Library -> "library"
    Screen.Settings -> "settings"
    else -> null
}

/** Простой стек экранов: вкладка — корень, остальное кладётся сверху; «назад» снимает верхний. */
class Navigator {
    val stack = mutableStateListOf<Screen>(Screen.Home)
    val current: Screen get() = stack.last()
    val tab: String get() = stack.first().tab() ?: "home"

    fun go(screen: Screen) {
        if (screen.tab() != null) {
            stack.clear()
            stack.add(screen)
        } else {
            stack.add(screen)
        }
    }

    fun tab(key: String) = go(
        when (key) {
            "search" -> Screen.Search
            "library" -> Screen.Library
            "settings" -> Screen.Settings
            else -> Screen.Home
        },
    )

    /** true — «назад» обработан внутри приложения, false — пора выходить. */
    fun back(): Boolean {
        if (stack.size > 1) {
            stack.removeAt(stack.lastIndex)
            return true
        }
        if (stack.first() != Screen.Home) {
            go(Screen.Home)
            return true
        }
        return false
    }
}

sealed interface Load<out T> {
    data object Loading : Load<Nothing>
    data class Done<T>(val value: T) : Load<T>
    data class Failed(val message: String) : Load<Nothing>
}

class Loaded<T>(val value: Load<T>, val reload: () -> Unit)

/** Загрузить данные с сервиса при появлении экрана (и заново при смене ключей или reload()). */
@Composable
fun <T> load(vararg keys: Any?, fetch: suspend () -> T): Loaded<T> {
    var version by remember { mutableIntStateOf(0) }
    val state = produceState<Load<T>>(Load.Loading, *keys, version) {
        value = Load.Loading
        value = try {
            Load.Done(fetch())
        } catch (e: IOException) {
            Load.Failed(e.message?.takeIf { it.isNotBlank() } ?: "Сервис VaginaWave недоступен")
        } catch (e: JSONException) {
            Load.Failed("Сервис ответил неожиданно: обновите сервис VaginaWave на сервере")
        }
    }
    return Loaded(state.value) { version++ }
}
