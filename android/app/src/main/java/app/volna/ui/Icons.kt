package app.volna.ui

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.PathFillType
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.vector.addPathNodes
import androidx.compose.ui.unit.dp

/** Линейные иконки 24×24 с обводкой 1.5 — те же контуры, что в веб-версии (web/js/dom.js). */
object VIcons {
    private fun circle(cx: Float, cy: Float, r: Float) =
        "M${cx - r} ${cy}a$r $r 0 1 0 ${2 * r} 0a$r $r 0 1 0 ${-2 * r} 0"

    private fun icon(name: String, vararg paths: String, filled: Set<Int> = emptySet()): ImageVector {
        val builder = ImageVector.Builder(name, 24.dp, 24.dp, 24f, 24f)
        paths.forEachIndexed { i, d ->
            if (i in filled) {
                // even-odd: контур внутри залитого — прорезь (стрелка в кружке «Скачано»)
                builder.addPath(addPathNodes(d), fill = SolidColor(Color.White), pathFillType = PathFillType.EvenOdd)
            } else {
                builder.addPath(
                    addPathNodes(d),
                    stroke = SolidColor(Color.White),
                    strokeLineWidth = 1.5f,
                    strokeLineCap = StrokeCap.Round,
                    strokeLineJoin = StrokeJoin.Round,
                )
            }
        }
        return builder.build()
    }

    private const val HEART = "M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z"

    val Wave = icon("wave", "M3 12c2-4 4-4 6 0s4 4 6 0 4-4 6 0")
    val Search = icon("search", circle(11f, 11f, 6.5f), "M16 16l4.5 4.5")
    val Library = icon("library", "M5 4v16M10 4v16M14.5 4.5l5 15")
    val Settings = icon(
        "settings",
        "M10.3 3h3.4l.5 2.4 1.7.7 2-1.4 2.4 2.4-1.4 2 .7 1.7 2.4.5v3.4l-2.4.5-.7 1.7 1.4 2-2.4 2.4-2-1.4-1.7.7-.5 2.4h-3.4l-.5-2.4-1.7-.7-2 1.4-2.4-2.4 1.4-2-.7-1.7L3 13.7v-3.4l2.4-.5.7-1.7-1.4-2 2.4-2.4 2 1.4 1.7-.7z",
        circle(12f, 12f, 3f),
    )
    val Back = icon("back", "M15 5l-7 7 7 7")
    val Chevron = icon("chevron", "M9 5l7 7-7 7")
    val Play = icon("play", "M8 5.5v13l10.5-6.5z", filled = setOf(0))
    val Pause = icon("pause", "M8 5h3v14H8zM13 5h3v14h-3z", filled = setOf(0))
    val Next = icon("next", "M6 6l9 6-9 6z", "M18 6v12")
    val Prev = icon("prev", "M18 6l-9 6 9 6z", "M6 6v12")
    val Heart = icon("heart", HEART)
    val HeartFilled = icon("heart-filled", HEART, filled = setOf(0))
    val Dislike = icon("dislike", HEART, "M4 4l16 16")
    val Shuffle = icon(
        "shuffle",
        "M4 7h3c4 0 6 10 10 10h3M4 17h3c1.6 0 2.8-1.6 3.9-3.5M13.1 9.5C14.2 8 15.4 7 17 7h3M18 4l3 3-3 3M18 14l3 3-3 3",
    )
    val Sliders = icon("sliders", "M4 7h10M18 7h2M4 17h4M12 17h8", circle(16f, 7f, 2f), circle(10f, 17f, 2f))
    val Server = icon("server", "M6 4h12a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2zM6 13h12a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2z", "M8 7.5h.01M8 16.5h.01")
    val Playback = icon("playback", "M4 9v6h3l5 4V5L7 9z", "M16 9a4 4 0 0 1 0 6M18.5 6.5a7.5 7.5 0 0 1 0 11")
    val Palette = icon("palette", "M12 3a9 9 0 1 0 0 18c1.1 0 1.5-.9 1-1.8-.6-1.1 0-2.2 1.2-2.2H17a4 4 0 0 0 4-4c0-5.5-4-10-9-10z")
    val Database = icon("database", "M5 6c0-1.7 3.1-3 7-3s7 1.3 7 3-3.1 3-7 3-7-1.3-7-3z", "M5 6v12c0 1.7 3.1 3 7 3s7-1.3 7-3V6M5 12c0 1.7 3.1 3 7 3s7-1.3 7-3")
    val Info = icon("info", circle(12f, 12f, 9f), "M12 11v6M12 7.5h.01")
    val Close = icon("close", "M6 6l12 12M18 6L6 18")
    val Album = icon("album", circle(12f, 12f, 8.5f), circle(12f, 12f, 2.5f))
    val Artist = icon("artist", circle(12f, 8.5f, 3.5f), "M5 20c1-3.6 3.8-5.5 7-5.5s6 1.9 7 5.5")
    val Playlist = icon("playlist", "M4 6h12M4 11h12M4 16h7", "M16 15.5V9l4-1", circle(14f, 16f, 2f))
    val Clock = icon("clock", circle(12f, 12f, 8.5f), "M12 7.5V12l3 2")
    val More = icon("more", circle(5f, 12f, 1.4f), circle(12f, 12f, 1.4f), circle(19f, 12f, 1.4f), filled = setOf(0, 1, 2))
    val ChevronDown = icon("chevron-down", "M6 9l6 6 6-6")
    val Expand = icon("expand", "M14 4h6v6M10 20H4v-6M20 4l-7 7M4 20l7-7")
    val Check = icon("check", "M5 12.5l4.5 4.5L19 7.5")
    val Queue = icon("queue", "M4 6h16M4 11h16M4 16h8", "M15 13.5v6l5-3z")
    val Grip = icon("grip", "M5 9h14M5 15h14")
    val Lyrics = icon("lyrics", "M4 6h12M4 10h12M4 14h7", "M17 18.5V12l3-1", circle(15.5f, 18.5f, 1.5f))
    val Mix = icon("mix", "M4 6h12M4 11h12M4 16h7", "M17 14v6M14 17h6")
    val Sync = icon("sync", "M20 12a8 8 0 0 1-14 5.3M4 12a8 8 0 0 1 14-5.3", "M18 3v4h-4M6 21v-4h4")
    val Share = icon("share", "M12 15V4M8 7.5l4-4 4 4", "M5 12v6.5A1.5 1.5 0 0 0 6.5 20h11a1.5 1.5 0 0 0 1.5-1.5V12")
    val Download = icon("download", "M12 4v11M7.5 10.5l4.5 4.5 4.5-4.5", "M5 19.5h14")
    val Downloaded = icon("downloaded", circle(12f, 12f, 9f) + "M11 6.5h2v5.5h2.8L12 16.2 8.2 12H11z", filled = setOf(0))
    val Offline = icon("offline", "M7 18h10a4 4 0 0 0 .6-7.95A6 6 0 0 0 6.1 9.2 4.5 4.5 0 0 0 7 18z", "M4 4l16 16")
    val Phone = icon("phone", "M8 3h8a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z", "M11 18h2")
}
