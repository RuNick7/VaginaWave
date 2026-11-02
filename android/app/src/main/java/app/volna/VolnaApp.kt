package app.volna

import android.app.Application
import app.volna.data.Prefs
import app.volna.data.VolnaApi
import app.volna.player.Devices
import app.volna.player.Downloads
import app.volna.player.Offline
import app.volna.player.VolnaPlayer
import app.volna.ui.SearchHistory
import coil3.ImageLoader
import coil3.PlatformContext
import coil3.SingletonImageLoader
import coil3.network.okhttp.OkHttpNetworkFetcherFactory

class VolnaApp : Application(), SingletonImageLoader.Factory {
    lateinit var prefs: Prefs
        private set
    lateinit var api: VolnaApi
        private set

    override fun onCreate() {
        super.onCreate()
        prefs = Prefs(this)
        api = VolnaApi(prefs)
        Downloads.init(this, api, prefs) // до плеера: он читает скачанное из кэша загрузок
        Offline.init(this, api, prefs)
        VolnaPlayer.init(this, api)
        Updates.init(this, api.client)
        Devices.init(this, api, prefs)
        SearchHistory.init(prefs)
    }

    /** Обложки идут через сервис Volna, поэтому Coil ходит тем же клиентом, с токеном входа. */
    override fun newImageLoader(context: PlatformContext): ImageLoader =
        ImageLoader.Builder(context).components { add(OkHttpNetworkFetcherFactory(callFactory = { api.client })) }.build()
}
