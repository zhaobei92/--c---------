"use client";

import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import {
  DecisionDetail,
  computeDecision,
  createRecommendation,
  getDecision,
  getEvaluations,
  putEvaluations,
} from "@/lib/api";

export default function AnalysisPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const [detail, setDetail] = useState<DecisionDetail | null>(null);
  const [values, setValues] = useState<Record<string, number>>({});
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const key = (optionId: string, criterionId: string) => `${optionId}:${criterionId}`;

  const load = useCallback(async () => {
    try {
      const [d, evals] = await Promise.all([
        getDecision(params.id),
        getEvaluations(params.id),
      ]);
      setDetail(d);
      const initial: Record<string, number> = {};
      for (const o of d.options.filter((o) => o.is_eligible)) {
        for (const c of d.criteria) {
          initial[key(o.id, c.id)] = 0.5;
        }
      }
      for (const e of evals) {
        initial[key(e.option_id, e.criterion_id)] = e.expected_value;
      }
      setValues(initial);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败");
    }
  }, [params.id]);

  useEffect(() => {
    void load();
  }, [load]);

  async function runAnalysis() {
    if (!detail || running) return;
    setRunning(true);
    setError(null);
    try {
      const items = Object.entries(values).map(([k, v]) => {
        const [option_id, criterion_id] = k.split(":");
        return { option_id, criterion_id, expected_value: v };
      });
      await putEvaluations(params.id, items);
      await computeDecision(params.id);
      await createRecommendation(params.id);
      router.push(`/decision/${params.id}/result`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "分析失败");
      setRunning(false);
    }
  }

  if (!detail) {
    return <p className="pt-8 text-neutral-500">{error ?? "加载中……"}</p>;
  }

  const eligibleOptions = detail.options.filter((o) => o.is_eligible);

  return (
    <main className="flex flex-col gap-6 pt-8">
      <header>
        <h1 className="text-xl font-bold">给每个选项打分</h1>
        <p className="text-sm text-neutral-400">
          按你目前的了解拖动滑块（不确定就放中间，算法会把不确定性考虑进去）
        </p>
      </header>

      {eligibleOptions.map((option) => (
        <section
          key={option.id}
          className="rounded-xl border border-neutral-200 bg-white p-4"
        >
          <h2 className="mb-3 font-semibold">{option.name}</h2>
          <div className="flex flex-col gap-3">
            {detail.criteria.map((criterion) => {
              const k = key(option.id, criterion.id);
              return (
                <label key={criterion.id} className="flex items-center gap-3 text-sm">
                  <span className="w-28 shrink-0 text-neutral-600">
                    {criterion.name}
                  </span>
                  <input
                    type="range"
                    min={0}
                    max={100}
                    value={Math.round((values[k] ?? 0.5) * 100)}
                    onChange={(e) =>
                      setValues((prev) => ({
                        ...prev,
                        [k]: Number(e.target.value) / 100,
                      }))
                    }
                    className="flex-1 accent-neutral-900"
                  />
                  <span className="w-10 text-right text-neutral-400">
                    {Math.round((values[k] ?? 0.5) * 100)}
                  </span>
                </label>
              );
            })}
          </div>
        </section>
      ))}

      <button
        onClick={runAnalysis}
        disabled={running}
        className="rounded-xl bg-neutral-900 px-8 py-4 text-lg text-white disabled:opacity-40"
      >
        {running ? "正在运行1000次模拟……" : "运行决策分析"}
      </button>
      {error && <p className="text-sm text-red-600">{error}</p>}
    </main>
  );
}
