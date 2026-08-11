# mobile/ — Flutter 双平台工程脚手架

Riverpod + Clean Architecture 分层;zh/en/ar 三语(ar 自动 RTL);SQLite 本地库;
上传队列(检查点断点续传);Platform Channel 原生设备契约。

## 目录

```
lib/
  main.dart / app.dart      入口与多语言(localeProvider,ar → RTL)
  core/router.dart          go_router 骨架路由(对应 PRD 55 页清单)
  platform/device_channel.dart   ★ Platform Channel 契约(唯一事实来源)
  data/
    local/app_database.dart      SQLite:recordings + sync_queue + upload_queue
    upload/upload_queue.dart     分片上传队列(断点续传、失败重试、仅Wi-Fi)
    api/api_client.dart          服务端 API 客户端(错误码 retryable 语义)
  features/                 auth / home / device / files / transcript / membership / settings
l10n/                       app_en.arb / app_zh.arb / app_ar.arb
native/
  ios/DeviceChannel.swift        iOS 原生模块骨架(CoreBluetooth/NEHotspot/StoreKit 接入点)
  android/DeviceChannelPlugin.kt Android 原生模块骨架(BLE/WifiSpecifier/Billing 接入点)
```

## 构建(需本地 Flutter SDK ≥ 3.22)

```bash
flutter create . --platforms=ios,android --org com.ysnote   # 首次生成平台工程
flutter pub get
flutter gen-l10n
flutter analyze
flutter test
```

`flutter create .` 生成 ios/、android/ 平台工程后:
1. 将 `native/ios/DeviceChannel.swift` 加入 Runner target,并在 AppDelegate 注册插件;
2. 将 `native/android/DeviceChannelPlugin.kt` 放入 `android/app/src/main/kotlin/com/ysnote/device/`,
   在 MainActivity/FlutterEngine 注册;
3. iOS Info.plist:蓝牙、本地网络(NSLocalNetworkUsageDescription)、麦克风用途说明;
   开启 Background Modes(bluetooth-central、processing);
4. AndroidManifest:BLUETOOTH_SCAN/CONNECT、NEARBY_WIFI_DEVICES(13+)、
   RECORD_AUDIO、FOREGROUND_SERVICE(dataSync/connectedDevice)。

## 开发约定

- 页面/业务在 Flutter;BLE、Wi-Fi 传输、后台、支付、OTA 一律走 `DeviceChannel` 契约进原生。
- 无真机时原生层对接 `mock_device/`(见其 protocol.md);Demo Mode 复用同一路径。
- 错误码展示必须带尾缀(如 `(DEV_1204)`),文案 key 三语齐备(docs/07)。
- 埋点统一 AnalyticsService 封装,事件表见 docs/08。
