"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { getDueFollowups, submitFollowup } from "@/lib/api";

const LABELS: Record<string, string> = {
  h24: "24小时回访：执行了吗？",
  d7: "7天回访：现在感觉怎么样？",
  d30: "30天回访：回头看这个决定",
  d90: "90天回访",
};

export default function FollowupPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const [due, setDue] = useState<string[] | null>(null);
  const [executed, setExecuted] = useState<boolean | null>(null);
  const [satisfaction, setSatisfaction] = useState(0.5);
  const [regret, setRegret] = useState(0.2);
  const [notes, setNotes] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getDueFollowups(params.id)
      .then((r) => setDue(r.due))
      .catch((err) => setError(err instanceof Error ? err.message : "加载失败"));
  }, [params.id]);

  if (!due) return <p className="pt-8 text-neutral-500">{error ?? "加载中……"}</p>;
  if (due.length === 0) {
    return (
      <main className="pt-16 text-center">
        <p className="text-neutral-500">目前没有到期的回访。</p>
        <button
          onClick={() => router.push(`/decision/${params.id}/conversation`)}
          className="mt-4 rounded-xl bg-neutral-900 px-6 py-3 text-white"
        >
          返回
        </button>
      </main>
    );
  }

  const checkpoint = due[0];
  const isExecution = checkpoint === "h24";

  return (
    <main className="flex flex-col gap-4 pt-8">
      <h1 className="text-2xl font-bold">{LABELS[checkpoint] ?? checkpoint}</h1>
      {isExecution ? (
        <div className="flex gap-3">
          {[
            { label: "已执行", value: true },
            { label: "还没有", value: false },
          ].map((o) => (
            <button
              key={o.label}
              onClick={() => setExecuted(o.value)}
              className={`flex-1 rounded-xl border-2 p-4 ${
                executed === o.value
                  ? "border-neutral-900 bg-neutral-900 text-white"
                  : "border-neutral-300 bg-white"
              }`}
            >
              {o.label}
            </button>
          ))}
        </div>
      ) : (
        <>
          <label className="text-sm text-neutral-600">
            满意程度：{Math.round(satisfaction * 100)}
            <input
              type="range"
              min={0}
              max={100}
              value={satisfaction * 100}
              onChange={(e) => setSatisfaction(Number(e.target.value) / 100)}
              className="mt-1 w-full accent-neutral-900"
            />
          </label>
          <label className="text-sm text-neutral-600">
            后悔程度：{Math.round(regret * 100)}
            <input
              type="range"
              min={0}
              max={100}
              value={regret * 100}
              onChange={(e) => setRegret(Number(e.target.value) / 100)}
              className="mt-1 w-full accent-neutral-900"
            />
          </label>
        </>
      )}
      <textarea
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
        rows={3}
        placeholder="想补充什么（可选）"
        className="w-full rounded-xl border border-neutral-300 p-3"
      />
      <button
        onClick={async () => {
          setError(null);
          try {
            await submitFollowup(params.id, {
              checkpoint,
              executed: isExecution ? (executed ?? undefined) : undefined,
              satisfaction: isExecution ? undefined : satisfaction,
              regret_level: isExecution ? undefined : regret,
              notes: notes || undefined,
            });
            const next = await getDueFollowups(params.id);
            setDue(next.due);
            setNotes("");
            setExecuted(null);
          } catch (err) {
            setError(err instanceof Error ? err.message : "提交失败");
          }
        }}
        disabled={isExecution && executed === null}
        className="rounded-xl bg-neutral-900 px-6 py-3 text-white disabled:opacity-40"
      >
        提交
      </button>
      {error && <p className="text-sm text-red-600">{error}</p>}
    </main>
  );
}
