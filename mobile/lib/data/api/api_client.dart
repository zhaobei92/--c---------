import 'dart:typed_data';

import 'package:dio/dio.dart';

/// 服务端 API 客户端(docs/04-api-spec.md)。
/// 上传契约与 server/tests/test_upload_contract.py 保持同步。
/// 错误统一转 [ApiException];retryable 语义见 docs/07-error-codes.md。
class ApiException implements Exception {
  ApiException(this.code, this.message, {this.retryable = false});

  final String code;
  final String message;
  final bool retryable;

  @override
  String toString() => 'ApiException($code: $message)';
}

class UploadPartInfo {
  UploadPartInfo({required this.partNo, required this.putUrl});

  final int partNo;
  final String putUrl;

  factory UploadPartInfo.fromJson(Map<String, dynamic> j) =>
      UploadPartInfo(partNo: j['part_no'] as int, putUrl: j['put_url'] as String);
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
  final List<UploadPartInfo> parts;
}

class UploadProgress {
  UploadProgress({required this.pendingParts, required this.totalParts});

  /// 未完成分片 → 重新签发的 put_url(旧预签名可能已过期)
  final Map<int, String> pendingParts;
  final int totalParts;
}

class ApiClient {
  /// [storageDio] 专用于对象存储的预签名 PUT:独立实例,
  /// **不携带** API 的 Authorization 拦截器(不能把用户 token 发给存储域)。
  ApiClient(this._dio, {Dio? storageDio}) : _storageDio = storageDio ?? Dio();

  final Dio _dio;
  final Dio _storageDio;

  static const _retryableCodes = {
    'AUTH_0003', 'DEV_1201', 'DEV_1202', 'DEV_1203', 'DEV_1205', 'DEV_1303',
    'UPL_2001', 'UPL_2101', 'UPL_2103', 'UPL_2104',
    'JOB_4101', 'JOB_4102', 'JOB_4103', 'JOB_4104', 'JOB_4105',
    'ORD_5001', 'ORD_5002', 'DOC_6001', 'DOC_6004',
    'SYS_9001', 'SYS_9002', 'SYS_9005',
  };

  Future<T> _call<T>(
      Future<Response<dynamic>> Function() fn, T Function(dynamic) parse) async {
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

  /// init 契约:必填 recording_id / size_bytes / sha256(服务端校验与录音登记一致)。
  /// dev/staging 返回 dev_code 便于 Demo 一键登录;prod 不回显。
  Future<String?> sendCode(String email) => _call(
        () => _dio.post('/v1/auth/email/code', data: {'email': email}),
        (d) => (d as Map)['dev_code'] as String?,
      );

  Future<String> verifyCode(String email, String code) => _call(
        () => _dio.post('/v1/auth/email/verify',
            data: {'email': email, 'code': code}),
        (d) => (d as Map)['access_token'] as String,
      );

  Future<UploadInit> initUpload(
    String recordingId, {
    required int sizeBytes,
    required String sha256,
    int? partSize,
  }) =>
      _call(
        () => _dio.post('/v1/uploads/init', data: {
          'recording_id': recordingId,
          'size_bytes': sizeBytes,
          'sha256': sha256,
          if (partSize != null) 'part_size': partSize,
        }),
        (d) => d['deduplicated'] == true
            ? UploadInit(deduplicated: true)
            : UploadInit(
                deduplicated: false,
                uploadId: d['upload_id'] as String,
                partSize: d['part_size'] as int,
                parts: (d['parts'] as List)
                    .map((p) => UploadPartInfo.fromJson((p as Map).cast()))
                    .toList(),
              ),
      );

  /// 断点续传:未完成分片列表 + 新 put_url。
  Future<UploadProgress> progress(String uploadId) => _call(
        () => _dio.get('/v1/uploads/$uploadId'),
        (d) => UploadProgress(
          pendingParts: {
            for (final p in d['parts'] as List)
              (p as Map)['part_no'] as int: p['put_url'] as String,
          },
          totalParts: d['total_parts'] as int,
        ),
      );

  /// 真实分片上传:PUT 数据到预签名 URL → 以真实 etag/size 向服务端登记。
  Future<void> uploadPart({
    required String uploadId,
    required int partNo,
    required String putUrl,
    required Uint8List chunk,
  }) async {
    Response<dynamic> putResp;
    try {
      putResp = await _storageDio.put(
        putUrl,
        data: Stream.fromIterable([chunk]),
        options: Options(headers: {
          Headers.contentLengthHeader: chunk.length,
          Headers.contentTypeHeader: 'application/octet-stream',
        }),
      );
    } on DioException catch (e) {
      throw ApiException('UPL_2101', 'part $partNo PUT failed: ${e.message}',
          retryable: true);
    }
    // S3/MinIO 均返回 ETag;缺失视为存储异常重试,禁止伪造 etag 提交登记
    final rawEtag = putResp.headers.value('etag');
    if (rawEtag == null || rawEtag.isEmpty) {
      throw ApiException('UPL_2101', 'storage did not return ETag for part $partNo',
          retryable: true);
    }
    final etag = rawEtag.replaceAll('"', '');
    await _call(
      () => _dio.post('/v1/uploads/$uploadId/parts/$partNo/complete',
          data: {'etag': etag, 'size_bytes': chunk.length}),
      (_) {},
    );
  }

  Future<void> completeUpload(String uploadId) =>
      _call(() => _dio.post('/v1/uploads/$uploadId/complete'), (_) {});

  Future<Map<String, dynamic>> createRecording({
    required String title,
    required int durationMs,
    required String sha256,
    required int sizeBytes,
    String source = 'device',
    String? deviceSn,
    String? deviceFileId,
  }) =>
      _call(
        () => _dio.post('/v1/recordings', data: {
          'title': title,
          'duration_ms': durationMs,
          'sha256': sha256,
          'size_bytes': sizeBytes,
          'source': source,
          if (deviceSn != null) 'device_sn': deviceSn,
          if (deviceFileId != null) 'device_file_id': deviceFileId,
        }),
        (d) => (d as Map).cast<String, dynamic>(),
      );

  Future<Map<String, dynamic>> createJob(String recordingId) => _call(
        () => _dio.post('/v1/jobs', data: {'recording_id': recordingId}),
        (d) => (d as Map).cast<String, dynamic>(),
      );

  Future<Map<String, dynamic>> getJob(String jobId) => _call(
        () => _dio.get('/v1/jobs/$jobId'),
        (d) => (d as Map).cast<String, dynamic>(),
      );

  Future<List<Map<String, dynamic>>> getTranscript(String recordingId) => _call(
        () => _dio.get('/v1/recordings/$recordingId/transcript'),
        (d) => ((d as Map)['segments'] as List)
            .map((e) => (e as Map).cast<String, dynamic>())
            .toList(),
      );

  Future<Map<String, dynamic>> getSummary(String recordingId) => _call(
        () => _dio.get('/v1/recordings/$recordingId/summary'),
        (d) => (d as Map).cast<String, dynamic>(),
      );
}
