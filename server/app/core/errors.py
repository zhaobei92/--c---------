"""统一错误码(唯一事实来源与 docs/07-error-codes.md 同步修改)。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ErrorDef:
    code: str
    http_status: int
    retryable: bool
    message: str


_E = ErrorDef

REGISTRY: dict[str, ErrorDef] = {e.code: e for e in [
    # AUTH
    _E("AUTH_0001", 400, False, "verification code invalid or expired"),
    _E("AUTH_0002", 429, False, "verification code rate limited"),
    _E("AUTH_0003", 401, True, "access token expired"),
    _E("AUTH_0004", 401, False, "refresh token invalid"),
    _E("AUTH_0005", 401, False, "third-party credential invalid"),
    _E("AUTH_0006", 409, False, "account in deletion cooling period"),
    _E("AUTH_0007", 403, False, "account banned"),
    # DEV
    _E("DEV_1101", 409, False, "device already bound by another account"),
    _E("DEV_1102", 400, False, "device model not supported"),
    _E("DEV_1103", 403, False, "device blacklisted"),
    _E("DEV_1104", 400, False, "device key verification failed"),
    _E("DEV_1201", 504, True, "BLE connect timeout"),
    _E("DEV_1202", 502, True, "failed to join device wifi"),
    _E("DEV_1203", 502, True, "device file index read failed"),
    _E("DEV_1204", 422, False, "file hash check failed after retries"),
    _E("DEV_1205", 502, True, "transfer interrupted by device"),
    _E("DEV_1206", 409, False, "device occupied by another phone"),
    _E("DEV_1207", 409, False, "device storage full"),
    _E("DEV_1301", 422, False, "firmware signature invalid"),
    _E("DEV_1302", 409, False, "device battery below OTA threshold"),
    _E("DEV_1303", 502, True, "OTA transfer failed"),
    _E("DEV_1304", 422, False, "firmware model mismatch"),
    _E("DEV_1305", 409, False, "firmware downgrade forbidden"),
    _E("DEV_1401", 409, False, "protocol version incompatible"),
    # UPL
    _E("UPL_2001", 500, True, "upload init failed"),
    _E("UPL_2002", 413, False, "file exceeds size limit"),
    _E("UPL_2003", 429, False, "concurrent upload limit reached"),
    _E("UPL_2101", 502, True, "part upload failed"),
    _E("UPL_2102", 409, False, "parts missing, cannot merge"),
    _E("UPL_2103", 422, True, "file hash mismatch"),
    _E("UPL_2104", 401, True, "presigned url expired"),
    # ENT
    _E("ENT_3001", 402, False, "insufficient minutes"),
    _E("ENT_3002", 429, False, "concurrent job limit reached"),
    _E("ENT_3003", 402, False, "storage quota exceeded"),
    _E("ENT_3004", 429, False, "summary regeneration limit reached"),
    _E("ENT_3005", 400, False, "redeem code invalid"),
    _E("ENT_3006", 409, False, "redeem code already used"),
    _E("ENT_3007", 410, False, "redeem code expired"),
    _E("ENT_3008", 410, False, "entitlement expired"),
    # JOB
    _E("JOB_4001", 500, False, "illegal job state transition"),
    _E("JOB_4002", 422, False, "audio preprocessing failed"),
    _E("JOB_4003", 413, False, "audio exceeds plan duration limit"),
    _E("JOB_4101", 502, True, "ASR provider error"),
    _E("JOB_4102", 504, True, "ASR timeout"),
    _E("JOB_4103", 502, True, "diarization failed"),
    _E("JOB_4104", 502, True, "summary generation failed"),
    _E("JOB_4105", 502, True, "translation failed"),
    _E("JOB_4201", 500, False, "retries exhausted, job terminated"),
    # ORD
    _E("ORD_5001", 402, True, "apple receipt verification failed"),
    _E("ORD_5002", 402, True, "google purchase verification failed"),
    _E("ORD_5003", 200, False, "order already processed"),
    _E("ORD_5004", 500, False, "product id not configured"),
    _E("ORD_5005", 403, False, "transaction belongs to another account"),
    _E("ORD_5006", 409, False, "refund in progress"),
    # DOC
    _E("DOC_6001", 502, True, "export generation failed"),
    _E("DOC_6002", 410, False, "share link expired"),
    _E("DOC_6003", 404, False, "document deleted"),
    _E("DOC_6004", 503, True, "search temporarily unavailable"),
    # SYS
    _E("SYS_9001", 503, True, "service unavailable"),
    _E("SYS_9002", 429, True, "rate limited"),
    _E("SYS_9003", 426, False, "client version too old"),
    _E("SYS_9004", 422, False, "parameter validation failed"),
    _E("SYS_9005", 500, True, "unknown error"),
]}


class ApiError(Exception):
    """业务错误 → HTTP 响应 {error: {code, message, detail}}。"""

    def __init__(self, code: str, detail: dict | None = None, message: str | None = None):
        if code not in REGISTRY:
            raise ValueError(f"unregistered error code {code}")
        self.code = code
        self.definition = REGISTRY[code]
        self.detail = detail or {}
        self.message = message or self.definition.message
        super().__init__(f"{code}: {self.message}")

    def to_body(self) -> dict:
        return {"error": {"code": self.code, "message": self.message, "detail": self.detail}}
