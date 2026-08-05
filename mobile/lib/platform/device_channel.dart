/// Platform Channel 契约 — 唯一事实来源。
///
/// iOS(native/ios/DeviceChannel.swift)与 Android(native/android/DeviceChannelPlugin.kt)
/// 必须按本文件的方法名、参数与事件负载实现。硬件真协议到位前,原生层对接
/// mock_device/(HTTP 模拟);替换真协议时本契约不变,只改原生实现。
///
/// MethodChannel: 'ysnote/device'   — 请求/响应命令
/// EventChannel:  'ysnote/device_events' — 设备状态流(连接、电量、传输进度)
library device_channel;

import 'dart:async';

import 'package:flutter/services.dart';

class DeviceInfo {
  const DeviceInfo({
    required this.sn,
    required this.model,
    required this.firmware,
    required this.protocolVersion,
  });

  final String sn;
  final String model;
  final String firmware;
  final String protocolVersion;

  factory DeviceInfo.fromMap(Map<Object?, Object?> m) => DeviceInfo(
        sn: m['sn']! as String,
        model: m['model']! as String,
        firmware: m['firmware']! as String,
        protocolVersion: m['protocolVersion']! as String,
      );
}

class DeviceFileEntry {
  const DeviceFileEntry({
    required this.id,
    required this.name,
    required this.size,
    required this.sha256,
    required this.createdAt,
    required this.synced,
  });

  final String id;
  final String name;
  final int size;
  final String sha256;
  final String createdAt;
  final bool synced;

  factory DeviceFileEntry.fromMap(Map<Object?, Object?> m) => DeviceFileEntry(
        id: m['id']! as String,
        name: m['name']! as String,
        size: m['size']! as int,
        sha256: m['sha256']! as String,
        createdAt: m['createdAt']! as String,
        synced: (m['synced'] ?? false) as bool,
      );
}

/// 设备事件(EventChannel 负载:{type, ...})
/// type ∈ connectionChanged / batteryChanged / transferProgress / transferError
class DeviceEvent {
  const DeviceEvent(this.type, this.payload);

  final String type;
  final Map<Object?, Object?> payload;
}

/// 原生设备模块接口。所有方法失败时抛 [PlatformException],
/// code 为 docs/07-error-codes.md 中的错误码(如 DEV_1201)。
class DeviceChannel {
  DeviceChannel({MethodChannel? channel, EventChannel? events})
      : _channel = channel ?? const MethodChannel('ysnote/device'),
        _events = events ?? const EventChannel('ysnote/device_events');

  final MethodChannel _channel;
  final EventChannel _events;

  // ---------------- 扫描与连接(BLE)

  /// 开始 BLE 扫描;发现的设备经事件流上报(type=deviceFound)。
  Future<void> startScan() => _invoke('startScan');

  Future<void> stopScan() => _invoke('stopScan');

  /// 连接并校验绑定;超时抛 DEV_1201。
  Future<DeviceInfo> connect(String sn) async =>
      DeviceInfo.fromMap(await _invoke('connect', {'sn': sn}) as Map);

  Future<void> disconnect() => _invoke('disconnect');

  // ---------------- 状态与控制

  Future<int> readBattery() async => await _invoke('readBattery') as int;

  Future<Map<Object?, Object?>> readStorage() async =>
      await _invoke('readStorage') as Map<Object?, Object?>;

  Future<void> setRecordMode(String mode) =>
      _invoke('setRecordMode', {'mode': mode});

  Future<void> startRecord() => _invoke('startRecord');

  Future<void> stopRecord() => _invoke('stopRecord');

  // ---------------- 文件同步(BLE 索引 + Wi-Fi 传输)

  Future<List<DeviceFileEntry>> listFiles() async {
    final raw = await _invoke('listFiles') as List;
    return raw.map((e) => DeviceFileEntry.fromMap(e as Map)).toList();
  }

  /// 开启设备 Wi-Fi 并(iOS: NEHotspotConfiguration / Android: P2P)加入网络。
  /// 拒绝加入抛 DEV_1202。
  Future<void> startWifiTransfer() => _invoke('startWifiTransfer');

  Future<void> stopWifiTransfer() => _invoke('stopWifiTransfer');

  /// 下载文件到 [localPath];原生层负责 Range 断点续传(每 4—8MB 检查点)、
  /// SHA-256 校验(失败自动重试,超限抛 DEV_1204)与进度事件(transferProgress)。
  Future<void> downloadFile(String fileId, String localPath) =>
      _invoke('downloadFile', {'fileId': fileId, 'localPath': localPath});

  Future<void> deleteDeviceFile(String fileId) =>
      _invoke('deleteDeviceFile', {'fileId': fileId});

  // ---------------- OTA

  /// 电量低于阈值抛 DEV_1302;签名校验失败抛 DEV_1301。
  Future<void> startOta(String firmwarePath, {required int minBattery}) =>
      _invoke('startOta', {'firmwarePath': firmwarePath, 'minBattery': minBattery});

  // ---------------- 事件流

  Stream<DeviceEvent> events() => _events.receiveBroadcastStream().map((raw) {
        final m = raw as Map<Object?, Object?>;
        return DeviceEvent(m['type']! as String, m);
      });

  Future<Object?> _invoke(String method, [Map<String, Object?>? args]) =>
      _channel.invokeMethod<Object?>(method, args);
}
