"use client";

import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import {
  DecisionDetail,
  getDecision,
  sendMessage,
} from "@/lib/api";

export default function ConversationPage() {
  const params = useParams<{ id: string }>();
  const [detail, setDetail] = useState<DecisionDetail | null>(null);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setDetail(await getDecision(params.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败");
    }
  }, [params.id]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function onSend(e: React.FormEvent) {
    e.preventDefault();
    if (!input.trim() || sending) return;
    setSending(true);
    setError(null);
    try {
      await sendMessage(params.id, input.trim());
      setInput("");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "发送失败");
    } finally {
      setSending(false);
    }
  }

  if (!detail) {
    return <p className="pt-8 text-neutral-500">{error ?? "加载中……"}</p>;
  }

  return (
    <main className="flex flex-col gap-4 pt-4">
      <header className="border-b border-neutral-200 pb-3">
        <h1 className="text-xl font-bold">{detail.title}</h1>
        <p className="text-xs text-neutral-400">
          状态：{detail.status} · 风险：{detail.risk_level}
        </p>
      </header>
      <section className="flex flex-col gap-3">
        {detail.messages.map((m) => (
          <div
            key={m.id}
            className={
              m.role === "user"
                ? "ml-8 self-end rounded-2xl rounded-br-sm bg-neutral-900 px-4 py-3 text-white"
                : "mr-8 self-start rounded-2xl rounded-bl-sm bg-white px-4 py-3 shadow-sm"
            }
          >
            {m.content}
          </div>
        ))}
      </section>
      <form onSubmit={onSend} className="sticky bottom-4 flex gap-2 pt-4">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="继续补充……"
          className="flex-1 rounded-xl border border-neutral-300 px-4 py-3 focus:border-neutral-900 focus:outline-none"
        />
        <button
          type="submit"
          disabled={sending || !input.trim()}
          className="rounded-xl bg-neutral-900 px-5 py-3 text-white disabled:opacity-40"
        >
          发送
        </button>
      </form>
      {error && <p className="text-sm text-red-600">{error}</p>}
    </main>
  );
}
