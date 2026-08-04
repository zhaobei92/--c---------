import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';
import 'package:sqflite/sqflite.dart';

/// 本地 SQLite:录音元数据镜像 + 同步/上传队列(检查点持久化,App 被杀后恢复)。
/// 对应测试用例 SYNC-01/08(docs/06-test-plan.md §4)。
///
/// 迁移规约:**禁止直接修改已有版本的 onCreate 结构**;任何结构变化必须
/// 提升 [schemaVersion] 并在 [_onUpgrade] 增加迁移分支 + 迁移测试
/// (test/app_database_migration_test.dart)。
class AppDatabase {
  AppDatabase._(this.db);

  final Database db;

  /// v1:首版(commit 027b2c7,upload_queue 含 uploaded_parts)
  /// v2:upload_queue 增加 lease_expires_at(租约恢复,P0-2);
  ///     uploaded_parts 废弃不再读写(旧库保留列,无害)
  static const int schemaVersion = 2;

  static Future<AppDatabase> open() async {
    final dir = await getApplicationDocumentsDirectory();
    return AppDatabase._(await openAt(p.join(dir.path, 'ysnote.db')));
  }

  /// 测试注入入口:CI/桌面用 sqflite_common_ffi 的 factory 跑真实迁移。
  static Future<Database> openAt(String path, {DatabaseFactory? factory}) {
    final f = factory ?? databaseFactory;
    return f.openDatabase(
      path,
      options: OpenDatabaseOptions(
        version: schemaVersion,
        onCreate: _onCreate,
        onUpgrade: _onUpgrade,
      ),
    );
  }

  static Future<void> _onCreate(Database db, int version) async {
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
  }

  static Future<void> _onUpgrade(Database db, int from, int to) async {
    if (from < 2) {
      // v1 → v2:旧库补租约列(uploaded_parts 列保留但不再使用)
      await db.execute(
          'ALTER TABLE upload_queue ADD COLUMN lease_expires_at INTEGER');
    }
  }
}
