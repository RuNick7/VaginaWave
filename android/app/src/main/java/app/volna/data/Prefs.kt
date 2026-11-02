package app.volna.data

import android.content.Context

/** Настройки самого приложения. Всё про Navidrome и волну хранит сервис Volna. */
class Prefs(context: Context) {
    private val prefs = context.getSharedPreferences("volna", Context.MODE_PRIVATE)

    /** Адрес сервиса Volna, который нашёлся при входе, например https://music.example.com/volna. */
    var serviceUrl: String
        get() = prefs.getString("service_url", DEFAULT_URL) ?: DEFAULT_URL
        set(value) = prefs.edit().putString("service_url", value.trim().trimEnd('/')).apply()

    /** Адрес так, как его ввёл человек: подставляем в форму входа. */
    var address: String
        get() = prefs.getString("address", "") ?: ""
        set(value) = prefs.edit().putString("address", value.trim()).apply()

    var user: String
        get() = prefs.getString("user", "") ?: ""
        set(value) = prefs.edit().putString("user", value).apply()

    /** Токен входа: сервис выдаёт его в обмен на логин и пароль Navidrome. */
    var token: String
        get() = prefs.getString("token", "") ?: ""
        set(value) = prefs.edit().putString("token", value).apply()

    /** Вход уже был: иначе при запуске показываем экран входа. */
    var connected: Boolean
        get() = prefs.getBoolean("connected", false)
        set(value) = prefs.edit().putBoolean("connected", value).apply()

    /** Фон: live — живые линии, calm — спокойные, off — без линий. */
    var background: String
        get() = prefs.getString("background", "live") ?: "live"
        set(value) = prefs.edit().putString("background", value).apply()

    /** Цветные обложки по умолчанию; ч/б включается в настройках интерфейса. */
    var colorCovers: Boolean
        get() = prefs.getBoolean("covers_in_color", true)
        set(value) = prefs.edit().putBoolean("covers_in_color", value).apply()

    /** Линии на фоне «дышат» под громкость музыки. */
    var react: Boolean
        get() = prefs.getBoolean("react", true)
        set(value) = prefs.edit().putBoolean("react", value).apply()

    /** Показывать текст песни на главной вместо обложки. */
    var lyrics: Boolean
        get() = prefs.getBoolean("lyrics", false)
        set(value) = prefs.edit().putBoolean("lyrics", value).apply()

    /** Номер этого устройства для сервиса («Играет на X»); заводится при первом отчёте. */
    var device: String
        get() = prefs.getString("device_id", "") ?: ""
        set(value) = prefs.edit().putString("device_id", value).apply()

    /** История поиска: JSON-список запросов и открытого из поиска. */
    var searchHistory: String
        get() = prefs.getString("search_history", "[]") ?: "[]"
        set(value) = prefs.edit().putString("search_history", value).apply()

    // --- загрузки и офлайн ---

    /** Скачивать только по Wi‑Fi (сеть без оплаты за трафик). */
    var wifiOnly: Boolean
        get() = prefs.getBoolean("offline_wifi_only", true)
        set(value) = prefs.edit().putBoolean("offline_wifi_only", value).apply()

    /** Держать скачанными все треки из «Мне нравится». */
    var offlineLiked: Boolean
        get() = prefs.getBoolean("offline_liked", false)
        set(value) = prefs.edit().putBoolean("offline_liked", value).apply()

    /** Запас на случай без сети: сколько часто и недавно слушаемых треков держать скачанными (0 — не держать). */
    var reserve: Int
        get() = prefs.getInt("offline_reserve", 50)
        set(value) = prefs.edit().putInt("offline_reserve", value).apply()

    /** Качество загрузок: original | 320 | 192 | 128. */
    var downloadQuality: String
        get() = prefs.getString("offline_quality", "original") ?: "original"
        set(value) = prefs.edit().putString("offline_quality", value).apply()

    /** Офлайн-режим, включённый вручную: играет только скачанное, даже когда сеть есть. */
    var forceOffline: Boolean
        get() = prefs.getBoolean("offline_forced", false)
        set(value) = prefs.edit().putBoolean("offline_forced", value).apply()

    /** Когда последний раз сверяли с сервисом «Мне нравится», запас и скачанные альбомы. */
    var offlineSynced: Long
        get() = prefs.getLong("offline_synced", 0)
        set(value) = prefs.edit().putLong("offline_synced", value).apply()

    companion object {
        const val DEFAULT_URL = "http://10.0.2.2:8765"
    }
}
