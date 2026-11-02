package app.volna.ui

import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameMillis
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.platform.LocalDensity
import app.volna.player.AudioLevel
import kotlin.math.ceil
import kotlin.math.floor
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sin

/**
 * Фон: топографические изолинии, которые медленно накатывают волной (порт web/topo.js).
 * Поле = наклон по вертикали + шум, изолинии строятся методом marching squares.
 * mode: live — обычное течение, calm — медленнее и тише, off — без линий.
 */
@Composable
fun Topo(modifier: Modifier = Modifier, mode: String = "live", dim: Boolean = false, playing: Boolean = false) {
    val speed = when (mode) {
        "calm" -> 0.3f
        "off" -> 0f
        else -> 1f
    }
    val modeAlpha = when (mode) {
        "calm" -> 0.65f
        "off" -> 0f
        else -> 1f
    }
    val alpha by animateFloatAsState(if (dim) modeAlpha * 0.4f else modeAlpha, tween(600), label = "topo-dim")
    val idle by animateFloatAsState(if (playing) 0.35f else 0f, tween(1200), label = "topo-energy")
    // «линии под музыку»: вместо ровного течения волна дышит громкостью того, что играет
    val react by rememberUpdatedState(UiPrefs.react && playing)
    var loud by remember { mutableFloatStateOf(0f) }
    val energy = if (react) loud else idle
    var time by remember { mutableFloatStateOf((Math.random() * 100).toFloat()) }
    val step = with(LocalDensity.current) { 12f * density }

    LaunchedEffect(speed) {
        if (speed == 0f) return@LaunchedEffect
        var last = withFrameMillis { it }
        while (true) {
            val now = withFrameMillis { it }
            if (now - last >= 33) { // ~30 кадров в секунду достаточно и бережёт батарею
                // громкость того, что звучит сейчас; удар — сразу, затихание — плавно, чтобы линии шли в такт
                val target = if (react) AudioLevel.current() else 0f
                loud += (target - loud) * (if (target > loud) 0.65f else 0.2f)
                time += min((now - last) / 1000f, 0.1f) * (0.5f + energy * 2.2f) * speed
                last = now
            }
        }
    }

    Canvas(modifier) {
        if (alpha <= 0.01f) return@Canvas
        val t = time
        val cols = ceil(size.width / step).toInt() + 1
        val rows = ceil(size.height / step).toInt() + 1
        val field = FloatArray(cols * rows)
        val amp = 1f + energy * 0.9f
        val px = step / density // поле считаем в dp, чтобы рисунок совпадал с веб-версией (там CSS-пиксели)
        for (r in 0 until rows) {
            val y = r * px
            for (c in 0 until cols) {
                val x = c * px
                val swell = sin(x * 0.0031f + y * 0.0009f - t * 0.55f)
                field[r * cols + c] = y * 0.0042f - t * 0.06f +
                    amp * (Noise.noise3(x * 0.0017f, y * 0.0017f * 1.5f, t * 0.05f) * 0.95f +
                        Noise.noise3(x * 0.0017f * 2.6f + 31.7f, y * 0.0017f * 2.6f, t * 0.09f) * 0.22f +
                        swell * 0.2f)
            }
        }
        val normal = Path()
        val accent = Path()
        val spacing = 0.17f
        for (r in 0 until rows - 1) {
            val y0 = r * step
            for (c in 0 until cols - 1) {
                val i = r * cols + c
                val a = field[i]
                val b = field[i + 1]
                val d = field[i + cols + 1]
                val e = field[i + cols]
                val lo = min(min(a, b), min(d, e))
                val hi = max(max(a, b), max(d, e))
                val x0 = c * step
                var n = ceil(lo / spacing).toInt()
                while (n * spacing <= hi) {
                    val level = n * spacing
                    val index = (if (a > level) 8 else 0) or (if (b > level) 4 else 0) or
                        (if (d > level) 2 else 0) or (if (e > level) 1 else 0)
                    if (index != 0 && index != 15) {
                        val path = if (Math.floorMod(n, 5) == 0) accent else normal
                        val tx = x0 + step * (level - a) / (b - a)
                        val ry = y0 + step * (level - b) / (d - b)
                        val bx = x0 + step * (level - e) / (d - e)
                        val ly = y0 + step * (level - a) / (e - a)
                        val yb = y0 + step
                        val xr = x0 + step
                        when (index) {
                            1, 14 -> path.segment(x0, ly, bx, yb)
                            2, 13 -> path.segment(bx, yb, xr, ry)
                            3, 12 -> path.segment(x0, ly, xr, ry)
                            4, 11 -> path.segment(tx, y0, xr, ry)
                            6, 9 -> path.segment(tx, y0, bx, yb)
                            7, 8 -> path.segment(tx, y0, x0, ly)
                            5 -> {
                                path.segment(tx, y0, xr, ry)
                                path.segment(x0, ly, bx, yb)
                            }
                            10 -> {
                                path.segment(tx, y0, x0, ly)
                                path.segment(bx, yb, xr, ry)
                            }
                        }
                    }
                    n++
                }
            }
        }
        val width = 1f * density
        drawPath(normal, Color.White.copy(alpha = (0.12f + energy * 0.1f) * alpha), style = Stroke(width))
        drawPath(accent, Color.White.copy(alpha = (0.32f + energy * 0.3f) * alpha), style = Stroke(width))
    }
}

private fun Path.segment(x1: Float, y1: Float, x2: Float, y2: Float) {
    moveTo(x1, y1)
    lineTo(x2, y2)
}

/** 3D simplex noise (алгоритм Стефана Густавсона). */
private object Noise {
    private val grad = floatArrayOf(
        1f, 1f, 0f, -1f, 1f, 0f, 1f, -1f, 0f, -1f, -1f, 0f,
        1f, 0f, 1f, -1f, 0f, 1f, 1f, 0f, -1f, -1f, 0f, -1f,
        0f, 1f, 1f, 0f, -1f, 1f, 0f, 1f, -1f, 0f, -1f, -1f,
    )
    private val perm = IntArray(512)
    private val permMod12 = IntArray(512)

    init {
        val p = IntArray(256) { it }
        var s = 20261031L
        for (i in 255 downTo 1) {
            s = (s * 1664525L + 1013904223L) and 0xFFFFFFFFL
            val j = (s % (i + 1)).toInt()
            val tmp = p[i]
            p[i] = p[j]
            p[j] = tmp
        }
        for (i in 0 until 512) {
            perm[i] = p[i and 255]
            permMod12[i] = perm[i] % 12
        }
    }

    private const val F3 = 1f / 3f
    private const val G3 = 1f / 6f

    private fun corner(gi: Int, x: Float, y: Float, z: Float): Float {
        var t = 0.6f - x * x - y * y - z * z
        if (t < 0) return 0f
        t *= t
        return t * t * (grad[gi] * x + grad[gi + 1] * y + grad[gi + 2] * z)
    }

    fun noise3(xin: Float, yin: Float, zin: Float): Float {
        val s = (xin + yin + zin) * F3
        val i = floor(xin + s).toInt()
        val j = floor(yin + s).toInt()
        val k = floor(zin + s).toInt()
        val t = (i + j + k) * G3
        val x0 = xin - (i - t)
        val y0 = yin - (j - t)
        val z0 = zin - (k - t)
        val i1: Int; val j1: Int; val k1: Int; val i2: Int; val j2: Int; val k2: Int
        if (x0 >= y0) {
            if (y0 >= z0) { i1 = 1; j1 = 0; k1 = 0; i2 = 1; j2 = 1; k2 = 0 }
            else if (x0 >= z0) { i1 = 1; j1 = 0; k1 = 0; i2 = 1; j2 = 0; k2 = 1 }
            else { i1 = 0; j1 = 0; k1 = 1; i2 = 1; j2 = 0; k2 = 1 }
        } else {
            if (y0 < z0) { i1 = 0; j1 = 0; k1 = 1; i2 = 0; j2 = 1; k2 = 1 }
            else if (x0 < z0) { i1 = 0; j1 = 1; k1 = 0; i2 = 0; j2 = 1; k2 = 1 }
            else { i1 = 0; j1 = 1; k1 = 0; i2 = 1; j2 = 1; k2 = 0 }
        }
        val ii = i and 255
        val jj = j and 255
        val kk = k and 255
        val n0 = corner(permMod12[ii + perm[jj + perm[kk]]] * 3, x0, y0, z0)
        val n1 = corner(permMod12[ii + i1 + perm[jj + j1 + perm[kk + k1]]] * 3, x0 - i1 + G3, y0 - j1 + G3, z0 - k1 + G3)
        val n2 = corner(permMod12[ii + i2 + perm[jj + j2 + perm[kk + k2]]] * 3, x0 - i2 + 2 * G3, y0 - j2 + 2 * G3, z0 - k2 + 2 * G3)
        val n3 = corner(permMod12[ii + 1 + perm[jj + 1 + perm[kk + 1]]] * 3, x0 - 1 + 3 * G3, y0 - 1 + 3 * G3, z0 - 1 + 3 * G3)
        return 32f * (n0 + n1 + n2 + n3)
    }
}
