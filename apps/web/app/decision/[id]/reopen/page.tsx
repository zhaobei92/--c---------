"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import {
  ClosureContract,
  ReopenResult,
  getContract,
  reopenDecision,
} from "@/lib/api";

export default function ReopenPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const [contract, setContract] = useState<ClosureContract | null>(null);
  const [input, setInput] = useState("");
  const [result, setResult] = useState<ReopenResult | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getContract(params.id)
      .then(setContract)
      .catch(() => setContract(null));
  }, [params.id]);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!input.trim() || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      setResult(await reopenDecision(params.id, input.trim()));
    } catch (err) {
      setError(err instanceof Error ? err.message : "提交失败");
    } finally {
      setSubmitting(false);
    }
  }

  if (result) {
    const reopened = result.outcome === "full_reopen";
    return (
      <main className="flex flex-col gap-4 pt-10">
        <h1 className="text-2xl font-bold">
          {reopened ? "可以重新评估" : "决定保持锁定"}
        </h1>
        <p className="rounded-xl bg-white p-4 text-sm leading-relaxed shadow-sm">
          {result.message}
        </p>
        <p className="text-xs text-neutral-400">
          重开评分 {result.reopen_score.toFixed(2)}（阈值 0.70）· 信息新颖度{" "}
          {result.novelty.toFixed(2)}
        </p>
        <button
          onClick={() =>
            router.push(
              reopened
                ? `/decision/${params.id}/conversation`
                : `/decision/${params.id}/result`,
            )
          }
          className="rounded-xl bg-neutral-900 px-6 py-3 text-white"
        >
          {reopened ? "继续重新分析" : "回到结果页"}
        </button>
      </main>
    );
  }

  return (
    <main className="flex flex-col gap-4 pt-8">
      <h1 className="text-2xl font-bold">发生了什么新情况？</h1>
      <p className="text-sm text-neutral-500">
        具体说说出现了什么<strong>新的事实</strong>
        （价格变化、质量问题、用途变化等）。如果只是又开始不安，也可以直接写出来。
      </p>
      {contract && contract.reopen_conditions.length > 0 && (
        <div className="rounded-xl border border-neutral-200 bg-white p-4 text-xs text-neutral-500">
          <p className="mb-1 font-medium">当时约定的重开条件：</p>
          <ul className="list-inside list-disc">
            {contract.reopen_conditions.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
        </div>
      )}
      <form onSubmit={onSubmit} className="flex flex-col gap-3">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          rows={5}
          placeholder="例如：卖家承认这台设备拆修过 / 价格降了1500元 / 我又开始怀疑自己……"
          className="w-full rounded-xl border border-neutral-300 p-4 focus:border-neutral-900 focus:outline-none"
        />
        <button
          type="submit"
          disabled={submitting || !input.trim()}
          className="rounded-xl bg-neutral-900 px-6 py-3 text-white disabled:opacity-40"
        >
          {submitting ? "评估中……" : "评估是否需要重开"}
        </button>
      </form>
      {error && <p className="text-sm text-red-600">{error}</p>}
    </main>
  );
}
