import 'package:flutter/material.dart';
import 'package:flutter_gen/gen_l10n/app_localizations.dart';

/// 音频详情骨架:播放器/原文/说话人/摘要/待办/翻译(PRD 页面 32—44)。
/// 摘要每条结论必须展示时间戳来源并可跳转播放位置。
class TranscriptPage extends StatelessWidget {
  const TranscriptPage({super.key, required this.recordingId});

  final String recordingId;

  @override
  Widget build(BuildContext context) {
    final t = AppLocalizations.of(context)!;
    return DefaultTabController(
      length: 4,
      child: Scaffold(
        appBar: AppBar(
          title: Text(t.transcriptTitle),
          bottom: TabBar(tabs: [
            Tab(text: t.transcriptTitle),
            Tab(text: t.summaryTitle),
            Tab(text: t.todosTitle),
            Tab(text: t.translationTitle),
          ]),
        ),
        body: TabBarView(children: [
          Center(child: Text('Segments · $recordingId')),
          const Center(child: Text('Summary with evidence timestamps')),
          const Center(child: Text('To-dos (100% with timestamps)')),
          const Center(child: Text('Translation layer (original untouched)')),
        ]),
      ),
    );
  }
}
