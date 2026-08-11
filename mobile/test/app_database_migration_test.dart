// 移动端数据库迁移回归测试:v1(含 uploaded_parts、无 lease_expires_at)的旧库
// 升级到 v2 后,新列可用且旧数据保留。
// 规约:禁止直接修改已有版本的 onCreate 结构而不写迁移。

import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';
import 'package:ysnote/data/local/app_database.dart';

Future<void> _createV1(String path) async {
  final db = await databaseFactoryFfi.openDatabase(
    path,
    options: OpenDatabaseOptions(
      version: 1,
      onCreate: (db, _) async {
        // 第一版发布时的真实结构(commit 027b2c7)
        await db.execute('''
          CREATE TABLE recordings (
            id TEXT PRIMARY KEY, server_id TEXT, title TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'device', device_sn TEXT,
            device_file_id TEXT, duration_ms INTEGER, size_bytes INTEGER,
            sha256 TEXT, local_path TEXT,
            local_status TEXT NOT NULL DEFAULT 'none',
            cloud_status TEXT NOT NULL DEFAULT 'none',
            recorded_at TEXT, created_at TEXT NOT NULL
          )
        ''');
        await db.execute('''
          CREATE TABLE sync_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_sn TEXT NOT NULL, file_id TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            downloaded_bytes INTEGER NOT NULL DEFAULT 0,
            sha256 TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            error_code TEXT, retry_count INTEGER NOT NULL DEFAULT 0,
            UNIQUE (device_sn, file_id)
          )
        ''');
        await db.execute('''
          CREATE TABLE upload_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recording_id TEXT NOT NULL, upload_id TEXT,
            part_size INTEGER, total_parts INTEGER,
            uploaded_parts TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'pending',
            error_code TEXT, wifi_only INTEGER NOT NULL DEFAULT 1,
            UNIQUE (recording_id)
          )
        ''');
      },
    ),
  );
  await db.insert('upload_queue',
      {'recording_id': 'legacy-rec', 'status': 'pending', 'wifi_only': 1});
  await db.close();
}

void main() {
  sqfliteFfiInit();

  test('v1 database upgrades to v2 with lease_expires_at, data preserved',
      () async {
    final dir = Directory.systemTemp.createTempSync('ysmig');
    final path = '${dir.path}/ysnote.db';
    await _createV1(path);

    final db = await AppDatabase.openAt(path, factory: databaseFactoryFfi);
    // 新列可用(旧库上直接查询,修复前会抛 no such column)
    final rows = await db.query('upload_queue',
        where: 'lease_expires_at IS NULL', orderBy: 'id');
    expect(rows, hasLength(1));
    expect(rows.first['recording_id'], 'legacy-rec'); // 旧数据保留
    final version =
        (await db.rawQuery('PRAGMA user_version')).first.values.first as int;
    expect(version, AppDatabase.schemaVersion);
    await db.close();
  });

  test('fresh install creates v2 schema directly', () async {
    final dir = Directory.systemTemp.createTempSync('ysmig2');
    final db = await AppDatabase.openAt('${dir.path}/fresh.db',
        factory: databaseFactoryFfi);
    final cols = await db.rawQuery('PRAGMA table_info(upload_queue)');
    expect(cols.map((c) => c['name']), contains('lease_expires_at'));
    await db.close();
  });
}
