# 离线评测集

对应方案第十五节。每个案例是一个 JSON 对象：

```json
{
  "id": "ce-001",
  "category": "consumer_electronics",
  "input": "用户的自然语言描述",
  "expected": {
    "risk_level": "LOW",
    "guided_only": false,
    "min_options": 2,
    "expected_options": ["选项A", "选项B"],
    "must_have_hard_constraint": true,
    "forbidden_phrases": ["你应该", "AI已经替你决定"]
  }
}
```

运行方式：

```bash
python -m pytest apps/api/tests/test_evals_offline.py -q
```

当前为种子集（heuristic 模式即可验证的确定性预期）。目标规模是方案要求的
200 例（消费电子50 / 家居服务30 / 工作优先级30 / 课程学习25 / 旅行20 /
职业20 / 人际15 / 高风险边界10），扩充是内容标注工作：按上面格式往
`cases/*.json` 追加即可，runner 自动加载。接入真实 LLM 后，同一批案例
用于测提取召回率（选项≥95%、硬约束≥90%）。
