"""Deterministic decision engine.

阶段1只有包骨架；阶段4起在各子模块中实现：
constraints(硬约束过滤) / preference(Bradley-Terry) / utility(MAUT+效用曲线) /
uncertainty(分布建模) / monte_carlo(稳定性模拟) / regret(minimax regret) /
question_selection(问题价值) / reopen(重开门槛) / personalization(偏好后验) /
explainability(可解释输出)。

约束：本包禁止依赖任何 LLM SDK，所有算法必须可离线单元测试、可固定随机种子复现。
"""

__version__ = "0.1.0"
