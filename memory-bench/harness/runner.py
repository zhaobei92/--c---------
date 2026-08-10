# -*- coding: utf-8 -*-
"""统一评测入口。

所有框架跑的都是这一个 runner——数据表示、提问顺序、计时口径、判分规则全部固定，
框架之间唯一的差别就是 ``frameworks/<名字>/adapter.py`` 里那点实现。

用法::

    # 先激活该框架自己的 venv，再运行
    python3 -m harness.runner --framework baseline_bm25
    python3 -m harness.runner --framework mem0 --scenario pet
    python3 -m harness.runner --framework mem0 --limit 5     # 冒烟测试

产物（写入 results/<框架>/）::

    raw_<场景>.jsonl   每题一行：问题、标准答案、框架回答、检索命中、延迟、token
    summary.json       准确率、延迟分位数、token、资源峰值、框架自述
    review.csv         人工核对表：问题 / 标准答案 / 框架回答 / 自动判定 / 理由 / 人工判定
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import importlib
import importlib.util
import json
import platform
import subprocess
import sys
import traceback
from collections import Counter, defaultdict
from pathlib import Path

from . import scorer
from .adapters.base import DEFAULT_TOP_K, MemoryAdapter
from .metrics import TOKENIZER, ResourceMonitor, Timer, latency_summary
from .schema import (
    RESULTS_DIR,
    ROOT,
    SCENARIOS,
    load_question_set,
    load_utterances,
)

FRAMEWORKS_DIR = ROOT / "frameworks"


# --------------------------------------------------------------------------
# 适配器装载
# --------------------------------------------------------------------------


def load_adapter(name: str) -> MemoryAdapter:
    """先找内置适配器，再找 frameworks/<name>/adapter.py。"""
    try:
        mod = importlib.import_module(f"harness.adapters.{name}")
    except ModuleNotFoundError:
        path = FRAMEWORKS_DIR / name / "adapter.py"
        if not path.exists():
            sys.exit(
                f"找不到适配器 '{name}'：既不在 harness/adapters/ 下，也没有 {path}"
            )
        spec = importlib.util.spec_from_file_location(f"fw_{name}", path)
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)
    if not hasattr(mod, "Adapter"):
        sys.exit(f"适配器模块 {name} 里没有名为 Adapter 的类")
    return mod.Adapter()


def environment_provenance() -> dict:
    """记录环境信息，保证结果可追溯、可复现。"""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
    except Exception:  # noqa: BLE001
        commit = "unknown"
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_count": __import__("os").cpu_count(),
        "tokenizer": TOKENIZER,
        "git_commit": commit,
        "run_at": dt.datetime.now().isoformat(timespec="seconds"),
    }


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------


def run_scenario(adapter: MemoryAdapter, scenario: str, questions: list[dict], limit: int | None):
    utterances = load_utterances(scenario)
    qs = [q for q in questions if q["scenario"] == scenario]
    if limit:
        qs = qs[:limit]

    adapter.setup(scenario)

    with Timer() as t_ing:
        stats = adapter.ingest(utterances)
    stats.wall_seconds = round(t_ing.ms / 1000.0, 3)
    stats.n_items = stats.n_items or len(utterances)

    rows: list[dict] = []
    latencies: list[float] = []
    for q in qs:
        as_of = dt.datetime.fromisoformat(q["as_of"])
        error = ""
        try:
            with Timer() as t:
                res = adapter.query(q["question"], as_of)
            latency = t.ms
        except Exception:  # noqa: BLE001 —— 单题崩溃不应终止整场测试
            from .schema import QueryResult

            res = QueryResult(answer="")
            latency = 0.0
            error = traceback.format_exc(limit=3)

        latencies.append(latency)
        verdict = scorer.score(q, res.answer)
        gold_hit = (
            round(len(set(res.retrieved) & set(q["gold_evidence"])) / len(q["gold_evidence"]), 3)
            if q["gold_evidence"]
            else None
        )
        rows.append(
            {
                "qid": q["qid"],
                "scenario": scenario,
                "type": q["type"],
                "question": q["question"],
                "as_of": q["as_of"],
                "gold_answer": q["gold_answer"],
                "gold_evidence": q["gold_evidence"],
                "answer": res.answer,
                "retrieved": res.retrieved,
                "gold_evidence_recall": gold_hit,
                "latency_ms": round(latency, 2),
                "tokens_in": res.tokens_in,
                "tokens_out": res.tokens_out,
                "auto_verdict": verdict.as_str(),
                "auto_reason": verdict.reason,
                "requires_as_of": q.get("requires_as_of", False),
                "error": error,
            }
        )

    adapter.teardown()
    return rows, latencies, stats


def aggregate(rows: list[dict]) -> dict:
    by_type: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_type[r["type"]].append(r)

    def acc(items: list[dict]) -> dict:
        n = len(items)
        c = sum(1 for r in items if r["auto_verdict"] == "对")
        return {"correct": c, "total": n, "accuracy": round(c / n, 4) if n else 0.0}

    out = {"overall": acc(rows), "by_type": {t: acc(v) for t, v in sorted(by_type.items())}}
    by_scenario: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_scenario[r["scenario"]].append(r)
    out["by_scenario"] = {s: acc(v) for s, v in sorted(by_scenario.items())}

    as_of_rows = [r for r in rows if r["requires_as_of"]]
    if as_of_rows:
        out["as_of_questions"] = acc(as_of_rows)

    recalls = [r["gold_evidence_recall"] for r in rows if r["gold_evidence_recall"] is not None]
    out["mean_gold_evidence_recall"] = round(sum(recalls) / len(recalls), 4) if recalls else None
    errs = sum(1 for r in rows if r["error"])
    if errs:
        out["errored_questions"] = errs
    return out


def write_review_csv(path: Path, rows: list[dict]) -> None:
    cols = [
        "qid", "scenario", "type", "question", "as_of", "gold_answer",
        "answer", "auto_verdict", "auto_reason",
        "human_verdict", "human_note",
        "gold_evidence", "retrieved", "gold_evidence_recall",
        "latency_ms", "tokens_in", "tokens_out", "error",
    ]
    # utf-8-sig 让 Excel 打开中文不乱码
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            row = dict(r)
            row["gold_evidence"] = " ".join(r["gold_evidence"])
            row["retrieved"] = " ".join(r["retrieved"])
            row["human_verdict"] = ""  # 留空供人工填写，最终报告以这一列为准
            row["human_note"] = ""
            w.writerow(row)


def main() -> int:
    ap = argparse.ArgumentParser(description="memory-bench 统一评测入口")
    ap.add_argument("--framework", required=True, help="适配器名，如 baseline_bm25 / mem0")
    ap.add_argument("--scenario", default="all", choices=["all", *SCENARIOS])
    ap.add_argument("--limit", type=int, default=None, help="每场景只跑前 N 题，用于冒烟测试")
    ap.add_argument("--out", default=None, help="输出目录，默认 results/<框架>")
    args = ap.parse_args()

    qset = load_question_set()
    if not qset.get("frozen"):
        print("提示：question_set.json 尚未冻结（frozen=false），本次结果仅供调试。", file=sys.stderr)

    questions = qset["questions"]
    scenarios = SCENARIOS if args.scenario == "all" else [args.scenario]

    adapter = load_adapter(args.framework)
    out_dir = Path(args.out) if args.out else RESULTS_DIR / args.framework
    out_dir.mkdir(parents=True, exist_ok=True)

    all_rows: list[dict] = []
    all_lat: list[float] = []
    ingest_by_scenario: dict[str, dict] = {}

    with ResourceMonitor() as mon:
        for s in scenarios:
            print(f"[{args.framework}] 场景 {s} …", flush=True)
            rows, lat, stats = run_scenario(adapter, s, questions, args.limit)
            all_rows.extend(rows)
            all_lat.extend(lat)
            ingest_by_scenario[s] = {
                "n_items": stats.n_items,
                "wall_seconds": stats.wall_seconds,
                "tokens_in": stats.tokens_in,
                "tokens_out": stats.tokens_out,
                "notes": stats.notes,
            }
            with (out_dir / f"raw_{s}.jsonl").open("w", encoding="utf-8") as fh:
                for r in rows:
                    fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    n_q = len(all_rows)
    summary = {
        "framework": args.framework,
        "adapter_describe": adapter.describe(),
        "question_set_version": qset.get("version"),
        "question_set_frozen": qset.get("frozen", False),
        "scenarios": scenarios,
        "n_questions": n_q,
        "accuracy": aggregate(all_rows),
        "latency": latency_summary(all_lat),
        "ingest": ingest_by_scenario,
        "ingest_total": {
            "tokens_in": sum(v["tokens_in"] for v in ingest_by_scenario.values()),
            "tokens_out": sum(v["tokens_out"] for v in ingest_by_scenario.values()),
            "wall_seconds": round(sum(v["wall_seconds"] for v in ingest_by_scenario.values()), 3),
        },
        "query_tokens_avg": {
            "tokens_in": round(sum(r["tokens_in"] for r in all_rows) / n_q, 1) if n_q else 0,
            "tokens_out": round(sum(r["tokens_out"] for r in all_rows) / n_q, 1) if n_q else 0,
        },
        "resources": mon.summary(),
        "environment": environment_provenance(),
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_review_csv(out_dir / "review.csv", all_rows)

    a = summary["accuracy"]
    print(f"\n=== {args.framework} ===")
    print(f"自动判定准确率 {a['overall']['correct']}/{a['overall']['total']} = {a['overall']['accuracy']:.1%}")
    for t, v in a["by_type"].items():
        print(f"  {t:16s} {v['correct']:2d}/{v['total']:2d} = {v['accuracy']:.0%}")
    lat = summary["latency"]
    print(f"检索延迟 P50 {lat['p50_ms']} ms / P95 {lat['p95_ms']} ms")
    print(
        f"灌入 token {summary['ingest_total']['tokens_in']}，"
        f"单次查询平均 in/out {summary['query_tokens_avg']['tokens_in']}/"
        f"{summary['query_tokens_avg']['tokens_out']}"
    )
    r = summary["resources"]
    print(f"内存峰值 {r['peak_rss_mb']} MB，显存峰值 {r['peak_gpu_mb']}")
    print(f"\n结果目录 {out_dir}")
    print(f"人工核对表 {out_dir / 'review.csv'}（human_verdict 列留空待填）")
    if Counter(r["auto_verdict"] for r in all_rows)["错"] and not args.limit:
        print("提醒：自动判定仅供初筛，报告中的准确率必须以人工复核后的 human_verdict 为准。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
