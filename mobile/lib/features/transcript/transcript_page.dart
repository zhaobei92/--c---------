import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_gen/gen_l10n/app_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/providers.dart';

/// 音频详情(Phase 3 Demo 闭环):任务状态轮询 → 转写原文 / AI 摘要与待办。
/// 摘要每条结论携带时间戳证据,点击跳转并高亮对应原文分段(PRD §6.3)。
class TranscriptPage extends ConsumerStatefulWidget {
  const TranscriptPage({super.key, required this.recordingId, this.jobId});

  final String recordingId;
  final String? jobId;

  @override
  ConsumerState<TranscriptPage> createState() => _TranscriptPageState();
}

class _TranscriptPageState extends ConsumerState<TranscriptPage> {
  String _jobStatus = 'loading';
  List<Map<String, dynamic>> _segments = const [];
  Map<String, dynamic>? _summary;
  String? _highlightSegmentId;
  Timer? _poll;
  final Map<String, GlobalKey> _segmentKeys = {};

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _poll?.cancel();
    super.dispose();
  }

  Future<void> _load() async {
    final api = ref.read(apiClientProvider);
    if (widget.jobId != null) {
      final job = await api.getJob(widget.jobId!);
      setState(() => _jobStatus = job['status'] as String);
      if (_jobStatus != 'completed' && _jobStatus != 'failed') {
        _poll = Timer(const Duration(seconds: 2), _load); // 轮询至终态
        return;
      }
    } else {
      _jobStatus = 'completed';
    }
    if (_jobStatus == 'completed') {
      final segments = await api.getTranscript(widget.recordingId);
      final summary = await api.getSummary(widget.recordingId);
      for (final seg in segments) {
        _segmentKeys[seg['segment_id'] as String] = GlobalKey();
      }
      setState(() {
        _segments = segments;
        _summary = summary;
      });
    }
  }

  void _jumpToEvidence(Map<String, dynamic> evidence) {
    final id = evidence['segment_id'] as String;
    setState(() => _highlightSegmentId = id);
    final key = _segmentKeys[id];
    if (key?.currentContext != null) {
      Scrollable.ensureVisible(key!.currentContext!,
          duration: const Duration(milliseconds: 300), alignment: 0.2);
    }
    DefaultTabController.of(context).animateTo(0); // 切回原文页签
  }

  static String _fmt(int ms) {
    final s = ms ~/ 1000;
    return '${(s ~/ 60).toString().padLeft(2, '0')}:'
        '${(s % 60).toString().padLeft(2, '0')}';
  }

  @override
  Widget build(BuildContext context) {
    final t = AppLocalizations.of(context)!;
    return DefaultTabController(
      length: 2,
      child: Scaffold(
        appBar: AppBar(
          title: Text(t.transcriptTitle),
          bottom: TabBar(tabs: [
            Tab(text: t.transcriptTitle),
            Tab(text: t.summaryTitle),
          ]),
        ),
        body: switch (_jobStatus) {
          'failed' => Center(child: Text(t.errorGeneric('JOB_4201'))),
          'completed' when _segments.isNotEmpty => TabBarView(children: [
              _transcriptView(),
              _summaryView(t),
            ]),
          _ => const Center(
              child: Column(mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    CircularProgressIndicator(),
                    SizedBox(height: 12),
                    Text('AI processing…'),
                  ])),
        },
      ),
    );
  }

  Widget _transcriptView() {
    return ListView.builder(
      padding: const EdgeInsets.all(16),
      itemCount: _segments.length,
      itemBuilder: (context, i) {
        final seg = _segments[i];
        final id = seg['segment_id'] as String;
        final highlighted = id == _highlightSegmentId;
        return Container(
          key: _segmentKeys[id],
          margin: const EdgeInsets.only(bottom: 12),
          padding: const EdgeInsets.all(12),
          decoration: BoxDecoration(
            color: highlighted
                ? Theme.of(context).colorScheme.primaryContainer
                : Theme.of(context).colorScheme.surfaceContainerHighest,
            borderRadius: BorderRadius.circular(12),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                '${seg['speaker'] ?? ''} · ${_fmt(seg['start_ms'] as int)}'
                '–${_fmt(seg['end_ms'] as int)}',
                style: Theme.of(context).textTheme.labelSmall,
              ),
              const SizedBox(height: 4),
              Text(seg['text'] as String),
            ],
          ),
        );
      },
    );
  }

  Widget _summaryView(AppLocalizations t) {
    final items =
        (_summary?['items'] as List?)?.cast<Map<String, dynamic>>() ?? const [];
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        for (final item in items)
          Card(
            child: ListTile(
              leading: Icon(item['type'] == 'todo'
                  ? Icons.check_box_outlined
                  : Icons.lightbulb_outline),
              title: Text(item['text'] as String),
              subtitle: Wrap(
                spacing: 8,
                children: [
                  for (final ev in (item['evidence'] as List)
                      .cast<Map<String, dynamic>>())
                    ActionChip(
                      avatar: const Icon(Icons.play_arrow, size: 16),
                      label: Text('${_fmt(ev['start_ms'] as int)}'
                          '–${_fmt(ev['end_ms'] as int)}'),
                      onPressed: () => _jumpToEvidence(ev),
                    ),
                ],
              ),
            ),
          ),
      ],
    );
  }
}
