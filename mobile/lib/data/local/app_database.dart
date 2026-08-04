import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';
import 'package:sqflite/sqflite.dart';

/// 本地 SQLite:录音元数据镜像 + 同步/上传队列(检查点持久化,App 被杀后恢复)。
/// 对应测试用例 SYNC-01/08(docs/06-test-plan.md §4)。
class AppDatabase {
  AppDatabase._(this.db);

  final Database db;

  static const _version = 1;

  static Future<AppDatabase> open() async {
    final dir = await getApplicationDocumentsDirectory();
    final db = await openDatabase(
      p.join(dir.path, 'ysnote.db'),
      version: _version,
      onCreate: (db, _) async {
        await db.execute('''
          CREATE TABLE recordings (
            id TEXT PRIMARY KEY,
            server_id TEXT,
            title TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'device',
            device_sn TEXT,
            device_file_id TEXT,
            duration_ms INTEGER,
            size_bytes INTEGER,
            sha256 TEXT,
            local_path TEXT,
            local_status TEXT NOT NULL DEFAULT 'none',
            cloud_status TEXT NOT NULL DEFAULT 'none',
            recorded_at TEXT,
            created_at TEXT NOT NULL
          )
        ''');
        // 重复文件识别:(device_sn, device_file_id, sha256) 判重
        await db.execute(
          'CREATE UNIQUE INDEX uq_rec_dev ON recordings (device_sn, device_file_id, sha256)');
        await db.execute('''
          CREATE TABLE sync_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_sn TEXT NOT NULL,
            file_id TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            downloaded_bytes INTEGER NOT NULL DEFAULT 0, -- Range 续传检查点
            sha256 TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', -- pending/downloading/verifying/done/error
            error_code TEXT,
            retry_count INTEGER NOT NULL DEFAULT 0,
            UNIQUE (device_sn, file_id)
          )
        ''');
        await db.execute('''
          CREATE TABLE upload_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recording_id TEXT NOT NULL,
            upload_id TEXT,               -- 服务端上传会话
            part_size INTEGER,
            total_parts INTEGER,
            status TEXT NOT NULL DEFAULT 'pending', -- pending/uploading/error/failed/done
            error_code TEXT,
            wifi_only INTEGER NOT NULL DEFAULT 1,
            lease_expires_at INTEGER,     -- 租约(P0-2):过期的 uploading 会被收回重跑
            UNIQUE (recording_id)
          )
        ''');
      },
    );
    return AppDatabase._(db);
  }
}
