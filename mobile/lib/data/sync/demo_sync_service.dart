/// Demo 黄金流程编排(Phase 3):
/// 连接(Mock)设备 → 文件索引 → Range 下载(检查点续传 + Hash 校验)
/// → 写 SQLite → 云端登记 → 分片上传(UploadQueue)→ 创建 AI 任务。
///
/// 崩溃恢复:下载检查点 = 半文件长度(transport 层);上传检查点 = 服务端
/// pending 视图 + upload_queue 租约(Phase 2 已验证);任务状态由服务端持有。
library demo_sync_service;

import 'dart:io';

import 'package:crypto/crypto.dart' show sha256;
import 'package:sqflite/sqflite.dart';

import '../../platform/device_transport.dart';
import '../api/api_client.dart';
import '../upload/upload_queue.dart';

enum SyncStage { connecting, downloading, registering, uploading, creatingJob, done }

class SyncProgress {
  const SyncProgress(this.stage, {this.received = 0, this.total = 0});

  final SyncStage stage;
  final int received;
  final int total;
}

class DemoSyncResult {
  const DemoSyncResult({required this.recordingId, required this.jobId});

  final String recordingId;
  final String jobId;
}

class DemoSyncService {
  DemoSyncService({
    required this.transport,
    required this.db,
    required this.api,
    required this.localDir,
  }) : uploadQueue = UploadQueue(db, api);

  final DeviceTransport transport;
  final Database db;
  final ApiClient api;
  final String localDir;
  final UploadQueue uploadQueue;

  /// 同步单个设备文件走完黄金链;幂等:同文件重复调用不重复登记/扣费
  /// (服务端 recordings/jobs 均按 sha256/recording 判重)。
  Future<DemoSyncResult> syncFile(
    DeviceFileInfo file, {
    void Function(SyncProgress progress)? onProgress,
  }) async {
    onProgress?.call(const SyncProgress(SyncStage.downloading));
    final localPath = '$localDir/${file.id}.opus';
    await transport.downloadFile(file, localPath,
        onProgress: (r, t) =>
            onProgress?.call(SyncProgress(SyncStage.downloading,
                received: r, total: t)));

    final bytes = await File(localPath).readAsBytes();
    final digest = sha256.convert(bytes).toString();

    onProgress?.call(const SyncProgress(SyncStage.registering));
    final rec = await api.createRecording(
      title: file.name,
      durationMs: _estimateDurationMs(file.size),
      sha256: digest,
      sizeBytes: bytes.length,
      deviceFileId: file.id,
    );
    final recordingId = rec['id'] as String;

    // 本地库:recordings 镜像((sn,file_id,sha) 判重)+ 上传队列
    await db.insert(
      'recordings',
      {
        'id': recordingId,
        'title': file.name,
        'device_file_id': file.id,
        'sha256': digest,
        'size_bytes': bytes.length,
        'local_path': localPath,
        'local_status': 'synced',
        'cloud_status': rec['cloud_status'] ?? 'none',
        'created_at': DateTime.now().toIso8601String(),
      },
      conflictAlgorithm: ConflictAlgorithm.replace,
    );

    onProgress?.call(const SyncProgress(SyncStage.uploading));
    await uploadQueue.enqueue(recordingId, wifiOnly: false);
    await uploadQueue.drain(onWifi: true);
    final row = (await db.query('upload_queue',
        where: 'recording_id = ?', whereArgs: [recordingId])).single;
    if (row['status'] != 'done') {
      throw ApiException(row['error_code'] as String? ?? 'UPL_2001',
          'upload did not complete (${row['status']})',
          retryable: row['status'] == 'error');
    }

    onProgress?.call(const SyncProgress(SyncStage.creatingJob));
    final job = await api.createJob(recordingId);

    await transport.ackSynced(file.id);
    onProgress?.call(const SyncProgress(SyncStage.done));
    return DemoSyncResult(recordingId: recordingId, jobId: job['id'] as String);
  }

  /// 演示音频为伪数据,无真实时长;按 16KB/s 码率估算(mock_device 同一口径)。
  static int _estimateDurationMs(int sizeBytes) =>
      (sizeBytes / 16384 * 1000).round().clamp(60000, 3600000);
}
