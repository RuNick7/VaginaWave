package app.volna.ui

import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.drawIntoCanvas
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.platform.LocalDensity
import app.volna.player.AudioLevel
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlin.math.ceil
import kotlin.math.exp
import kotlin.math.floor
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sin

/**
 * Фон: топографические изолинии, которые медленно накатывают волной (порт web/topo.js).
 * Поле = наклон по вертикали + шум, изолинии строятся методом marching squares.
 * mode: live — обычное течение, calm — медленнее и тише, off — без линий.
 *
 * Анимация идёт на частоте экрана (до ~90 кадров; в calm — 30). Линии считаются в фоновом потоке,
 * поток интерфейса только рисует готовые отрезки: прокрутка списков поверх фона не страдает. Каждый
 * кадр просит одну перерисовку, без перекомпозиции, и память не выделяется: нет рывков от сборщика мусора.
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
    val scene = remember { TopoScene() }
    var frame by remember { mutableIntStateOf(0) }
    val pixel = LocalDensity.current.density
    val step = 12f * pixel

    LaunchedEffect(speed) {
        if (speed == 0f) return@LaunchedEffect
        val minFrame = if (speed < 1f) 30_000_000L else 10_000_000L
        var last = withFrameNanos { it }
        while (true) {
            val now = withFrameNanos { it }
            if (now - last < minFrame) continue
            scene.advance(min((now - last) / 1e9f, 0.1f), if (react) AudioLevel.current() else idle, speed)
            last = now
            frame = scene.version // показать то, что фоновый поток успел посчитать к этому кадру
            if (!scene.building && scene.width > 0f) {
                scene.building = true
                launch(Dispatchers.Default) { scene.build(step, pixel) }
            }
        }
    }

    Canvas(modifier) {
        frame // каждый новый кадр анимации перерисовывает только холст
        scene.width = size.width
        scene.height = size.height
        if (alpha <= 0.01f) return@Canvas
        drawIntoCanvas { scene.draw(it.nativeCanvas, density, alpha) }
    }
}

/** Отрезки линий одним массивом: Canvas.drawLines рисует их пачкой, намного быстрее длинного Path. */
private class Segments {
    var points = FloatArray(8192)
    var count = 0

    fun add(x1: Float, y1: Float, x2: Float, y2: Float) {
        if (count + 4 > points.size) points = points.copyOf(points.size * 2)
        points[count] = x1
        points[count + 1] = y1
        points[count + 2] = x2
        points[count + 3] = y2
        count += 4
    }
}

/** Линии одного кадра: два слоя — обычные и выделенные. */
private class Lines {
    val normal = Segments()
    val accent = Segments()
}

private class TopoScene {
    @Volatile private var time = (Math.random() * 100).toFloat()
    @Volatile private var loud = 0f // огибающая громкости: высота рельефа и яркость линий
    private var drive = 0f // ещё плавнее: скорость течения, чтобы линии не дёргались на каждый удар
    private var field = FloatArray(0)
    // два набора: фоновый поток пишет в back, холст рисует front; расчёт запускается из кадра
    // анимации на главном потоке, поэтому в момент записи back никто не рисует
    @Volatile private var front = Lines()
    private var back = Lines()
    @Volatile var version = 0
        private set
    @Volatile var building = false
    @Volatile var width = 0f
    @Volatile var height = 0f
    private val paint = android.graphics.Paint(android.graphics.Paint.ANTI_ALIAS_FLAG).apply {
        style = android.graphics.Paint.Style.STROKE
    }

    /** Постоянные времени в секундах: от частоты кадров результат не зависит. */
    fun advance(dt: Float, target: Float, speed: Float) {
        loud += (target - loud) * (1f - exp(-dt / if (target > loud) ATTACK else RELEASE))
        drive += (loud - drive) * (1f - exp(-dt / DRIVE))
        time += dt * (0.5f + drive * 2.2f) * speed
    }

    fun build(step: Float, density: Float) {
        val t = time
        val normal = back.normal
        val accent = back.accent
        val cols = ceil(width / step).toInt() + 1
        val rows = ceil(height / step).toInt() + 1
        if (field.size != cols * rows) field = FloatArray(cols * rows)
        val field = field
        val amp = 1f + loud * RELIEF
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
        normal.count = 0
        accent.count = 0
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
                            1, 14 -> path.add(x0, ly, bx, yb)
                            2, 13 -> path.add(bx, yb, xr, ry)
                            3, 12 -> path.add(x0, ly, xr, ry)
                            4, 11 -> path.add(tx, y0, xr, ry)
                            6, 9 -> path.add(tx, y0, bx, yb)
                            7, 8 -> path.add(tx, y0, x0, ly)
                            5 -> {
                                path.add(tx, y0, xr, ry)
                                path.add(x0, ly, bx, yb)
                            }
                            10 -> {
                                path.add(tx, y0, x0, ly)
                                path.add(bx, yb, xr, ry)
                            }
                        }
                    }
                    n++
                }
            }
        }
        val done = back
        back = front
        front = done
        version++
        building = false
    }

    fun draw(canvas: android.graphics.Canvas, density: Float, alpha: Float) {
        val lines = front
        paint.strokeWidth = density
        paint.color = Color.White.copy(alpha = ((0.12f + loud * 0.1f) * alpha).coerceIn(0f, 1f)).toArgb()
        canvas.drawLines(lines.normal.points, 0, lines.normal.count, paint)
        paint.color = Color.White.copy(alpha = ((0.32f + loud * 0.3f) * alpha).coerceIn(0f, 1f)).toArgb()
        canvas.drawLines(lines.accent.points, 0, lines.accent.count, paint)
    }

    private companion object {
        const val ATTACK = 0.12f // удар поднимает рельеф мягко, но заметно
        const val RELEASE = 0.55f // а опускается он плавно
        const val DRIVE = 0.8f
        const val RELIEF = 0.55f // насколько громкость поднимает рельеф (было 0.9: линии прыгали)
    }
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
