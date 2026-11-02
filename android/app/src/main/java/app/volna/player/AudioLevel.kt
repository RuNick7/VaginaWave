package app.volna.player

import androidx.media3.common.C
import androidx.media3.exoplayer.audio.AudioSink
import androidx.media3.exoplayer.audio.ForwardingAudioSink
import androidx.media3.exoplayer.audio.TeeAudioProcessor
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.math.min
import kotlin.math.sqrt

/**
 * Громкость того, что играет: под неё «дышат» линии на фоне, как в веб-версии.
 * Считаем по звуку на выходе ExoPlayer, поэтому разрешение на микрофон не нужно.
 *
 * Звук проходит через «тройник» заранее: в буфер AudioTrack он попадает на 0,2–1 с раньше,
 * чем его слышно (в Bluetooth — ещё позже). Поэтому громкость считаем кусками по 20 мс и
 * помечаем временем в треке, а линии берут кусок, который звучит сейчас: позицию воспроизведения
 * сообщает сам аудиовыход (Sink), с учётом его задержки.
 */
object AudioLevel : TeeAudioProcessor.AudioBufferSink {
    private const val CHUNK_US = 20_000L
    private const val SIZE = 1024 // ~20 с по 20 мс: с запасом на любой буфер

    private val lock = Any()
    private val times = LongArray(SIZE)
    private val levels = FloatArray(SIZE)
    private var count = 0
    private var head = 0 // куда писать следующий кусок

    private var encoding = C.ENCODING_INVALID
    private var sampleRate = 0
    private var channels = 0
    private var bufferUs = C.TIME_UNSET // время в треке у буфера, который сейчас обрабатывается
    private var framesInBuffer = 0L

    @Volatile private var playedUs = C.TIME_UNSET // что звучит, по аудиовыходу
    @Volatile private var playedAtNs = 0L

    /** Громкость того, что слышно сейчас: 0 — тишина, 1 — громко; та же шкала, что у веб-плеера. */
    fun current(): Float {
        val played = playedUs
        synchronized(lock) {
            if (count == 0) return 0f
            val latest = (head - 1 + SIZE) % SIZE
            if (played == C.TIME_UNSET) return levels[latest]
            // между отчётами аудиовыхода время идёт само; дальше 0,3 с не угадываем
            val now = played + min((System.nanoTime() - playedAtNs) / 1000, 300_000L)
            for (k in 0 until count) {
                val i = (latest - k + SIZE) % SIZE
                if (times[i] <= now) return levels[i]
            }
            return 0f
        }
    }

    fun silence() = clear()

    override fun flush(sampleRateHz: Int, channelCount: Int, encoding: Int) {
        this.encoding = encoding
        sampleRate = sampleRateHz
        channels = channelCount
        clear()
    }

    override fun handleBuffer(buffer: ByteBuffer) {
        val bytes = when (encoding) {
            C.ENCODING_PCM_16BIT -> 2
            C.ENCODING_PCM_FLOAT -> 4
            else -> return
        }
        if (sampleRate <= 0 || channels <= 0 || bufferUs == C.TIME_UNSET) return
        val data = buffer.duplicate().order(ByteOrder.nativeOrder())
        val frames = data.remaining() / (bytes * channels)
        val chunk = (sampleRate * CHUNK_US / 1_000_000L).toInt().coerceAtLeast(1)
        var frame = 0
        while (frame < frames) {
            val end = min(frame + chunk, frames)
            var sum = 0.0
            var n = 0
            var i = frame * channels
            val last = end * channels
            while (i < last) { // каждый четвёртый отсчёт: для громкости хватает
                val v = if (bytes == 2) data.getShort(data.position() + i * 2) / 32768.0 else data.getFloat(data.position() + i * 4).toDouble()
                sum += v * v
                n++
                i += 4
            }
            val at = bufferUs + (framesInBuffer + frame) * 1_000_000L / sampleRate
            if (n > 0) push(at, min(1.0, sqrt(sum / n) * 3.2).toFloat())
            frame = end
        }
        framesInBuffer += frames
    }

    private fun push(at: Long, level: Float) = synchronized(lock) {
        times[head] = at
        levels[head] = level
        head = (head + 1) % SIZE
        if (count < SIZE) count++
    }

    private fun clear() = synchronized(lock) {
        count = 0
        head = 0
    }

    /** Аудиовыход ExoPlayer, который сообщает, какой звук в какой момент уходит в динамик. */
    class Sink(sink: AudioSink) : ForwardingAudioSink(sink) {
        private var lastInputUs = C.TIME_UNSET

        override fun handleBuffer(buffer: ByteBuffer, presentationTimeUs: Long, encodedAccessUnitCount: Int): Boolean {
            if (presentationTimeUs != lastInputUs) { // новый буфер (тот же приходит снова, если не влез целиком)
                lastInputUs = presentationTimeUs
                bufferUs = presentationTimeUs
                framesInBuffer = 0
            }
            return super.handleBuffer(buffer, presentationTimeUs, encodedAccessUnitCount)
        }

        override fun getCurrentPositionUs(sourceEnded: Boolean): Long {
            val position = super.getCurrentPositionUs(sourceEnded)
            if (position != AudioSink.CURRENT_POSITION_NOT_SET) {
                playedUs = position
                playedAtNs = System.nanoTime()
            }
            return position
        }

        override fun flush() {
            super.flush()
            lastInputUs = C.TIME_UNSET
            bufferUs = C.TIME_UNSET
            playedUs = C.TIME_UNSET
            clear()
        }
    }
}
