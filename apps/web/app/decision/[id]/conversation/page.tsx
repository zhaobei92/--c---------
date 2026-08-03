"use client";

import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  DecisionDetail,
  addConstraint,
  addOption,
  deleteConstraint,
  deleteOption,
  getDecision,
  sendMessage,
  streamUrl,
  updateOption,
} from "@/lib/api";

const STATUS_LABELS: Record<string, string> = {
  DRAFT: "草稿",
  INTAKE: "正在解析",
  RISK_TRIAGE: "风险评估",
  PROBLEM_NORMALIZATION: "问题梳理",
  GUIDED_ONLY: "引导模式",
};

export default function ConversationPage() {
  const params = useParams<{ id: string }>();
  const [detail, setDetail] = useState<DecisionDetail | null>(null);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const streamStarted = useRef(false);

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

  // 状态为 INTAKE 时自动连接 SSE 执行解析流水线
  useEffect(() => {
    if (!detail || detail.status !== "INTAKE" || streamStarted.current) return;
    streamStarted.current = true;
    setStreaming(true);
    const source = new EventSource(streamUrl(params.id));
    const finish = () => {
      source.close();
      setStreaming(false);
      void refresh();
    };
    source.addEventListener("done", finish);
    source.onerror = finish;
    return () => source.close();
  }, [detail, params.id, refresh]);

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
          状态：{STATUS_LABELS[detail.status] ?? detail.status} · 风险：
          {detail.risk_level}
          {streaming && " · 正在解析你的描述……"}
        </p>
      </header>

      <StructuredPanel detail={detail} onChanged={refresh} />

      <section className="flex flex-col gap-3">
        {detail.messages.map((m) => (
          <div
            key={m.id}
            className={
              m.role === "user"
                ? "ml-8 self-end rounded-2xl rounded-br-sm bg-neutral-900 px-4 py-3 text-white"
                : "mr-8 self-start whitespace-pre-wrap rounded-2xl rounded-bl-sm bg-white px-4 py-3 shadow-sm"
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

function StructuredPanel({
  detail,
  onChanged,
}: {
  detail: DecisionDetail;
  onChanged: () => Promise<void>;
}) {
  const [newOption, setNewOption] = useState("");
  const [newConstraint, setNewConstraint] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingName, setEditingName] = useState("");

  const hasContent =
    detail.options.length > 0 ||
    detail.constraints.length > 0 ||
    detail.facts.length > 0;
  if (!hasContent && detail.status === "INTAKE") return null;

  return (
    <section className="rounded-xl border border-neutral-200 bg-white p-4 text-sm">
      <h2 className="mb-2 font-semibold">
        结构化信息
        <span className="ml-2 font-normal text-neutral-400">
          AI 可能有误，可直接修改
        </span>
      </h2>

      <div className="mb-3">
        <p className="mb-1 text-xs font-medium text-neutral-500">选项</p>
        <ul className="flex flex-col gap-1">
          {detail.options.map((o) => (
            <li key={o.id} className="flex items-center gap-2">
              {editingId === o.id ? (
                <>
                  <input
                    value={editingName}
                    onChange={(e) => setEditingName(e.target.value)}
                    className="flex-1 rounded border border-neutral-300 px-2 py-1"
                  />
                  <button
                    className="text-neutral-900 underline"
                    onClick={async () => {
                      if (editingName.trim()) {
                        await updateOption(detail.id, o.id, {
                          name: editingName.trim(),
                        });
                      }
                      setEditingId(null);
                      await onChanged();
                    }}
                  >
                    保存
                  </button>
                </>
              ) : (
                <>
                  <span className="flex-1">
                    {o.name}
                    {o.description && (
                      <span className="text-neutral-400">
                        {" "}
                        · {o.description}
                      </span>
                    )}
                  </span>
                  <button
                    className="text-xs text-neutral-400 underline"
                    onClick={() => {
                      setEditingId(o.id);
                      setEditingName(o.name);
                    }}
                  >
                    编辑
                  </button>
                  <button
                    className="text-xs text-red-400 underline"
                    onClick={async () => {
                      await deleteOption(detail.id, o.id);
                      await onChanged();
                    }}
                  >
                    删除
                  </button>
                </>
              )}
            </li>
          ))}
        </ul>
        <form
          className="mt-1 flex gap-2"
          onSubmit={async (e) => {
            e.preventDefault();
            if (!newOption.trim()) return;
            await addOption(detail.id, newOption.trim());
            setNewOption("");
            await onChanged();
          }}
        >
          <input
            value={newOption}
            onChange={(e) => setNewOption(e.target.value)}
            placeholder="补充选项……"
            className="flex-1 rounded border border-neutral-200 px-2 py-1"
          />
          <button type="submit" className="text-xs text-neutral-500 underline">
            添加
          </button>
        </form>
      </div>

      <div className="mb-3">
        <p className="mb-1 text-xs font-medium text-neutral-500">约束</p>
        <ul className="flex flex-col gap-1">
          {detail.constraints.map((c) => (
            <li key={c.id} className="flex items-center gap-2">
              <span className="flex-1">
                {c.description}
                <span className="ml-1 text-xs text-neutral-400">
                  {c.is_hard ? "硬约束" : "偏好"}
                </span>
              </span>
              <button
                className="text-xs text-red-400 underline"
                onClick={async () => {
                  await deleteConstraint(detail.id, c.id);
                  await onChanged();
                }}
              >
                删除
              </button>
            </li>
          ))}
        </ul>
        <form
          className="mt-1 flex gap-2"
          onSubmit={async (e) => {
            e.preventDefault();
            if (!newConstraint.trim()) return;
            await addConstraint(detail.id, newConstraint.trim(), true);
            setNewConstraint("");
            await onChanged();
          }}
        >
          <input
            value={newConstraint}
            onChange={(e) => setNewConstraint(e.target.value)}
            placeholder="补充约束，如：预算不超过5000元"
            className="flex-1 rounded border border-neutral-200 px-2 py-1"
          />
          <button type="submit" className="text-xs text-neutral-500 underline">
            添加
          </button>
        </form>
      </div>

      {detail.facts.length > 0 && (
        <Facet label="已知事实" items={detail.facts} />
      )}
      {detail.concerns.length > 0 && (
        <Facet label="担忧" items={detail.concerns} />
      )}
      {detail.unknowns.length > 0 && (
        <Facet label="待澄清" items={detail.unknowns} />
      )}
    </section>
  );
}

function Facet({ label, items }: { label: string; items: string[] }) {
  return (
    <div className="mb-2">
      <p className="mb-1 text-xs font-medium text-neutral-500">{label}</p>
      <ul className="list-inside list-disc text-neutral-700">
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </div>
  );
}
