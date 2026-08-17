/// 全局依赖装配(Demo Mode)。
///
/// Demo 端点默认指向本机联调环境:
///   API   http://127.0.0.1:8000   (uvicorn app.main:app)
///   设备  http://127.0.0.1:9100   (python -m mock_device.server)
/// 真机构建通过 --dart-define 覆盖;NativeDeviceTransport 在真机协议
/// PoC 通过后替换 transportProvider 实现,业务层不变。
library providers;

import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:path_provider/path_provider.dart';
import 'package:sqflite/sqflite.dart';

import '../data/api/api_client.dart';
import '../data/local/app_database.dart';
import '../data/sync/demo_sync_service.dart';
import '../platform/device_transport.dart';

const apiBaseUrl =
    String.fromEnvironment('YS_API_URL', defaultValue: 'http://127.0.0.1:8000');
const deviceControlUrl = String.fromEnvironment('YS_DEVICE_URL',
    defaultValue: 'http://127.0.0.1:9100');

/// 登录后写入;ApiClient 经拦截器携带
final authTokenProvider = StateProvider<String?>((ref) => null);

final apiClientProvider = Provider<ApiClient>((ref) {
  final dio = Dio(BaseOptions(baseUrl: apiBaseUrl));
  dio.interceptors.add(InterceptorsWrapper(onRequest: (options, handler) {
    final token = ref.read(authTokenProvider);
    if (token != null) {
      options.headers['Authorization'] = 'Bearer $token';
    }
    handler.next(options);
  }));
  return ApiClient(dio);
});

final deviceTransportProvider = Provider<DeviceTransport>(
    (ref) => MockHttpDeviceTransport(controlUrl: deviceControlUrl));

final databaseProvider = FutureProvider<Database>((ref) async {
  final appDb = await AppDatabase.open();
  return appDb.db;
});

final demoSyncServiceProvider = FutureProvider<DemoSyncService>((ref) async {
  final db = await ref.watch(databaseProvider.future);
  final dir = await getApplicationDocumentsDirectory();
  return DemoSyncService(
    transport: ref.watch(deviceTransportProvider),
    db: db,
    api: ref.watch(apiClientProvider),
    localDir: dir.path,
  );
});
