"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { createDecision } from "@/lib/api";

export default function NewDecisionPage() {
  const router = useRouter();
  const [input, setInput] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!input.trim() || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const created = await createDecision(input.trim());
      router.push(`/decision/${created.decision_id}/conversation`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "提交失败，请重试");
      setSubmitting(false);
    }
  }

  return (
    <main className="flex flex-col gap-4 pt-8">
      <h1 className="text-2xl font-bold">你在纠结什么？</h1>
      <p className="text-sm text-neutral-500">
        用自己的话描述就行，比如在哪几个选项之间犹豫、担心什么、有什么限制。
      </p>
      <form onSubmit={onSubmit} className="flex flex-col gap-3">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          rows={6}
          placeholder="例如：我在雷鸟U8和三星S27B800之间纠结……"
          className="w-full rounded-xl border border-neutral-300 p-4 focus:border-neutral-900 focus:outline-none"
        />
        {error && <p className="text-sm text-red-600">{error}</p>}
        <button
          type="submit"
          disabled={submitting || !input.trim()}
          className="rounded-xl bg-neutral-900 px-6 py-3 text-white disabled:opacity-40"
        >
          {submitting ? "正在整理……" : "开始梳理"}
        </button>
      </form>
    </main>
  );
}
