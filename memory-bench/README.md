# memory-bench

开源 agent 记忆框架实测对比。**这是选型调研项目，不是产品开发**——约束见
[`CONSTITUTION.md`](CONSTITUTION.md)，任务范围见 [`TASK.md`](TASK.md)，
测试方法见 [`docs/plan.md`](docs/plan.md)，当前进度见 [`PROGRESS.md`](PROGRESS.md)。

待测：Mem0、Letta、Graphiti、Memvid，加分项 Hindsight。

## 当前状态

- **阶段1**（测试方案 + 问题集）✅ 完成，问题集已冻结为 **v1.0**（60 题）
- **阶段2-A**（静态调研：源码、依赖、端侧可行性）✅ 完成 →
  [`docs/frameworks-survey.md`](docs/frameworks-survey.md)
- **阶段2-B**（逐框架实测）⛔ 需在有 GPU 的机器上执行，原因见 `PROGRESS.md`
  "环境阻断"一节。`frameworks/{graphiti,mem0,hindsight}/` 下的 `setup.sh` 与
  `adapter.py` 已写好待跑，**但未经验证**。
- **阶段3**（选型报告）⬜ 未开始

静态调研已得出的四条关键结论（详见调研文档）：Letta 主仓已被官方弃用；
Mem0 开源版没有事件时间且旧事实原地覆盖；Graphiti 是唯一原生双时间轴；
Hindsight 的时间能力与活跃度均被低估，建议升为正式待测项。

## 环境要求

### 跑数据生成、问题集、harness（现在就能跑）

- Python ≥ 3.10，**不需要任何第三方包**
- 不需要网络、不需要 GPU

`tiktoken`（更准的 token 计数）和 `pynvml`（显存采样）装了会自动启用，
没装会降级并在结果里注明，不影响运行。

### 跑各框架实测（阶段2，尚未开始）

- NVIDIA GPU，显存 ≥ 12GB（本地跑开源 LLM + embedding）
- Docker（Letta 的服务端、Graphiti 的图数据库都依赖容器）
- 能访问 huggingface.co（下载模型权重）与 github.com
- 每个框架一个独立虚拟环境，安装命令写在 `frameworks/<名字>/setup.sh`

## 目录结构

```
memory-bench/
├── CONSTITUTION.md          项目宪法（最高约束）
├── TASK.md                  任务书
├── PROGRESS.md              进度与断点恢复锚点
├── docs/plan.md             测试方案：公平性约束、指标定义、判分口径
├── scripts/
│   ├── gen_data.py          模拟数据生成器（确定性，自带一致性自检）
│   └── gen_question_set.py  问题集生成器（自带对着语料的逐题校验）
├── data/<场景>/
│   ├── day_01.jsonl … day_30.jsonl    对话转写
│   └── facts_timeline.json            事实时间线（标准答案的唯一来源）
├── question_set.json        60 题，5 类 × 各 12 题
├── harness/                 评测骨架，只依赖标准库
│   ├── schema.py            数据结构与语料装载
│   ├── metrics.py           延迟分位数 / token / 内存显存峰值
│   ├── scorer.py            判分规则
│   ├── runner.py            统一评测入口
│   └── adapters/
│       ├── base.py          框架必须实现的接口
│       └── baseline_bm25.py 零依赖 BM25 基线
├── frameworks/<名字>/       每框架一个目录：setup.sh + adapter.py（阶段2填充）
└── results/<框架>/          raw_*.jsonl + summary.json + review.csv
```

## 复现步骤

以下命令都在 `memory-bench/` 目录下执行。

### 1. 生成测试数据

```bash
python3 scripts/gen_data.py
```

固定随机种子，任意机器重跑产出一致。会先跑一致性自检（版本链是否连续、
事实是否都有对话证据支撑），不过就直接报错退出。

只想验证不想写文件：`python3 scripts/gen_data.py --check`

### 2. 生成问题集

```bash
python3 scripts/gen_question_set.py
```

会逐题对着语料校验（证据能否唯一命中、关键词能否在原文找到、拒答题的探针词是否
真的在语料中不存在），任何一条不过就拒绝产出文件。

### 3. 跑基线，验证整条流水线

```bash
python3 -m harness.runner --framework baseline_bm25
```

无需网络和 GPU，几秒钟跑完。输出在 `results/baseline_bm25/`。

冒烟测试（每场景只跑前 5 题）：加 `--limit 5`
单个场景：加 `--scenario pet`

### 4. 跑某个框架（阶段2）

```bash
bash frameworks/mem0/setup.sh          # 建独立 venv 并安装
source frameworks/mem0/.venv/bin/activate
python3 -m harness.runner --framework mem0
deactivate
```

harness 只依赖标准库，所以能在任意框架的 venv 解释器下直接运行。

### 5. 人工复核

打开 `results/<框架>/review.csv`（UTF-8 BOM，Excel 直接打开不乱码），
逐题填 `human_verdict` 列（对/错）和 `human_note`。
**报告中的准确率以人工列为准**，自动列只作初筛和差异参照。

## 输出格式

`results/<框架>/` 下三个文件：

- **`raw_<场景>.jsonl`** — 每题一行：问题、标准答案、框架回答、检索命中的
  utt_id、证据召回率、延迟、token、自动判定与理由
- **`summary.json`** — 准确率（总体/分题型/分场景/回溯题单列）、延迟 P50/P95、
  灌入与查询 token、内存显存峰值、框架自述（`describe()`）、环境溯源
  （Python 版本、平台、git commit、token 计数口径）
- **`review.csv`** — 人工核对表

## 已知限制

- **数据是模拟的**。真实转写数据到位后，至少要重测事实变更、跨会话综合、拒答
  三类关键项。报告中所有结论都必须带这个标注。
- **内存指标只覆盖 runner 进程**。把重活放在独立服务或容器里的框架（Letta、
  Graphiti）需另行用 `docker stats` 补齐，报告里要注明口径。
- **自动判分有误差**，尤其是措辞灵活的跨会话题，所以强制人工复核。
- **BM25 基线不能与带 LLM 的框架直接比总分**，原因见 `docs/plan.md` 第七节。
