// P0-2(上传中被杀任务卡死)与上传契约对齐的回归测试。
// 使用 sqflite_common_ffi 在 CI/桌面环境运行真实 SQLite。

import 'dart:io';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';
import 'package:ysnote/data/api/api_client.dart';
import 'package:ysnote/data/upload/upload_queue.dart';

class FakeApi implements ApiClient {
  final List<String> calls = [];
  final Map<int, Uint8List> putChunks = {};
  bool deduplicated = false;
  ApiException? failWith;
  int totalParts = 3;
  int partSize = 4;

  @override
  Future<UploadInit> initUpload(String recordingId,
      {required int sizeBytes, required String sha256, int? partSize}) async {
    calls.add('init:$recordingId:$sizeBytes:$sha256');
    if (failWith != null) throw failWith!;
    if (deduplicated) return UploadInit(deduplicated: true);
    return UploadInit(
      deduplicated: false,
      uploadId: 'up-1',
      partSize: this.partSize,
      parts: [
        for (var n = 1; n <= totalParts; n++)
          UploadPartInfo(partNo: n, putUrl: 'http://store.local/p/$n'),
      ],
    );
  }

  @override
  Future<UploadProgress> progress(String uploadId) async {
    calls.add('progress:$uploadId');
    final pending = <int, String>{
      for (var n = 1; n <= totalParts; n++)
        if (!putChunks.containsKey(n)) n: 'http://store.local/p/$n',
    };
    return UploadProgress(pendingParts: pending, totalParts: totalParts);
  }

  @override
  Future<void> uploadPart(
      {required String uploadId,
      required int partNo,
      required String putUrl,
      required Uint8List chunk}) async {
    calls.add('put:$partNo:${chunk.length}');
    putChunks[partNo] = chunk;
  }

  @override
  Future<void> completeUpload(String uploadId) async {
    calls.add('complete:$uploadId');
  }

  @override
  Future<void> createJob(String recordingId) async {}
}

Future<Database> _openDb() async {
  final db = await databaseFactoryFfi.openDatabase(inMemoryDatabasePath);
  await db.execute('''
    CREATE TABLE recordings (
      id TEXT PRIMARY KEY, title TEXT, sha256 TEXT,
      size_bytes INTEGER, local_path TEXT
    )
  ''');
  await db.execute('''
    CREATE TABLE upload_queue (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      recording_id TEXT NOT NULL,
      upload_id TEXT, part_size INTEGER, total_parts INTEGER,
      status TEXT NOT NULL DEFAULT 'pending',
      error_code TEXT, wifi_only INTEGER NOT NULL DEFAULT 1,
      lease_expires_at INTEGER,
      UNIQUE (recording_id)
    )
  ''');
  return db;
}

void main() {
  sqfliteFfiInit();

  late Database db;
  late FakeApi api;
  late UploadQueue queue;
  late File audio;

  setUp(() async {
    db = await _openDb();
    api = FakeApi();
    queue = UploadQueue(db, api);
    audio = File(
        '${Directory.systemTemp.createTempSync('ysq').path}/rec.opus');
    await audio.writeAsBytes(List.generate(10, (i) => i)); // 10 字节 → 3 片(4/4/2)
    await db.insert('recordings', {
      'id': 'rec-1', 'title': 't', 'sha256': 'x' * 64,
      'size_bytes': 10, 'local_path': audio.path,
    });
  });

  tearDown(() async => db.close());

  test('full drain uploads real chunks and completes', () async {
    await queue.enqueue('rec-1');
    await queue.drain(onWifi: true);

    final row = (await db.query('upload_queue')).single;
    expect(row['status'], 'done');
    // init 携带完整契约(size + sha)
    expect(api.calls.first, 'init:rec-1:10:${'x' * 64}');
    // 三个分片,内容为文件真实区间
    expect(api.putChunks[1], Uint8List.fromList([0, 1, 2, 3]));
    expect(api.putChunks[2], Uint8List.fromList([4, 5, 6, 7]));
    expect(api.putChunks[3], Uint8List.fromList([8, 9]));
    expect(api.calls.last, 'complete:up-1');
  });

  test('stale uploading task is recovered after process kill (P0-2)', () async {
    // 模拟上传中被杀:状态 uploading,租约已过期
    await db.insert('upload_queue', {
      'recording_id': 'rec-1', 'status': 'uploading',
      'lease_expires_at':
          DateTime.now().subtract(const Duration(hours: 1)).millisecondsSinceEpoch,
    });
    await queue.drain(onWifi: true);
    final row = (await db.query('upload_queue')).single;
    expect(row['status'], 'done'); // 收回 → 重跑 → 完成
  });

  test('live lease is not stolen by concurrent drain', () async {
    await db.insert('upload_queue', {
      'recording_id': 'rec-1', 'status': 'uploading',
      'lease_expires_at':
          DateTime.now().add(const Duration(minutes: 5)).millisecondsSinceEpoch,
    });
    await queue.drain(onWifi: true);
    final row = (await db.query('upload_queue')).single;
    expect(row['status'], 'uploading'); // 有效租约不被抢占
    expect(api.calls, isEmpty);
  });

  test('retryable failure marks error and keeps task for next drain', () async {
    api.failWith = ApiException('UPL_2001', 'boom', retryable: true);
    await queue.enqueue('rec-1');
    await queue.drain(onWifi: true);
    var row = (await db.query('upload_queue')).single;
    expect(row['status'], 'error');
    expect(row['error_code'], 'UPL_2001');

    api.failWith = null; // 故障恢复后可续跑
    await queue.drain(onWifi: true);
    row = (await db.query('upload_queue')).single;
    expect(row['status'], 'done');
  });

  test('non-retryable failure marks failed', () async {
    api.failWith = ApiException('UPL_2002', 'too large', retryable: false);
    await queue.enqueue('rec-1');
    await queue.drain(onWifi: true);
    expect((await db.query('upload_queue')).single['status'], 'failed');
  });

  test('wifi_only task skipped on cellular', () async {
    await queue.enqueue('rec-1', wifiOnly: true);
    await queue.drain(onWifi: false);
    expect((await db.query('upload_queue')).single['status'], 'pending');
    expect(api.calls, isEmpty);
  });

  test('deduplicated file finishes without part uploads', () async {
    api.deduplicated = true;
    await queue.enqueue('rec-1');
    await queue.drain(onWifi: true);
    expect((await db.query('upload_queue')).single['status'], 'done');
    expect(api.putChunks, isEmpty);
  });
}
