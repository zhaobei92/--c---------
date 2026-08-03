"""决策引擎的输入输出类型（纯数据，不依赖 ORM 与 LLM）。"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class OptionSpec:
    key: str
    name: str
    eligible: bool = True
    ineligible_reason: str | None = None


@dataclass(frozen=True)
class CriterionSpec:
    key: str
    name: str
    weight: float
    weight_uncertainty: float = 0.1
    curve_type: str = "linear"
    direction: str = "higher_better"  # higher_better | lower_better
    criterion_type: str = "compensatory"  # compensatory | veto | threshold
    veto_threshold: float | None = None
    minimum_acceptable: float | None = None


@dataclass(frozen=True)
class EvaluationSpec:
    option_key: str
    criterion_key: str
    expected_value: float  # 0-1（lower_better 也按原始值给，引擎负责翻转）
    uncertainty: float = 0.15


@dataclass
class EliminationRecord:
    option_key: str
    reason: str


@dataclass
class SensitivityFlip:
    variable: str  # 例如 "weight:价格" / "score:雷鸟U8:画质"
    change: str  # 例如 "权重提高50%"
    new_winner: str


@dataclass
class AnalysisResult:
    algorithm_version: str
    seed: int
    n_iterations: int
    eliminated: list[EliminationRecord]
    deterministic_utilities: dict[str, float]  # option_key -> U
    ranking: list[str]  # 按确定性效用降序的 option_key
    winner_probability: dict[str, float]
    ranking_stability: float
    expected_utility_gap: float
    minimax_regret_option: str | None
    max_regret: dict[str, float]
    critical_variables: list[str]
    sensitivity_flips: list[SensitivityFlip]
    aspiration_warnings: list[str] = field(default_factory=list)

    @property
    def winner(self) -> str | None:
        return self.ranking[0] if self.ranking else None

    def to_dict(self) -> dict:
        return {
            "algorithm_version": self.algorithm_version,
            "seed": self.seed,
            "n_iterations": self.n_iterations,
            "eliminated": [vars(e) for e in self.eliminated],
            "deterministic_utilities": self.deterministic_utilities,
            "ranking": self.ranking,
            "winner": self.winner,
            "winner_probability": self.winner_probability,
            "ranking_stability": self.ranking_stability,
            "expected_utility_gap": self.expected_utility_gap,
            "minimax_regret_option": self.minimax_regret_option,
            "max_regret": self.max_regret,
            "critical_variables": self.critical_variables,
            "sensitivity_flips": [vars(f) for f in self.sensitivity_flips],
            "aspiration_warnings": self.aspiration_warnings,
        }
