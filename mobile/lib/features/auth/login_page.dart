import 'package:flutter/material.dart';
import 'package:flutter_gen/gen_l10n/app_localizations.dart';
import 'package:go_router/go_router.dart';

class LoginPage extends StatelessWidget {
  const LoginPage({super.key});

  @override
  Widget build(BuildContext context) {
    final t = AppLocalizations.of(context)!;
    return Scaffold(
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text(t.loginTitle, style: Theme.of(context).textTheme.headlineMedium),
              const SizedBox(height: 24),
              TextField(decoration: InputDecoration(hintText: t.loginEmailHint)),
              const SizedBox(height: 12),
              Row(children: [
                Expanded(
                  child: TextField(
                      decoration: InputDecoration(hintText: t.loginCodeHint)),
                ),
                const SizedBox(width: 12),
                OutlinedButton(onPressed: () {}, child: Text(t.loginSendCode)),
              ]),
              const SizedBox(height: 24),
              FilledButton(
                onPressed: () => context.go('/home'),
                child: Text(t.loginTitle),
              ),
              const SizedBox(height: 12),
              OutlinedButton(onPressed: () {}, child: Text(t.loginWithApple)),
              const SizedBox(height: 8),
              OutlinedButton(onPressed: () {}, child: Text(t.loginWithGoogle)),
            ],
          ),
        ),
      ),
    );
  }
}
