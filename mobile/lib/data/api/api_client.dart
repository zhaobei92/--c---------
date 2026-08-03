import 'package:dio/dio.dart';

/// 服务端 API 客户端骨架(docs/04-api-spec.md)。
/// 错误统一转 [ApiException];retryable 语义见 docs/07-error-codes.md。
class ApiException implements Exception {
  ApiException(this.code, this.message, {this.retryable = false});

  final String code;
  final String message;
  final bool retryable;

  @override
  String toString() => 'ApiException($code: $message)';
}

class UploadInit {
  UploadInit({
    required this.deduplicated,
    this.uploadId = '',
    this.partSize = 0,
    this.parts = const [],
  });

  final bool deduplicated;
  final String uploadId;
  final int partSize;
  final List<int> parts;
}

class ApiClient {
  ApiClient(this._dio);

  final Dio _dio;

  static const _retryableCodes = {
    'AUTH_0003', 'DEV_1201', 'DEV_1202', 'DEV_1203', 'DEV_1205', 'DEV_1303',
    'UPL_2001', 'UPL_2101', 'UPL_2103', 'UPL_2104',
    'JOB_4101', 'JOB_4102', 'JOB_4103', 'JOB_4104', 'JOB_4105',
    'ORD_5001', 'ORD_5002', 'DOC_6001', 'DOC_6004',
    'SYS_9001', 'SYS_9002', 'SYS_9005',
  };

  Future<T> _call<T>(Future<Response<dynamic>> Function() fn, T Function(dynamic) parse) async {
    try {
      final resp = await fn();
      return parse(resp.data);
    } on DioException catch (e) {
      final err = (e.response?.data is Map) ? (e.response!.data['error'] as Map?) : null;
      final code = (err?['code'] as String?) ?? 'SYS_9005';
      throw ApiException(code, (err?['message'] as String?) ?? e.message ?? '',
          retryable: _retryableCodes.contains(code));
    }
  }

  Future<UploadInit> initUpload(String recordingId) => _call(
        () => _dio.post('/v1/uploads/init', data: {'recording_id': recordingId}),
        (d) => d['deduplicated'] == true
            ? UploadInit(deduplicated: true)
            : UploadInit(
                deduplicated: false,
                uploadId: d['upload_id'] as String,
                partSize: d['part_size'] as int,
                parts: (d['parts'] as List).map((p) => p['part_no'] as int).toList(),
              ),
      );

  Future<List<int>> pendingParts(String uploadId) => _call(
        () => _dio.get('/v1/uploads/$uploadId'),
        (d) => (d['pending_parts'] as List).cast<int>(),
      );

  Future<void> uploadPart(String uploadId, String recordingId, int partNo) async {
    // 骨架:读本地文件分片 → PUT 预签名 URL → 登记
    await _call(
      () => _dio.post('/v1/uploads/$uploadId/parts/$partNo/complete',
          data: {'etag': 'pending-native-impl', 'size_bytes': 0}),
      (_) {},
    );
  }

  Future<void> completeUpload(String uploadId) =>
      _call(() => _dio.post('/v1/uploads/$uploadId/complete'), (_) {});

  Future<void> createJob(String recordingId) =>
      _call(() => _dio.post('/v1/jobs', data: {'recording_id': recordingId}), (_) {});
}
