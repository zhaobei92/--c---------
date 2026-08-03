import 'package:flutter/material.dart';
import 'package:flutter_gen/gen_l10n/app_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app.dart';

/// 设置骨架(PRD 页面 51—55):语言/通知/音频保留策略/数据导出/注销。
/// 注销与数据导出为商店合规必备(App 内入口)。
class SettingsPage extends ConsumerWidget {
  const SettingsPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final t = AppLocalizations.of(context)!;
    return Scaffold(
      appBar: AppBar(title: Text(t.settingsTab)),
      body: ListView(children: [
        ListTile(
          title: const Text('语言 / Language / اللغة'),
          trailing: DropdownButton<String>(
            value: ref.watch(localeProvider).languageCode,
            items: const [
              DropdownMenuItem(value: 'zh', child: Text('中文')),
              DropdownMenuItem(value: 'en', child: Text('English')),
              DropdownMenuItem(value: 'ar', child: Text('العربية')),
            ],
            onChanged: (code) {
              if (code != null) {
                ref.read(localeProvider.notifier).state = Locale(code);
              }
            },
          ),
        ),
        ListTile(title: Text(t.exportMyData), onTap: () {}),
        ListTile(
          title: Text(t.deleteAccount,
              style: TextStyle(color: Theme.of(context).colorScheme.error)),
          onTap: () {},
        ),
      ]),
    );
  }
}
