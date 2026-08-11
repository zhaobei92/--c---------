package com.ysnote.ysnote

import com.ysnote.device.DeviceChannelPlugin
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine

class MainActivity : FlutterActivity() {
    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        // 注册设备原生模块(契约:lib/platform/device_channel.dart)
        flutterEngine.plugins.add(DeviceChannelPlugin())
    }
}
