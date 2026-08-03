"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { DecisionSummary, listDecisions } from "@/lib/api";

export default function HistoryPage() {
  const [items, setItems] = useState<DecisionSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listDecisions()
      .then(setItems)
      .catch((err) =>
        setError(err instanceof Error ? err.message : "加载失败"),
      );
  }, []);

  return (
    <main className="flex flex-col gap-4 pt-8">
      <h1 className="text-2xl font-bold">以前的决定</h1>
      {error && <p className="text-sm text-red-600">{error}</p>}
      {items === null && !error && (
        <p className="text-neutral-500">加载中……</p>
      )}
      {items?.length === 0 && (
        <p className="text-neutral-500">还没有记录。</p>
      )}
      <ul className="flex flex-col gap-2">
        {items?.map((c) => (
          <li key={c.id}>
            <Link
              href={`/decision/${c.id}/conversation`}
              className="block rounded-xl border border-neutral-200 bg-white px-4 py-3 hover:border-neutral-400"
            >
              <span className="font-medium">{c.title}</span>
              <span className="ml-2 text-xs text-neutral-400">{c.status}</span>
            </Link>
          </li>
        ))}
      </ul>
    </main>
  );
}
