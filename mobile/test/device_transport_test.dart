// MockHttpDeviceTransport 契约测试:用进程内 HttpServer 复刻 mock_device
// 协议(mock_device/protocol.md),覆盖连接、索引、Range 续传、Hash 校验。

import 'dart:convert';
import 'dart:io';

import 'package:crypto/crypto.dart' show sha256;
import 'package:flutter_test/flutter_test.dart';
import 'package:ysnote/platform/device_transport.dart';

class _FakeDevice {
  _FakeDevice(this.audio);

  final List<int> audio;
  late HttpServer server;
  bool wifiOn = false;
  bool acked = false;
  bool corrupt = false;
  final List<String> rangeRequests = [];

  String get fileSha => sha256.convert(audio).toString();

  Future<String> start() async {
    server = await HttpServer.bind('127.0.0.1', 0);
    server.listen(_handle);
    return 'http://127.0.0.1:${server.port}';
  }

  void _handle(HttpRequest req) async {
    final path = req.uri.path;
    if (path == '/cmd') {
      final body = jsonDecode(await utf8.decoder.bind(req).join()) as Map;
      final cmd = body['cmd'] as String;
      Object? data;
      switch (cmd) {
        case 'info.get':
          data = {'sn': 'MOCK-SN-01', 'model': 'MOCK-1',
                  'firmware': '0.9.0', 'protocol_version': '0.1'};
        case 'battery.get':
          data = {'percent': 88};
        case 'wifi.start':
          wifiOn = true;
          data = {'ssid': 'YS-TEST', 'password': 'x',
                  'url': 'http://127.0.0.1:${server.port}', 'ttl_s': 300};
        case 'wifi.stop':
          wifiOn = false;
          data = {'stopped': true};
        case 'files.list':
          data = {'files': [
            {'id': 'F0001', 'name': 'REC_0001.opus', 'size': audio.length,
             'sha256': fileSha, 'created_at': '2026-08-01T09:00:00Z',
             'synced': acked},
          ]};
        case 'files.delete':
          data = {'deleted': true};
        default:
          req.response
            ..write(jsonEncode({'seq': body['seq'], 'ok': false,
                                'error': 'E_BAD_CMD'}))
            ..close();
          return;
      }
      req.response
        ..headers.contentType = ContentType.json
        ..write(jsonEncode({'seq': body['seq'], 'ok': true, 'data': data}))
        ..close();
      return;
    }
    if (path == '/files/F0001') {
      final range = req.headers.value('range');
      rangeRequests.add(range ?? 'none');
      var start = 0;
      if (range != null && range.startsWith('bytes=')) {
        start = int.parse(range.substring(6).split('-').first);
      }
      // 模拟中断:每次最多回传 1000 字节(App 必须靠 Range 续传拼完整)
      var chunk = audio.sublist(start,
          (start + 1000 > audio.length) ? audio.length : start + 1000);
      if (corrupt) chunk = List<int>.filled(chunk.length, 0);
      req.response
        ..statusCode = start == 0 ? 200 : 206
        ..headers.contentType = ContentType.binary
        ..add(chunk)
        ..close();
      return;
    }
    if (path == '/files/F0001/ack') {
      acked = true;
      req.response
        ..headers.contentType = ContentType.json
        ..write(jsonEncode({'acked': true}))
        ..close();
      return;
    }
    req.response
      ..statusCode = 404
      ..close();
  }
}

void main() {
  late _FakeDevice device;
  late MockHttpDeviceTransport transport;
  late Directory tmp;

  setUp(() async {
    device = _FakeDevice(List.generate(3500, (i) => (i * 7) % 251));
    final url = await device.start();
    transport = MockHttpDeviceTransport(controlUrl: url);
    tmp = Directory.systemTemp.createTempSync('yst');
  });

  tearDown(() async {
    await device.server.close(force: true);
  });

  test('connect returns device info and enables wifi', () async {
    final info = await transport.connect();
    expect(info.sn, 'MOCK-SN-01');
    expect(info.battery, 88);
    expect(device.wifiOn, isTrue);
  });

  test('download reassembles via range requests, verifies hash, acks',
      () async {
    await transport.connect();
    final files = await transport.listFiles();
    expect(files.single.size, 3500);

    final progress = <int>[];
    final path = '${tmp.path}/rec.opus';
    await transport.downloadFile(files.single, path,
        onProgress: (r, t) => progress.add(r));
    // 3500 字节 / 每次 1000 → 4 次 Range 请求,断点递增
    expect(device.rangeRequests,
        ['bytes=0-', 'bytes=1000-', 'bytes=2000-', 'bytes=3000-']);
    expect(progress.last, 3500);
    final bytes = File(path).readAsBytesSync();
    expect(sha256.convert(bytes).toString(), files.single.sha256);

    await transport.ackSynced(files.single.id);
    expect(device.acked, isTrue);
  });

  test('resume from existing partial file (process-kill recovery)', () async {
    await transport.connect();
    final files = await transport.listFiles();
    final path = '${tmp.path}/rec.opus';
    // 模拟上次进程被杀:半文件已有前 1000 字节
    File(path).writeAsBytesSync(device.audio.sublist(0, 1000));

    await transport.downloadFile(files.single, path);
    expect(device.rangeRequests.first, 'bytes=1000-'); // 从检查点续传,不重下
    expect(File(path).lengthSync(), 3500);
    expect(sha256.convert(File(path).readAsBytesSync()).toString(),
        files.single.sha256);
  });

  test('hash mismatch deletes file and raises DEV_1204', () async {
    await transport.connect();
    final files = await transport.listFiles();
    device.corrupt = true;
    final path = '${tmp.path}/rec.opus';
    await expectLater(
      transport.downloadFile(files.single, path),
      throwsA(isA<TransportException>()
          .having((e) => e.code, 'code', 'DEV_1204')
          .having((e) => e.retryable, 'retryable', isTrue)),
    );
    expect(File(path).existsSync(), isFalse); // 损坏文件不留检查点
  });

  test('file operations before connect raise DEV_1202', () async {
    await expectLater(
      transport.downloadFile(
          const DeviceFileInfo(id: 'F0001', name: 'x', size: 1,
              sha256: 'x', createdAt: '', synced: false),
          '${tmp.path}/x'),
      throwsA(isA<TransportException>()
          .having((e) => e.code, 'code', 'DEV_1202')),
    );
  });
}
