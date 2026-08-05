import 'package:flutter/material.dart';
import 'package:flutter_gen/gen_l10n/app_localizations.dart';

/// 会员中心骨架(PRD 页面 45—50):套餐/分钟包/订单/兑换码/恢复购买。
/// 购买经原生 StoreKit 2 / Play Billing;服务端验证后刷新权益。
class MembershipPage extends StatelessWidget {
  const MembershipPage({super.key});

  @override
  Widget build(BuildContext context) {
    final t = AppLocalizations.of(context)!;
    return Scaffold(
      appBar: AppBar(title: Text(t.membershipTitle)),
      body: Center(child: Text(t.minutesRemaining(120))),
    );
  }
}
