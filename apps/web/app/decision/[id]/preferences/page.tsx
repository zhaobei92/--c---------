"use client";

import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { NextComparison, getNextComparison, submitComparison } from "@/lib/api";

export default function PreferencesPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const [state, setState] = useState<NextComparison | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setState(await getNextComparison(params.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败");
    }
  }, [params.id]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function answer(choice: "left" | "right" | "equal" | "incomparable") {
    if (!state?.left || !state.right || submitting) return;
    setSubmitting(true);
    try {
      await submitComparison(params.id, state.left.id, state.right.id, choice, 0.7);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "提交失败");
    } finally {
      setSubmitting(false);
    }
  }

  if (!state) {
    return <p className="pt-8 text-neutral-500">{error ?? "加载中……"}</p>;
  }

  if (state.done) {
    return (
      <main className="flex flex-col gap-4 pt-16 text-center">
        <h1 className="text-2xl font-bold">偏好已了解</h1>
        <p className="text-neutral-500">
          已完成 {state.comparisons_done} 组比较，权重已根据你的回答更新。
        </p>
        <button
          onClick={() => router.push(`/decision/${params.id}/analysis`)}
          className="mx-auto rounded-xl bg-neutral-900 px-8 py-3 text-white"
        >
          继续：为选项打分
        </button>
      </main>
    );
  }

  return (
    <main className="flex flex-col gap-6 pt-8">
      <header>
        <h1 className="text-xl font-bold">哪个对你更重要？</h1>
        <p className="text-sm text-neutral-400">
          第 {state.comparisons_done + 1} / {state.comparisons_required} 组 ·
          凭直觉选就好，不需要精确
        </p>
      </header>
      <div className="flex gap-3">
        <button
          onClick={() => answer("left")}
          disabled={submitting}
          className="flex-1 rounded-2xl border-2 border-neutral-300 bg-white p-6 text-lg font-medium hover:border-neutral-900 disabled:opacity-40"
        >
          {state.left?.name}
        </button>
        <button
          onClick={() => answer("right")}
          disabled={submitting}
          className="flex-1 rounded-2xl border-2 border-neutral-300 bg-white p-6 text-lg font-medium hover:border-neutral-900 disabled:opacity-40"
        >
          {state.right?.name}
        </button>
      </div>
      <div className="flex justify-center gap-4 text-sm">
        <button
          onClick={() => answer("equal")}
          disabled={submitting}
          className="text-neutral-500 underline"
        >
          差不多一样重要
        </button>
        <button
          onClick={() => answer("incomparable")}
          disabled={submitting}
          className="text-neutral-400 underline"
        >
          没法比较
        </button>
      </div>
      {error && <p className="text-sm text-red-600">{error}</p>}
    </main>
  );
}
