import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:ysnote/app.dart';

void main() {
  testWidgets('app boots to login page with zh locale', (tester) async {
    await tester.pumpWidget(const ProviderScope(child: YsNoteApp()));
    await tester.pumpAndSettle();
    expect(find.text('登录'), findsWidgets);
    expect(find.text('通过 Apple 登录'), findsOneWidget);
  });
}
