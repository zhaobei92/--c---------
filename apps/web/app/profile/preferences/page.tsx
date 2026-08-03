"use client";

import { useEffect, useState } from "react";
import {
  PreferencePosterior,
  deletePreferenceProfile,
  getPreferenceProfile,
} from "@/lib/api";

export default function PreferenceProfilePage() {
  const [items, setItems] = useState<PreferencePosterior[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = () =>
    getPreferenceProfile()
      .then(setItems)
      .catch((err) => setError(err instanceof Error ? err.message : "加载失败"));

  useEffect(() => {
    void load();
  }, []);

  if (!items) return <p className="pt-8 text-neutral-500">{error ?? "加载中……"}</p>;

  return (
    <main className="flex flex-col gap-4 pt-8">
      <h1 className="text-2xl font-bold">我的决策偏好画像</h1>
      <p className="text-sm text-neutral-500">
        由多次已执行决策的回访逐渐确认；单次决策不会写入画像。
      </p>
      {items.length === 0 ? (
        <p className="text-neutral-400">
          还没有足够的数据。完成决策并回访后，这里会逐渐形成你的偏好画像。
        </p>
      ) : (
        <ul className="flex flex-col gap-2">
          {items.map((p) => (
            <li
              key={p.id}
              className="rounded-xl border border-neutral-200 bg-white p-4 text-sm"
            >
              <div className="flex items-center justify-between">
                <span className="font-medium">{p.criterion_name}</span>
                <span className="text-xs text-neutral-400">
                  {p.category} · {p.evidence_count} 次证据
                </span>
              </div>
              <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-neutral-100">
                <div
                  className="h-full bg-neutral-900"
                  style={{ width: `${Math.round(p.posterior_mean * 100)}%` }}
                />
              </div>
              <p className="mt-1 text-xs text-neutral-400">
                重要度 {(p.posterior_mean * 100).toFixed(0)} ±
                {(p.posterior_std * 100).toFixed(0)}
              </p>
            </li>
          ))}
        </ul>
      )}
      {items.length > 0 && (
        <button
          onClick={async () => {
            await deletePreferenceProfile();
            await load();
          }}
          className="self-start text-sm text-red-500 underline"
        >
          删除我的偏好画像
        </button>
      )}
      {error && <p className="text-sm text-red-600">{error}</p>}
    </main>
  );
}
