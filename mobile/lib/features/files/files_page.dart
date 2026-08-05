import 'package:flutter/material.dart';
import 'package:flutter_gen/gen_l10n/app_localizations.dart';

/// 文件列表骨架(PRD 页面 15—18:全部文件/文件夹/标签/搜索)。
class FilesPage extends StatelessWidget {
  const FilesPage({super.key});

  @override
  Widget build(BuildContext context) {
    final t = AppLocalizations.of(context)!;
    return Scaffold(
      appBar: AppBar(title: Text(t.filesTab)),
      body: const Center(child: Text('Recording list — Repository 接本地 SQLite')),
    );
  }
}
