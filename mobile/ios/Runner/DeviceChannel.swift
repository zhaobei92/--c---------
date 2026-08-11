// iOS 原生设备模块骨架 — 实现 lib/platform/device_channel.dart 契约。
//
// 职责(docs/02-architecture.md §2.1):
//   * CoreBluetooth 扫描/连接/特征读写 + State Restoration(后台恢复)
//   * NEHotspotConfiguration 加入设备热点(需本地网络访问用途说明)
//   * URLSession 下载:Range 断点续传(每 4—8MB 检查点)+ SHA-256 校验
//   * BGTaskScheduler 队列恢复;OTA 数据传输
// 阶段2 前对接 mock_device(HTTP);真协议到位后仅替换传输实现,契约不变。

import CoreBluetooth
import CryptoKit
import Flutter
import NetworkExtension

public class DeviceChannelPlugin: NSObject, FlutterPlugin {
    private var central: CBCentralManager?
    private var eventSink: FlutterEventSink?

    public static func register(with registrar: FlutterPluginRegistrar) {
        let instance = DeviceChannelPlugin()
        let channel = FlutterMethodChannel(name: "ysnote/device",
                                           binaryMessenger: registrar.messenger())
        registrar.addMethodCallDelegate(instance, channel: channel)

        let events = FlutterEventChannel(name: "ysnote/device_events",
                                         binaryMessenger: registrar.messenger())
        events.setStreamHandler(instance)
    }

    public func handle(_ call: FlutterMethodCall, result: @escaping FlutterResult) {
        switch call.method {
        case "startScan":
            // TODO(阶段2): CBCentralManager(restoreIdentifier:) + scanForPeripherals
            result(nil)
        case "stopScan":
            result(nil)
        case "connect":
            // TODO(阶段2): 连接 + 服务发现 + 绑定校验;超时回 DEV_1201
            result(FlutterError(code: "DEV_1201", message: "not implemented", details: nil))
        case "disconnect":
            result(nil)
        case "readBattery", "readStorage", "setRecordMode",
             "startRecord", "stopRecord", "listFiles", "deleteDeviceFile":
            // TODO(阶段2): BLE 命令封包(协议映射层)
            result(FlutterError(code: "DEV_1401", message: "not implemented", details: nil))
        case "startWifiTransfer":
            // TODO(阶段2): NEHotspotConfiguration(ssid:passphrase:) 申请加入;拒绝回 DEV_1202
            result(FlutterError(code: "DEV_1202", message: "not implemented", details: nil))
        case "stopWifiTransfer":
            result(nil)
        case "downloadFile":
            // TODO(阶段2): URLSession + Range 续传 + SHA256(CryptoKit);校验超限回 DEV_1204
            result(FlutterError(code: "DEV_1204", message: "not implemented", details: nil))
        case "startOta":
            // TODO(阶段5): 验签(DEV_1301)、电量阈值(DEV_1302)、分包、失败恢复
            result(FlutterError(code: "DEV_1302", message: "not implemented", details: nil))
        default:
            result(FlutterMethodNotImplemented)
        }
    }
}

extension DeviceChannelPlugin: FlutterStreamHandler {
    public func onListen(withArguments _: Any?,
                         eventSink events: @escaping FlutterEventSink) -> FlutterError? {
        eventSink = events
        return nil
    }

    public func onCancel(withArguments _: Any?) -> FlutterError? {
        eventSink = nil
        return nil
    }

    // 事件负载契约:["type": "transferProgress", "fileId": ..., "bytes": ..., "total": ...]
    func emit(_ payload: [String: Any]) {
        eventSink?(payload)
    }
}
