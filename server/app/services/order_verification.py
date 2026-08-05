"""订单验证框架(Apple StoreKit 2 / Google Play Billing)。

设计:
  * PaymentProvider 协议抽象平台验证;生产实现分别接 App Store Server API
    与 Google Play Developer API(接入点在 AppleProvider/GoogleProvider 的 TODO 处)。
  * verify() 幂等:同 transaction_id 只发放一次权益(ORD_5003 返回原订单)。
  * 权益发放走 EntitlementService.grant(),即 usage_ledger 流水,禁止直改余额。
  * 退款回调(webhook)→ refund():订阅降级 + 分钟包按 ledger 冲正。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Protocol

from .entitlement_service import EntitlementService


class OrderError(Exception):
    error_code = "ORD_5001"


class VerificationFailed(OrderError):
    def __init__(self, platform: str, detail: str):
        self.error_code = "ORD_5001" if platform == "apple" else "ORD_5002"
        super().__init__(f"{platform} verification failed: {detail}")


class UnknownProduct(OrderError):
    error_code = "ORD_5004"  # 配置缺失,属程序缺陷,触发即告警


@dataclass
class VerifiedTransaction:
    transaction_id: str
    product_id: str
    purchased_at: datetime
    expires_at: datetime | None = None  # 订阅有值;消耗型为 None
    is_refunded: bool = False


class PaymentProvider(Protocol):
    platform: str

    def verify(self, credential: str) -> VerifiedTransaction:
        """验证平台凭证;失败抛 VerificationFailed。"""
        ...


@dataclass
class ProductConfig:
    """IAP 产品目录:product_id → 发放内容。"""

    product_id: str
    kind: str                      # subscription_month / subscription_year / minute_pack
    minutes: int                   # 发放分钟(订阅为每周期额度)
    bucket: str                    # member / purchased
    valid_days: int | None = None  # 分钟桶有效期;None = 不过期


@dataclass
class Order:
    id: str
    user_id: str
    platform: str
    product_id: str
    transaction_id: str
    status: str  # verified / refunded
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class OrderService:
    def __init__(
        self,
        entitlements: EntitlementService,
        catalog: dict[str, ProductConfig],
        providers: dict[str, PaymentProvider],
        now=lambda: datetime.now(timezone.utc),
    ):
        self.entitlements = entitlements
        self.catalog = catalog
        self.providers = providers
        self.now = now
        self.orders_by_txn: dict[str, Order] = {}

    def verify_and_fulfill(self, *, user_id: str, platform: str, credential: str) -> tuple[Order, bool]:
        """验证凭证并发放权益。返回 (order, already_processed)。

        幂等锚点 = 平台 transaction_id:重复验证同一交易返回原订单,不重复发放。
        """
        provider = self.providers.get(platform)
        if provider is None:
            raise VerificationFailed(platform, "provider not configured")
        txn = provider.verify(credential)

        existing = self.orders_by_txn.get(txn.transaction_id)
        if existing is not None:
            if existing.user_id != user_id:
                raise OrderError("ORD_5005: transaction belongs to another account")
            return existing, True

        product = self.catalog.get(txn.product_id)
        if product is None:
            raise UnknownProduct(txn.product_id)

        order = Order(
            id=str(uuid.uuid4()), user_id=user_id, platform=platform,
            product_id=txn.product_id, transaction_id=txn.transaction_id,
            status="verified",
        )
        expires = None
        if product.kind.startswith("subscription"):
            expires = txn.expires_at
        elif product.valid_days is not None:
            expires = self.now() + timedelta(days=product.valid_days)
        self.entitlements.grant(
            user_id, product.bucket, product.minutes,
            source_type="order", order_id=order.id, expires_at=expires,
            idempotency_key=f"order:{txn.transaction_id}",
        )
        self.orders_by_txn[txn.transaction_id] = order
        return order, False

    def refund(self, transaction_id: str) -> Order:
        """平台退款回调:订单标记 refunded;分钟包按该订单授予流水冲正。

        生产实现:找到 order 对应 grant 流水的 entitlement,expire 其剩余余额;
        订阅另行降级 subscriptions.status。此处落框架逻辑。
        """
        order = self.orders_by_txn.get(transaction_id)
        if order is None:
            raise OrderError(f"unknown transaction {transaction_id}")
        if order.status == "refunded":
            return order
        order.status = "refunded"
        for ent in self.entitlements.store.entitlements_for(order.user_id):
            grants = [
                e for e in self.entitlements.store.entries_for(order.user_id)
                if e.entitlement_id == ent.id and e.reason == "grant" and e.order_id == order.id
            ]
            if grants:
                self.entitlements.expire_entitlement(order.user_id, ent.id)
        return order


class AppleProvider:
    """生产实现:App Store Server API(JWS 校验 signed_transaction)。

    TODO(阶段5): 接入 app-store-server-library,校验签名链、bundle_id、环境。
    """

    platform = "apple"

    def verify(self, credential: str) -> VerifiedTransaction:  # pragma: no cover
        raise VerificationFailed("apple", "AppleProvider not wired yet (阶段5)")


class GoogleProvider:
    """生产实现:Google Play Developer API purchases.products / subscriptionsv2。"""

    platform = "google"

    def verify(self, credential: str) -> VerifiedTransaction:  # pragma: no cover
        raise VerificationFailed("google", "GoogleProvider not wired yet (阶段5)")


DEFAULT_CATALOG: dict[str, ProductConfig] = {
    "ys_member_monthly": ProductConfig("ys_member_monthly", "subscription_month", minutes=1200, bucket="member"),
    "ys_member_yearly": ProductConfig("ys_member_yearly", "subscription_year", minutes=1200, bucket="member"),
    "ys_minutes_300": ProductConfig("ys_minutes_300", "minute_pack", minutes=300, bucket="purchased", valid_days=365),
    "ys_minutes_1000": ProductConfig("ys_minutes_1000", "minute_pack", minutes=1000, bucket="purchased", valid_days=365),
}
