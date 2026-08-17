import 'package:flutter/material.dart';
import 'package:flutter_gen/gen_l10n/app_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../core/providers.dart';
import '../../data/sync/demo_sync_service.dart';
import '../../platform/device_transport.dart';

/// Demo 设备页(Phase 3):连接 Mock 设备 → 文件列表 → 单文件黄金链同步
/// (下载/上传进度 + 阶段展示)→ 完成后跳转转写页。
/// 真机阶段仅替换 transportProvider,本页零改动。
class DevicePage extends ConsumerStatefulWidget {
  const DevicePage({super.key});

  @override
  ConsumerState<DevicePage> createState() => _DevicePageState();
}

class _DevicePageState extends ConsumerState<DevicePage> {
  DeviceInfoSummary? _device;
  List<DeviceFileInfo> _files = const [];
  String? _error;
  final Map<String, SyncProgress> _progress = {};

  Future<void> _connect() async {
    setState(() => _error = null);
    try {
      final transport = ref.read(deviceTransportProvider);
      final info = await transport.connect();
      final files = await transport.listFiles();
      setState(() {
        _device = info;
        _files = files;
      });
    } on TransportException catch (e) {
      setState(() => _error = '${e.message} (${e.code})');
    } catch (e) {
      setState(() => _error = '$e');
    }
  }

  Future<void> _sync(DeviceFileInfo file) async {
    try {
      final service = await ref.read(demoSyncServiceProvider.future);
      final result = await service.syncFile(file,
          onProgress: (p) => setState(() => _progress[file.id] = p));
      if (mounted) {
        context.go('/recording/${result.recordingId}?job=${result.jobId}');
      }
    } on Exception catch (e) {
      setState(() {
        _progress.remove(file.id);
        _error = '$e';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = AppLocalizations.of(context)!;
    return Scaffold(
      appBar: AppBar(title: Text(t.deviceTab)),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          if (_device == null) ...[
            Text(t.deviceScanning),
            const SizedBox(height: 12),
            FilledButton(onPressed: _connect, child: Text(t.deviceAdd)),
          ] else ...[
            Card(
              child: ListTile(
                leading: const Icon(Icons.mic_external_on),
                title: Text('${_device!.model} · ${_device!.sn}'),
                subtitle: Text(
                    '${t.deviceBattery} ${_device!.battery}% · FW ${_device!.firmware}'),
              ),
            ),
            const SizedBox(height: 8),
            for (final file in _files) _fileTile(t, file),
          ],
          if (_error != null)
            Padding(
              padding: const EdgeInsets.only(top: 12),
              child: Text(_error!,
                  style: TextStyle(color: Theme.of(context).colorScheme.error)),
            ),
        ],
      ),
    );
  }

  Widget _fileTile(AppLocalizations t, DeviceFileInfo file) {
    final progress = _progress[file.id];
    return Card(
      child: ListTile(
        leading: const Icon(Icons.audio_file_outlined),
        title: Text(file.name),
        subtitle: progress == null
            ? Text('${(file.size / 1024).toStringAsFixed(0)} KB'
                '${file.synced ? ' · synced' : ''}')
            : _progressView(progress),
        trailing: progress == null || progress.stage == SyncStage.done
            ? FilledButton.tonal(
                onPressed: () => _sync(file), child: Text(t.deviceSync))
            : null,
      ),
    );
  }

  Widget _progressView(SyncProgress p) {
    final label = switch (p.stage) {
      SyncStage.connecting => 'Connecting…',
      SyncStage.downloading => 'Downloading…',
      SyncStage.registering => 'Registering…',
      SyncStage.uploading => 'Uploading…',
      SyncStage.creatingJob => 'Creating AI task…',
      SyncStage.done => 'Done',
    };
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(label),
        if (p.stage == SyncStage.downloading && p.total > 0)
          LinearProgressIndicator(value: p.received / p.total)
        else if (p.stage != SyncStage.done)
          const LinearProgressIndicator(),
      ],
    );
  }
}
