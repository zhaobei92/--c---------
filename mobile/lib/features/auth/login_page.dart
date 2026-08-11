import 'package:flutter/material.dart';
import 'package:flutter_gen/gen_l10n/app_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../core/providers.dart';
import '../../data/api/api_client.dart';

/// 登录页(Phase 3 Demo 闭环):真实邮箱验证码流程。
/// dev/staging 服务端回显 dev_code,一键完成"发码→验证→持 token";
/// prod 不回显,用户手动输入邮件中的验证码(同一 API)。
class LoginPage extends ConsumerStatefulWidget {
  const LoginPage({super.key});

  @override
  ConsumerState<LoginPage> createState() => _LoginPageState();
}

class _LoginPageState extends ConsumerState<LoginPage> {
  final _email = TextEditingController(text: 'demo@ysnote.dev');
  final _code = TextEditingController();
  String? _error;
  bool _busy = false;

  Future<void> _sendCode() async {
    setState(() => _error = null);
    try {
      final devCode = await ref.read(apiClientProvider).sendCode(_email.text);
      if (devCode != null) _code.text = devCode; // dev 环境自动填码
    } on ApiException catch (e) {
      setState(() => _error = '${e.message} (${e.code})');
    }
  }

  Future<void> _login() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final api = ref.read(apiClientProvider);
      if (_code.text.isEmpty) {
        final devCode = await api.sendCode(_email.text);
        if (devCode != null) _code.text = devCode;
      }
      final token = await api.verifyCode(_email.text, _code.text);
      ref.read(authTokenProvider.notifier).state = token;
      if (mounted) context.go('/device');
    } on ApiException catch (e) {
      setState(() => _error = '${e.message} (${e.code})');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

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
              Text(t.loginTitle,
                  style: Theme.of(context).textTheme.headlineMedium),
              const SizedBox(height: 24),
              TextField(
                controller: _email,
                decoration: InputDecoration(hintText: t.loginEmailHint),
              ),
              const SizedBox(height: 12),
              Row(children: [
                Expanded(
                  child: TextField(
                    controller: _code,
                    decoration: InputDecoration(hintText: t.loginCodeHint),
                  ),
                ),
                const SizedBox(width: 12),
                OutlinedButton(
                    onPressed: _sendCode, child: Text(t.loginSendCode)),
              ]),
              const SizedBox(height: 24),
              FilledButton(
                onPressed: _busy ? null : _login,
                child: _busy
                    ? const SizedBox(
                        width: 18, height: 18,
                        child: CircularProgressIndicator(strokeWidth: 2))
                    : Text(t.loginTitle),
              ),
              if (_error != null)
                Padding(
                  padding: const EdgeInsets.only(top: 12),
                  child: Text(_error!,
                      style: TextStyle(
                          color: Theme.of(context).colorScheme.error)),
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
