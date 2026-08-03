"""Prompt 版本管理回归（方案阶段8 / 第十三节）。"""

import re
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parents[3] / "prompts"
PROMPT_FILES = [
    p for p in PROMPTS_DIR.rglob("*.md") if p.name != "README.md"
]

REQUIRED_SECTIONS = ["# 角色", "# 唯一任务", "# 禁止", "# 输出 Schema"]


def test_prompts_exist():
    names = {p.name for p in PROMPT_FILES}
    assert {
        "intake_extractor.md",
        "stuck_classifier.md",
        "criteria_generator.md",
        "challenger.md",
        "decision_coach.md",
    } <= names


def test_every_prompt_has_version_header():
    for path in PROMPT_FILES:
        head = path.read_text(encoding="utf-8").splitlines()[0]
        assert re.search(r"version:\s*\d+\.\d+\.\d+", head), f"{path.name} 缺少版本号"


def test_every_prompt_has_required_sections():
    for path in PROMPT_FILES:
        text = path.read_text(encoding="utf-8")
        for section in REQUIRED_SECTIONS:
            assert section in text, f"{path.name} 缺少小节 {section}"


def test_prompt_loader_reads_all():
    from app.services.intake_service import load_prompt

    for path in PROMPT_FILES:
        rel = path.relative_to(PROMPTS_DIR).as_posix()
        assert load_prompt(rel)
