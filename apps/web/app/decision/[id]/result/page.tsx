"use client";

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import {
  DecisionDetail,
  DecisionRun,
  Recommendation,
  getDecision,
  getLatestRun,
  getRecommendation,
} from "@/lib/api";

export default function ResultPage() {
  const params = useParams<{ id: string }>();
  const [detail, setDetail] = useState<DecisionDetail | null>(null);
  const [run, setRun] = useState<DecisionRun | null>(null);
  const [rec, setRec] = useState<Recommendation | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      getDecision(params.id),
      getLatestRun(params.id),
      getRecommendation(params.id),
    ])
      .then(([d, r, rc]) => {
        setDetail(d);
        setRun(r);
        setRec(rc);
      })
      .catch((err) =>
        setError(err instanceof Error ? err.message : "加载失败"),
      );
  }, [params.id]);

  if (error) return <p className="pt-8 text-red-600">{error}</p>;
  if (!detail || !run || !rec) {
    return <p className="pt-8 text-neutral-500">加载中……</p>;
  }

  const nameOf = (id: string | null) =>
    detail.options.find((o) => o.id === id)?.name ?? "—";
  const winnerName = nameOf(rec.recommended_option_id);
  const stability = run.ranking_stability ?? 0;
  const probs = run.result_snapshot.winner_probability;

  return (
    <main className="flex flex-col gap-4 pt-6">
      <section className="rounded-2xl bg-neutral-900 p-6 text-white">
        <p className="text-sm text-neutral-400">建议选择</p>
        <h1 className="text-3xl font-bold">{winnerName}</h1>
        <p className="mt-3 text-sm leading-relaxed text-neutral-200">
          {rec.summary}
        </p>
      </section>

      <section className="rounded-xl border border-neutral-200 bg-white p-4">
        <h2 className="mb-2 text-sm font-semibold text-neutral-500">推荐稳定性</h2>
        <div className="h-3 w-full overflow-hidden rounded-full bg-neutral-100">
          <div
            className="h-full rounded-full bg-neutral-900"
            style={{ width: `${Math.round(stability * 100)}%` }}
          />
        </div>
        <p className="mt-2 text-xs text-neutral-400">
          在 {run.result_snapshot ? "1000" : ""} 次模拟中，{winnerName} 有{" "}
          {Math.round(
            (probs[rec.recommended_option_id] ?? stability) * 100,
          )}
          % 的条件下排名第一
        </p>
        <ul className="mt-2 flex flex-col gap-1 text-xs text-neutral-500">
          {Object.entries(probs).map(([optionId, p]) => (
            <li key={optionId} className="flex items-center gap-2">
              <span className="w-32 shrink-0">{nameOf(optionId)}</span>
              <div className="h-2 flex-1 overflow-hidden rounded-full bg-neutral-100">
                <div
                  className="h-full bg-neutral-400"
                  style={{ width: `${Math.round(p * 100)}%` }}
                />
              </div>
              <span className="w-10 text-right">{Math.round(p * 100)}%</span>
            </li>
          ))}
        </ul>
      </section>

      <Card title="为什么这么建议" items={rec.main_reasons} />
      <Card
        title="需要接受的代价"
        items={rec.accepted_tradeoffs}
        accent="border-amber-300"
      />
      {rec.critical_unknowns.length > 0 && (
        <Card title="关键未知项" items={rec.critical_unknowns} />
      )}
      <Card title="什么情况值得重新评估" items={rec.reopen_conditions} />
      <Card
        title="什么情况不构成重开理由"
        items={rec.non_reopen_conditions}
        muted
      />

      {run.result_snapshot.eliminated.length > 0 && (
        <Card
          title="被硬约束淘汰的选项"
          items={run.result_snapshot.eliminated.map(
            (e) => `${nameOf(e.option_key)}：${e.reason}`,
          )}
        />
      )}

      {rec.challenger_output && (
        <section className="rounded-xl border border-blue-200 bg-blue-50 p-4 text-sm">
          <h2 className="mb-2 font-semibold text-blue-900">
            第二模型的质疑（供参考，不改变推荐）
          </h2>
          {rec.challenger_output.counterargument && (
            <p className="mb-1 text-blue-800">
              {rec.challenger_output.counterargument}
            </p>
          )}
          <ul className="list-inside list-disc text-blue-700">
            {rec.challenger_output.missing_assumptions.map((a) => (
              <li key={a}>{a}</li>
            ))}
            {rec.challenger_output.fragile_variables.map((v) => (
              <li key={v}>脆弱变量：{v}</li>
            ))}
          </ul>
        </section>
      )}

      <section className="rounded-xl border border-neutral-200 bg-white p-4">
        <h2 className="mb-1 text-sm font-semibold text-neutral-500">下一步</h2>
        <p className="text-sm">{rec.next_action}</p>
      </section>

      <button
        disabled
        className="rounded-xl bg-neutral-300 px-8 py-4 text-lg text-white"
        title="决策契约与锁定将在下个版本开放"
      >
        我理解并接受这个选择（即将开放）
      </button>
      <p className="pb-8 text-center text-xs text-neutral-400">
        算法版本 {run.algorithm_version} · 结果可复现（seed={run.seed}）
      </p>
    </main>
  );
}

function Card({
  title,
  items,
  accent,
  muted,
}: {
  title: string;
  items: string[];
  accent?: string;
  muted?: boolean;
}) {
  if (items.length === 0) return null;
  return (
    <section
      className={`rounded-xl border bg-white p-4 ${accent ?? "border-neutral-200"}`}
    >
      <h2 className="mb-2 text-sm font-semibold text-neutral-500">{title}</h2>
      <ul
        className={`flex list-inside list-disc flex-col gap-1 text-sm ${
          muted ? "text-neutral-400" : "text-neutral-800"
        }`}
      >
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </section>
  );
}
