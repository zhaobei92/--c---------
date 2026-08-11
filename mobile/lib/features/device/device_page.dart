import 'package:flutter/material.dart';
import 'package:flutter_gen/gen_l10n/app_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../platform/device_channel.dart';

final deviceChannelProvider = Provider<DeviceChannel>((ref) => DeviceChannel());

/// 设备页骨架:扫描/绑定/状态/同步入口(PRD 页面 21—31)。
/// 真机协议未到位时,原生层对接 mock_device;UI 与业务流程不变。
class DevicePage extends ConsumerWidget {
  const DevicePage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = AppLocalizations.of(context)!;
    return Scaffold(
      appBar: AppBar(title: Text(t.deviceTab)),
      body: Center(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Text(t.deviceScanning),
            const SizedBox(height: 16),
            FilledButton(
              onPressed: () => ref.read(deviceChannelProvider).startScan(),
              child: Text(t.deviceAdd),
            ),
          ],
        ),
      ),
    );
  }
}
