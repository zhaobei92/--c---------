# Prompts

每个模型任务一个独立提示词文件，包含：角色、唯一任务、允许使用的数据、
禁止行为、输出 Schema、失败条件、示例。文件头部必须带版本号。

阶段2起填充：
- intake/intake_extractor.md
- diagnosis/risk_triage.md
- diagnosis/stuck_classifier.md
- question-selection/question_selector.md
- challenger/challenger.md
- coaching/decision_coach.md
