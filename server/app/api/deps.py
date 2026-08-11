"""依赖注入:服务单例与鉴权。

阶段1 骨架:仓储用内存实现,DB 接入(SQLAlchemy session)在阶段3 替换,
业务层接口不变。
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field

from fastapi import Depends, Header

from ..core.config import settings
from ..core.errors import ApiError
from ..core.security import verify_token
from ..services.code_store import MemoryCodeStore
from ..services.entitlement_service import EntitlementService, InMemoryLedgerStore
from ..services.order_verification import (
    DEFAULT_CATALOG, AppleProvider, GoogleProvider, OrderService,
)
from ..services.queue import InMemoryQueue
from ..services.upload_service import ObjectStore, UploadService


class FakeObjectStore(ObjectStore):
    """开发/测试对象存储:内存字典模拟 S3(生产换 boto3 实现)。"""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.parts: dict[tuple[str, int], bytes] = {}

    def presign_put(self, key: str, part_no: int) -> str:
        return f"https://storage.local/{key}/part/{part_no}?sig=dev"

    def put_part(self, key: str, part_no: int, data: bytes) -> str:
        self.parts[(key, part_no)] = data
        return hashlib.md5(data).hexdigest()

    def merge_parts(self, key: str, part_nos: list[int]) -> str:
        self.objects[key] = b"".join(self.parts[(key, n)] for n in part_nos)
        return key

    def compute_sha256(self, key: str) -> str:
        return hashlib.sha256(self.objects.get(key, b"")).hexdigest()


@dataclass
class AppState:
    """进程级服务容器。"""

    object_store: FakeObjectStore = field(default_factory=FakeObjectStore)
    queue: InMemoryQueue = field(default_factory=InMemoryQueue)
    ledger_store: InMemoryLedgerStore = field(default_factory=InMemoryLedgerStore)
    users: dict[str, dict] = field(default_factory=dict)          # user_id -> profile
    users_by_email: dict[str, str] = field(default_factory=dict)  # email -> user_id
    code_store: MemoryCodeStore = field(default_factory=MemoryCodeStore)
    devices: dict[str, dict] = field(default_factory=dict)        # sn -> device
    bindings: dict[str, str] = field(default_factory=dict)        # sn -> user_id(活跃绑定唯一)
    recordings: dict[str, dict] = field(default_factory=dict)
    jobs: dict[str, dict] = field(default_factory=dict)
    notifications: dict[str, list[dict]] = field(default_factory=dict)
    audit: list[dict] = field(default_factory=list)
    # Outbox(P0-5):业务写入与消息投递解耦;生产实现为 outbox_events 表
    # 与业务同事务写入,由 Publisher 轮询投递到队列后标记 published。
    outbox: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.entitlements = EntitlementService(self.ledger_store)
        self.uploads = UploadService(self.object_store)
        self.orders = OrderService(
            self.entitlements, DEFAULT_CATALOG,
            providers={"apple": AppleProvider(), "google": GoogleProvider()},
        )
        # DB 后端(Phase 2):configure_db 后黄金主链路由走 PostgreSQL + S3,
        # 不再依赖内存字典;未配置时保持内存实现(单元测试)。
        self.db = None          # GoldenChainDb | None
        self.uploads_pg = None  # PgUploadService | None

    @property
    def email_codes(self) -> dict:
        """兼容测试访问:MemoryCodeStore 的底层字典。"""
        return self.code_store.codes

    def configure_db(self, engine, s3_store) -> None:
        from ..db.engine import make_session_factory
        from ..db.golden import GoldenChainDb
        from ..db.uploads import PgUploadService
        sf = make_session_factory(engine)
        self.db = GoldenChainDb(sf)
        self.uploads_pg = PgUploadService(sf, s3_store)

    def reset(self) -> None:
        """测试隔离:清空所有内存态并重建服务(路由持有的引用不变)。"""
        self.code_store = MemoryCodeStore()
        self.db = None
        self.uploads_pg = None
        for attr in ("users", "users_by_email", "devices", "bindings",
                     "recordings", "jobs", "notifications"):
            getattr(self, attr).clear()
        self.audit.clear()
        self.outbox.clear()
        self.object_store = FakeObjectStore()
        self.queue = InMemoryQueue()
        self.ledger_store = InMemoryLedgerStore()
        self.__post_init__()

    def create_user(self, email: str) -> str:
        user_id = str(uuid.uuid4())
        self.users[user_id] = {
            "id": user_id, "email": email, "nickname": email.split("@")[0],
            "region": "CN", "language": "zh", "audio_retention_days": None,
        }
        self.users_by_email[email] = user_id
        # 注册即送免费月度分钟(经 ledger 流水,幂等)
        self.entitlements.grant(
            user_id, "free_monthly", settings.free_monthly_minutes,
            source_type="system", idempotency_key=f"signup:{user_id}",
        )
        return user_id


state = AppState()


def current_user_id(authorization: str = Header(default="")) -> str:
    if not authorization.startswith("Bearer "):
        raise ApiError("AUTH_0004")
    user_id = verify_token(authorization.removeprefix("Bearer "))
    if state.db is not None:
        if state.db.get_user(user_id) is None:
            raise ApiError("AUTH_0004")
        return user_id
    if user_id not in state.users:
        raise ApiError("AUTH_0004")
    return user_id


def admin_guard(x_admin_token: str = Header(default="")) -> None:
    # 骨架:生产替换为独立 RBAC 账号体系 + audit_logs。
    # token 来自配置(prod 启动时 validate_production_settings 拒绝默认值)。
    import secrets as _secrets
    if not _secrets.compare_digest(x_admin_token, settings.admin_token):
        raise ApiError("AUTH_0007", message="admin access denied")


CurrentUser = Depends(current_user_id)
AdminGuard = Depends(admin_guard)
