/// DeviceTransport 抽象(Phase 3)。
///
/// Flutter 业务层不直接依赖尚未实现的 Swift/Kotlin BLE 插件:
///   * [MockHttpDeviceTransport] — Demo Mode / 商店审核:走 mock_device 的
///     HTTP 协议(mock_device/protocol.md),完整覆盖发现→索引→Range 下载
///     →Hash 校验→确认→删除;
///   * [NativeDeviceTransport] — 真机阶段:包装 DeviceChannel(BLE + Wi-Fi),
///     真协议 PoC 通过后启用,业务层零改动。
library device_transport;

import 'dart:io';
import 'dart:typed_data';

import 'package:crypto/crypto.dart' show sha256;
import 'package:dio/dio.dart';

import 'device_channel.dart';

class DeviceFileInfo {
  const DeviceFileInfo({
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
}

class DeviceInfoSummary {
  const DeviceInfoSummary({
    required this.sn,
    required this.model,
    required this.firmware,
    required this.battery,
  });

  final String sn;
  final String model;
  final String firmware;
  final int battery;
}

class TransportException implements Exception {
  TransportException(this.code, this.message, {this.retryable = false});

  final String code; // docs/07 错误码
  final String message;
  final bool retryable;

  @override
  String toString() => 'TransportException($code: $message)';
}

abstract class DeviceTransport {
  Future<DeviceInfoSummary> connect();
  Future<List<DeviceFileInfo>> listFiles();

  /// 下载到 [localPath]:Range 断点续传(存在的半文件从其长度续传)、
  /// SHA-256 校验(不符删除半文件并抛 DEV_1204,可重试)。
  /// [onProgress] 回调 (已下载字节, 总字节)。
  Future<void> downloadFile(DeviceFileInfo file, String localPath,
      {void Function(int received, int total)? onProgress});

  Future<void> ackSynced(String fileId);
  Future<void> deleteFile(String fileId);
  Future<void> disconnect();
}

/// Demo Mode:mock_device 的 HTTP 模拟(控制面 :9100 / 文件面 :9101)。
class MockHttpDeviceTransport implements DeviceTransport {
  MockHttpDeviceTransport({
    required this.controlUrl,
    Dio? dio,
  }) : _dio = dio ?? Dio();

  final String controlUrl;
  final Dio _dio;
  String? _fileUrl;
  int _seq = 0;

  Future<Map<String, dynamic>> _cmd(String name,
      [Map<String, dynamic>? params]) async {
    final resp = await _dio.post('$controlUrl/cmd', data: {
      'cmd': name,
      'params': params ?? const <String, dynamic>{},
      'seq': ++_seq,
    });
    final body = (resp.data as Map).cast<String, dynamic>();
    if (body['ok'] != true) {
      final code = _mapError(body['error'] as String? ?? 'E_BAD_CMD');
      throw TransportException(code, 'device error ${body['error']}',
          retryable: code == 'DEV_1203');
    }
    return (body['data'] as Map).cast<String, dynamic>();
  }

  static String _mapError(String deviceCode) => switch (deviceCode) {
        'E_LOW_BATTERY' => 'DEV_1302',
        'E_BAD_STATE' || 'E_BUSY' => 'DEV_1401',
        'E_NOT_FOUND' => 'DEV_1203',
        _ => 'DEV_1401',
      };

  @override
  Future<DeviceInfoSummary> connect() async {
    final info = await _cmd('info.get');
    final battery = await _cmd('battery.get');
    final wifi = await _cmd('wifi.start'); // 模拟加入设备热点
    _fileUrl = wifi['url'] as String;
    return DeviceInfoSummary(
      sn: info['sn'] as String,
      model: info['model'] as String,
      firmware: info['firmware'] as String,
      battery: battery['percent'] as int,
    );
  }

  @override
  Future<List<DeviceFileInfo>> listFiles() async {
    final data = await _cmd('files.list');
    return [
      for (final f in (data['files'] as List).cast<Map>())
        DeviceFileInfo(
          id: f['id'] as String,
          name: f['name'] as String,
          size: f['size'] as int,
          sha256: f['sha256'] as String,
          createdAt: f['created_at'] as String,
          synced: (f['synced'] ?? false) as bool,
        ),
    ];
  }

  @override
  Future<void> downloadFile(DeviceFileInfo file, String localPath,
      {void Function(int received, int total)? onProgress}) async {
    final url = _requireFileUrl();
    final target = File(localPath);
    var offset = target.existsSync() ? target.lengthSync() : 0;
    if (offset > file.size) {
      await target.delete();
      offset = 0;
    }
    while (offset < file.size) {
      final resp = await _dio.get<List<int>>(
        '$url/files/${file.id}',
        options: Options(
          responseType: ResponseType.bytes,
          headers: {'Range': 'bytes=$offset-'},
          validateStatus: (s) => s == 200 || s == 206,
        ),
      );
      final chunk = Uint8List.fromList(resp.data ?? const []);
      if (chunk.isEmpty) {
        throw TransportException('DEV_1205', 'device returned empty chunk',
            retryable: true);
      }
      await target.writeAsBytes(chunk,
          mode: offset == 0 ? FileMode.write : FileMode.append);
      offset += chunk.length; // 每块即检查点:进程被杀后按文件长度续传
      onProgress?.call(offset, file.size);
    }
    final digest = sha256.convert(await target.readAsBytes()).toString();
    if (digest != file.sha256) {
      await target.delete(); // 损坏文件不留检查点
      throw TransportException('DEV_1204', 'hash mismatch after download',
          retryable: true);
    }
  }

  @override
  Future<void> ackSynced(String fileId) async {
    await _dio.post('${_requireFileUrl()}/files/$fileId/ack');
  }

  @override
  Future<void> deleteFile(String fileId) async {
    await _cmd('files.delete', {'file_id': fileId});
  }

  @override
  Future<void> disconnect() async {
    try {
      await _cmd('wifi.stop');
    } finally {
      _fileUrl = null;
    }
  }

  String _requireFileUrl() {
    final url = _fileUrl;
    if (url == null) {
      throw TransportException('DEV_1202', 'not connected (wifi off)');
    }
    return url;
  }
}

/// 真机:包装 Platform Channel(BLE 控制 + Wi-Fi 传输)。
/// 真协议 PoC 通过前不会被 Demo 流程使用。
class NativeDeviceTransport implements DeviceTransport {
  NativeDeviceTransport({DeviceChannel? channel})
      : _channel = channel ?? DeviceChannel();

  final DeviceChannel _channel;

  @override
  Future<DeviceInfoSummary> connect() async {
    throw TransportException('DEV_1401',
        'native transport pending real protocol PoC (阶段0)');
  }

  @override
  Future<List<DeviceFileInfo>> listFiles() async {
    final files = await _channel.listFiles();
    return [
      for (final f in files)
        DeviceFileInfo(id: f.id, name: f.name, size: f.size,
            sha256: f.sha256, createdAt: f.createdAt, synced: f.synced),
    ];
  }

  @override
  Future<void> downloadFile(DeviceFileInfo file, String localPath,
      {void Function(int received, int total)? onProgress}) =>
      _channel.downloadFile(file.id, localPath);

  @override
  Future<void> ackSynced(String fileId) async {}

  @override
  Future<void> deleteFile(String fileId) => _channel.deleteDeviceFile(fileId);

  @override
  Future<void> disconnect() => _channel.disconnect();
}
