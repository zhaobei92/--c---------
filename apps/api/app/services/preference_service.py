"""偏好学习（阶段3）：标准生成 + 成对比较 + Bradley-Terry 权重。

阶段2桩实现：直接放行到信息缺口分析；阶段3替换为真实逻辑。
"""

from sqlalchemy.orm import Session

from app.models import DecisionCase
from model_gateway import ModelGateway
from shared_schemas import AdvanceResponse


def preference_step(
    db: Session, case: DecisionCase, gateway: ModelGateway
) -> AdvanceResponse | None:
    """需要用户做成对比较时返回 AdvanceResponse；偏好已就绪时返回 None。"""
    return None
