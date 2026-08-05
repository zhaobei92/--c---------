import 'package:flutter/material.dart';
import 'package:flutter_gen/gen_l10n/app_localizations.dart';
import 'package:go_router/go_router.dart';

/// 首页:设备状态卡 + 最近文件 + 同步/上传队列入口(PRD 页面 12—20)。
class HomePage extends StatelessWidget {
  const HomePage({super.key});

  @override
  Widget build(BuildContext context) {
    final t = AppLocalizations.of(context)!;
    return Scaffold(
      appBar: AppBar(title: Text(t.appTitle)),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Card(
            child: ListTile(
              leading: const Icon(Icons.mic_external_on),
              title: Text(t.deviceTab),
              subtitle: Text('${t.deviceBattery} · ${t.deviceStorage}'),
              trailing: FilledButton.tonal(
                onPressed: () => context.go('/device'),
                child: Text(t.deviceSync),
              ),
            ),
          ),
          const SizedBox(height: 12),
          ListTile(
            title: Text(t.syncQueueTitle),
            trailing: const Icon(Icons.chevron_right),
            onTap: () {},
          ),
          ListTile(
            title: Text(t.uploadQueueTitle),
            trailing: const Icon(Icons.chevron_right),
            onTap: () {},
          ),
        ],
      ),
      bottomNavigationBar: NavigationBar(
        selectedIndex: 0,
        onDestinationSelected: (i) => context.go(const ['/home', '/files', '/device', '/settings'][i]),
        destinations: [
          NavigationDestination(icon: const Icon(Icons.home_outlined), label: t.homeTab),
          NavigationDestination(icon: const Icon(Icons.folder_outlined), label: t.filesTab),
          NavigationDestination(icon: const Icon(Icons.headset_outlined), label: t.deviceTab),
          NavigationDestination(icon: const Icon(Icons.settings_outlined), label: t.settingsTab),
        ],
      ),
    );
  }
}
