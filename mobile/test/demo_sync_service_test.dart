// Demo 黄金流程编排测试:设备下载 → SQLite → 上传 → 建任务的完整链路
// (fake transport / fake api / 真实 SQLite,与 upload_queue_test 同一 FakeApi 风格)。

import 'dart:io';
import 'dart:typed_data';

import 'package:crypto/crypto.dart' show sha256;
import 'package:flutter_test/flutter_test.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';
import 'package:ysnote/data/api/api_client.dart';
import 'package:ysnote/data/sync/demo_sync_service.dart';
import 'package:ysnote/platform/device_transport.dart';

final _audio = Uint8List.fromList(List.generate(64, (i) => i));

class _FakeTransport implements DeviceTransport {
  final acked = <String>[];

  @override
  Future<DeviceInfoSummary> connect() async =>
      const DeviceInfoSummary(sn: 'SN', model: 'M', firmware: '1', battery: 90);

  @override
  Future<List<DeviceFileInfo>> listFiles() async => [
        DeviceFileInfo(id: 'F1', name: 'REC.opus', size: _audio.length,
            sha256: sha256.convert(_audio).toString(),
            createdAt: '', synced: false),
      ];

  @override
  Future<void> downloadFile(DeviceFileInfo file, String localPath,
      {void Function(int, int)? onProgress}) async {
    File(localPath).writeAsBytesSync(_audio);
    onProgress?.call(file.size, file.size);
  }

  @override
  Future<void> ackSynced(String fileId) async => acked.add(fileId);

  @override
  Future<void> deleteFile(String fileId) async {}

  @override
  Future<void> disconnect() async {}
}

class _FakeApi implements ApiClient {
  final calls = <String>[];
  bool uploadFails = false;

  @override
  Future<Map<String, dynamic>> createRecording(
      {required String title, required int durationMs, required String sha256,
       required int sizeBytes, String source = 'device',
       String? deviceSn, String? deviceFileId}) async {
    calls.add('createRecording:$sha256');
    return {'id': 'rec-1', 'cloud_status': 'none', 'deduplicated': false};
  }

  @override
  Future<UploadInit> initUpload(String recordingId,
      {required int sizeBytes, required String sha256, int? partSize}) async {
    calls.add('initUpload');
    if (uploadFails) throw ApiException('UPL_2002', 'too big');
    return UploadInit(deduplicated: true); // 走去重捷径,上传链路已有专测
  }

  @override
  Future<Map<String, dynamic>> createJob(String recordingId) async {
    calls.add('createJob:$recordingId');
    return {'id': 'job-1', 'status': 'waiting'};
  }

  @override
  Future<UploadProgress> progress(String uploadId) async =>
      UploadProgress(pendingParts: const {}, totalParts: 0);

  @override
  Future<void> uploadPart({required String uploadId, required int partNo,
      required String putUrl, required Uint8List chunk}) async {}

  @override
  Future<String?> sendCode(String email) async => '000000';

  @override
  Future<String> verifyCode(String email, String code) async => 'token';

  @override
  Future<void> completeUpload(String uploadId) async {}

  @override
  Future<Map<String, dynamic>> getJob(String jobId) async => {'id': jobId};

  @override
  Future<List<Map<String, dynamic>>> getTranscript(String recordingId) async => [];

  @override
  Future<Map<String, dynamic>> getSummary(String recordingId) async => {};
}

Future<Database> _db() async {
  final db = await databaseFactoryFfi.openDatabase(inMemoryDatabasePath);
  await db.execute('''
    CREATE TABLE recordings (
      id TEXT PRIMARY KEY, title TEXT, device_file_id TEXT, sha256 TEXT,
      size_bytes INTEGER, local_path TEXT, local_status TEXT,
      cloud_status TEXT, created_at TEXT
    )
  ''');
  await db.execute('''
    CREATE TABLE upload_queue (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      recording_id TEXT NOT NULL, upload_id TEXT, part_size INTEGER,
      total_parts INTEGER, status TEXT NOT NULL DEFAULT 'pending',
      error_code TEXT, wifi_only INTEGER NOT NULL DEFAULT 1,
      lease_expires_at INTEGER, UNIQUE (recording_id)
    )
  ''');
  return db;
}

void main() {
  sqfliteFfiInit();

  test('full demo chain: download → sqlite → upload → job → ack', () async {
    final db = await _db();
    final transport = _FakeTransport();
    final api = _FakeApi();
    final tmp = Directory.systemTemp.createTempSync('ysd');
    final service = DemoSyncService(
        transport: transport, db: db, api: api, localDir: tmp.path);

    final stages = <SyncStage>[];
    final file = (await transport.listFiles()).single;
    final result = await service.syncFile(file,
        onProgress: (p) => stages.add(p.stage));

    expect(result.recordingId, 'rec-1');
    expect(result.jobId, 'job-1');
    // 阶段完整且有序
    expect(stages.first, SyncStage.downloading);
    expect(stages.last, SyncStage.done);
    expect(stages, containsAllInOrder([
      SyncStage.downloading, SyncStage.registering,
      SyncStage.uploading, SyncStage.creatingJob, SyncStage.done,
    ]));
    // SQLite 镜像落库
    final rec = (await db.query('recordings')).single;
    expect(rec['device_file_id'], 'F1');
    expect(rec['sha256'], sha256.convert(_audio).toString());
    // 设备侧确认
    expect(transport.acked, ['F1']);
    // API 调用顺序
    expect(api.calls, [
      'createRecording:${sha256.convert(_audio).toString()}',
      'initUpload',
      'createJob:rec-1',
    ]);
    await db.close();
  });

  test('upload failure surfaces error and does not create job', () async {
    final db = await _db();
    final transport = _FakeTransport();
    final api = _FakeApi()..uploadFails = true;
    final tmp = Directory.systemTemp.createTempSync('ysd2');
    final service = DemoSyncService(
        transport: transport, db: db, api: api, localDir: tmp.path);

    final file = (await transport.listFiles()).single;
    await expectLater(service.syncFile(file), throwsA(isA<ApiException>()));
    expect(api.calls.where((c) => c.startsWith('createJob')), isEmpty);
    expect(transport.acked, isEmpty); // 未完成不向设备确认
    await db.close();
  });
}
