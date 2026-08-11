// Android 原生设备模块骨架 — 实现 lib/platform/device_channel.dart 契约。
//
// 职责(docs/02-architecture.md §2.1):
//   * Bluetooth LE 扫描/连接/GATT + Companion Device
//   * Wi-Fi Direct / 局部热点(Android 13+ 处理 NEARBY_WIFI_DEVICES 运行时权限)
//   * Foreground Service(dataSync/connectedDevice)支撑长时间同步
//   * WorkManager 队列恢复;OkHttp Range 断点续传 + SHA-256 校验;OTA 传输
// 阶段2 前对接 mock_device(HTTP);真协议到位后仅替换传输实现,契约不变。

package com.ysnote.device

import io.flutter.embedding.engine.plugins.FlutterPlugin
import io.flutter.plugin.common.EventChannel
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel

class DeviceChannelPlugin : FlutterPlugin, MethodChannel.MethodCallHandler, EventChannel.StreamHandler {
    private lateinit var channel: MethodChannel
    private lateinit var events: EventChannel
    private var eventSink: EventChannel.EventSink? = null

    override fun onAttachedToEngine(binding: FlutterPlugin.FlutterPluginBinding) {
        channel = MethodChannel(binding.binaryMessenger, "ysnote/device")
        channel.setMethodCallHandler(this)
        events = EventChannel(binding.binaryMessenger, "ysnote/device_events")
        events.setStreamHandler(this)
    }

    override fun onDetachedFromEngine(binding: FlutterPlugin.FlutterPluginBinding) {
        channel.setMethodCallHandler(null)
        events.setStreamHandler(null)
    }

    override fun onMethodCall(call: MethodCall, result: MethodChannel.Result) {
        when (call.method) {
            "startScan" -> result.success(null) // TODO(阶段2): BluetoothLeScanner + 权限处理
            "stopScan" -> result.success(null)
            "connect" -> result.error("DEV_1201", "not implemented", null)
            "disconnect" -> result.success(null)
            "readBattery", "readStorage", "setRecordMode",
            "startRecord", "stopRecord", "listFiles", "deleteDeviceFile" ->
                result.error("DEV_1401", "not implemented", null) // TODO(阶段2): GATT 命令封包
            "startWifiTransfer" ->
                result.error("DEV_1202", "not implemented", null) // TODO(阶段2): WifiNetworkSpecifier / P2P
            "stopWifiTransfer" -> result.success(null)
            "downloadFile" ->
                result.error("DEV_1204", "not implemented", null) // TODO(阶段2): Range 续传 + SHA-256
            "startOta" ->
                result.error("DEV_1302", "not implemented", null) // TODO(阶段5): 验签/电量/分包/恢复
            else -> result.notImplemented()
        }
    }

    override fun onListen(arguments: Any?, sink: EventChannel.EventSink?) {
        eventSink = sink
    }

    override fun onCancel(arguments: Any?) {
        eventSink = null
    }

    // 事件负载契约:mapOf("type" to "transferProgress", "fileId" to ..., "bytes" to ..., "total" to ...)
    fun emit(payload: Map<String, Any?>) {
        eventSink?.success(payload)
    }
}
