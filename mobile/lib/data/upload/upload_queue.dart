import 'dart:io';
import 'dart:typed_data';

import 'package:sqflite/sqflite.dart';

import '../api/api_client.dart';

/// 分片上传队列:后台队列、失败重试、进度、断点续传。
///
/// 行为契约(与 docs/04-api-spec.md §4 及 server/tests/test_upload_contract.py 对应):
///  1. init 发送完整契约(recording_id / size_bytes / sha256);同 sha256 直接去重。
///  2. 逐分片:读本地文件对应区间 → PUT 预签名 URL → 以真实 etag/size 登记 →
///     记检查点(uploaded_parts)。
///  3. 恢复(App 重启 / 网络恢复 / WorkManager-BGTask 唤醒):
///     - 先把租约过期的 `uploading` 任务收回为 `pending`(P0-2 修复:
///       上传中被杀死的任务不会永久卡死);
///     - 以服务端 GET /uploads/{id} 的 pending_parts + 新 put_url 续传。
///  4. 领取任务用 SQLite 事务 + 租约(lease_expires_at),防止两个后台
///     任务同时处理同一条队列。
///  5. wifi_only=1 时蜂窝网络暂停队列(SYNC-07)。
class UploadQueue {
  UploadQueue(this.db, this.api, {this.leaseDuration = const Duration(minutes: 10)});

  final Database db;
  final ApiClient api;
  final Duration leaseDuration;

  Future<void> enqueue(String recordingId, {bool wifiOnly = true}) async {
    await db.insert(
      'upload_queue',
      {'recording_id': recordingId, 'wifi_only': wifiOnly ? 1 : 0},
      conflictAlgorithm: ConflictAlgorithm.ignore, // 同录音只排队一次
    );
  }

  /// P0-2 修复:回收租约过期的 uploading 任务(进程被杀后的遗留状态)。
  Future<int> recoverStaleLeases() async {
    return db.update(
      'upload_queue',
      {'status': 'pending'},
      where: "status = 'uploading' AND (lease_expires_at IS NULL OR lease_expires_at < ?)",
      whereArgs: [DateTime.now().millisecondsSinceEpoch],
    );
  }

  /// 恢复并推进队列。每次 drain 中每个任务最多处理一次,
  /// 失败任务(error)留待下一次 drain,避免同一轮内死循环重试。
  Future<void> drain({required bool onWifi}) async {
    await recoverStaleLeases();
    final attempted = <int>{};
    while (true) {
      final row = await _claimNext(onWifi: onWifi, exclude: attempted);
      if (row == null) break;
      attempted.add(row['id'] as int);
      await _process(row);
    }
  }

  /// 事务内领取下一条任务并写入租约,防止并发双取。
  Future<Map<String, Object?>?> _claimNext(
      {required bool onWifi, required Set<int> exclude}) async {
    return db.transaction((txn) async {
      final where = StringBuffer("status IN ('pending', 'error')");
      if (!onWifi) where.write(' AND wifi_only = 0');
      if (exclude.isNotEmpty) {
        where.write(' AND id NOT IN (${exclude.join(',')})');
      }
      final rows = await txn.query('upload_queue',
          where: where.toString(), orderBy: 'id', limit: 1);
      if (rows.isEmpty) return null;
      final row = rows.first;
      await txn.update(
        'upload_queue',
        {
          'status': 'uploading',
          'lease_expires_at':
              DateTime.now().add(leaseDuration).millisecondsSinceEpoch,
        },
        where: 'id = ?',
        whereArgs: [row['id']],
      );
      return row;
    });
  }

  Future<void> _process(Map<String, Object?> row) async {
    final id = row['id'] as int;
    final recordingId = row['recording_id'] as String;
    try {
      final rec = await _recording(recordingId);
      final localPath = rec['local_path'] as String?;
      final sha256 = rec['sha256'] as String?;
      final sizeBytes = rec['size_bytes'] as int?;
      if (localPath == null || sha256 == null || sizeBytes == null) {
        throw ApiException('SYS_9004', 'recording missing local file metadata');
      }

      var uploadId = row['upload_id'] as String?;
      var partSize = row['part_size'] as int?;
      if (uploadId == null) {
        final init = await api.initUpload(recordingId,
            sizeBytes: sizeBytes, sha256: sha256);
        if (init.deduplicated) {
          await _finish(id, 'done');
          return;
        }
        uploadId = init.uploadId;
        partSize = init.partSize;
        await db.update(
          'upload_queue',
          {
            'upload_id': uploadId,
            'part_size': partSize,
            'total_parts': init.parts.length,
          },
          where: 'id = ?',
          whereArgs: [id],
        );
      }

      // 以服务端为准的续传视图:pending 分片 + 新 put_url
      final progress = await api.progress(uploadId);
      final file = await File(localPath).open();
      try {
        for (final entry in progress.pendingParts.entries) {
          final partNo = entry.key;
          final chunk = await _readChunk(file, partNo, partSize!, sizeBytes);
          await api.uploadPart(
              uploadId: uploadId, partNo: partNo, putUrl: entry.value, chunk: chunk);
          await _renewLease(id); // 每分片续租,长文件不因租约过期被并发抢占
        }
      } finally {
        await file.close();
      }

      await api.completeUpload(uploadId);
      await _finish(id, 'done');
    } on ApiException catch (e) {
      await db.update(
        'upload_queue',
        {
          'status': e.retryable ? 'error' : 'failed',
          'error_code': e.code,
          'lease_expires_at': null,
        },
        where: 'id = ?',
        whereArgs: [id],
      );
    }
  }

  Future<Uint8List> _readChunk(
      RandomAccessFile file, int partNo, int partSize, int totalSize) async {
    final start = (partNo - 1) * partSize;
    final length = (start + partSize > totalSize) ? totalSize - start : partSize;
    await file.setPosition(start);
    return file.read(length);
  }

  Future<void> _renewLease(int id) => db.update(
        'upload_queue',
        {'lease_expires_at': DateTime.now().add(leaseDuration).millisecondsSinceEpoch},
        where: 'id = ?',
        whereArgs: [id],
      );

  Future<void> _finish(int id, String status) => db.update(
        'upload_queue',
        {'status': status, 'lease_expires_at': null, 'error_code': null},
        where: 'id = ?',
        whereArgs: [id],
      );

  Future<Map<String, Object?>> _recording(String recordingId) async {
    final rows = await db
        .query('recordings', where: 'id = ?', whereArgs: [recordingId], limit: 1);
    if (rows.isEmpty) {
      throw ApiException('DOC_6003', 'recording not found locally');
    }
    return rows.first;
  }
}
