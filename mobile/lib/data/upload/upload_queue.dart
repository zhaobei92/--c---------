import 'dart:convert';

import 'package:sqflite/sqflite.dart';

import '../api/api_client.dart';

/// 分片上传队列:后台队列、失败重试、进度、断点续传(检查点存 upload_queue 表)。
///
/// 行为契约(与 docs/04-api-spec.md §4 对应):
///  1. init → 服务端返回 upload_id + 分片预签名 URL(同 sha256 直接去重返回)。
///  2. 逐分片 PUT,成功后登记并把 part_no 追加进 uploaded_parts 检查点。
///  3. App 重启/切后台恢复时,以 uploaded_parts 与 GET /uploads/{id} 求差集续传。
///  4. complete 合并;UPL_2103(hash 不一致)按 retryable 重传。
///  5. wifi_only=1 时蜂窝网络暂停队列(SYNC-07)。
class UploadQueue {
  UploadQueue(this.db, this.api);

  final Database db;
  final ApiClient api;

  Future<void> enqueue(String recordingId, {bool wifiOnly = true}) async {
    await db.insert(
      'upload_queue',
      {'recording_id': recordingId, 'wifi_only': wifiOnly ? 1 : 0},
      conflictAlgorithm: ConflictAlgorithm.ignore, // 同录音只排队一次
    );
  }

  /// 恢复并推进队列(前台恢复 / 网络恢复 / WorkManager-BGTask 唤醒时调用)。
  Future<void> drain({required bool onWifi}) async {
    final rows = await db.query(
      'upload_queue',
      where: "status IN ('pending', 'error')",
      orderBy: 'id',
    );
    for (final row in rows) {
      if ((row['wifi_only'] as int) == 1 && !onWifi) continue;
      await _process(row);
    }
  }

  Future<void> _process(Map<String, Object?> row) async {
    final id = row['id'] as int;
    final recordingId = row['recording_id'] as String;
    try {
      await db.update('upload_queue', {'status': 'uploading'},
          where: 'id = ?', whereArgs: [id]);

      var uploadId = row['upload_id'] as String?;
      var uploaded = (jsonDecode((row['uploaded_parts'] as String?) ?? '[]') as List)
          .cast<int>()
          .toSet();

      if (uploadId == null) {
        final init = await api.initUpload(recordingId);
        if (init.deduplicated) {
          await db.update('upload_queue', {'status': 'done'},
              where: 'id = ?', whereArgs: [id]);
          return;
        }
        uploadId = init.uploadId;
        uploaded = {};
        await db.update(
          'upload_queue',
          {
            'upload_id': uploadId,
            'part_size': init.partSize,
            'total_parts': init.parts.length,
            'uploaded_parts': '[]',
          },
          where: 'id = ?',
          whereArgs: [id],
        );
      }

      final pending = await api.pendingParts(uploadId);
      for (final partNo in pending) {
        if (uploaded.contains(partNo)) continue;
        await api.uploadPart(uploadId, recordingId, partNo);
        uploaded.add(partNo);
        await db.update('upload_queue', {'uploaded_parts': jsonEncode(uploaded.toList())},
            where: 'id = ?', whereArgs: [id]); // 每分片一个检查点
      }

      await api.completeUpload(uploadId);
      await db.update('upload_queue', {'status': 'done'},
          where: 'id = ?', whereArgs: [id]);
    } on ApiException catch (e) {
      await db.update(
        'upload_queue',
        {'status': e.retryable ? 'error' : 'failed', 'error_code': e.code},
        where: 'id = ?',
        whereArgs: [id],
      );
    }
  }
}
