import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../features/auth/login_page.dart';
import '../features/device/device_page.dart';
import '../features/files/files_page.dart';
import '../features/home/home_page.dart';
import '../features/membership/membership_page.dart';
import '../features/settings/settings_page.dart';
import '../features/transcript/transcript_page.dart';

/// 页面清单对应 docs/01-prd.md §10(55 页);此处为骨架路由,逐页补齐。
final routerProvider = Provider<GoRouter>((ref) {
  return GoRouter(
    initialLocation: '/login',
    routes: [
      GoRoute(path: '/login', builder: (_, __) => const LoginPage()),
      GoRoute(path: '/home', builder: (_, __) => const HomePage()),
      GoRoute(path: '/files', builder: (_, __) => const FilesPage()),
      GoRoute(path: '/device', builder: (_, __) => const DevicePage()),
      GoRoute(
        path: '/recording/:id',
        builder: (_, s) => TranscriptPage(
          recordingId: s.pathParameters['id']!,
          jobId: s.uri.queryParameters['job'],
        ),
      ),
      GoRoute(path: '/membership', builder: (_, __) => const MembershipPage()),
      GoRoute(path: '/settings', builder: (_, __) => const SettingsPage()),
    ],
  );
});
