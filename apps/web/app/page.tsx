import Link from "next/link";

export default function HomePage() {
  return (
    <main className="flex flex-col gap-6 pt-16">
      <h1 className="text-3xl font-bold">定了</h1>
      <p className="text-neutral-500">
        识别纠结、澄清偏好、作出决定、接受代价，然后停止反复推翻。
      </p>
      <nav className="flex flex-col gap-3 pt-4">
        <Link
          href="/decision/new"
          className="rounded-xl bg-neutral-900 px-6 py-4 text-center text-lg text-white hover:bg-neutral-700"
        >
          我正在纠结
        </Link>
        <Link
          href="/history"
          className="rounded-xl border border-neutral-300 px-6 py-4 text-center text-lg hover:bg-neutral-100"
        >
          我又开始后悔了
        </Link>
        <Link
          href="/history"
          className="rounded-xl border border-neutral-300 px-6 py-4 text-center text-lg hover:bg-neutral-100"
        >
          看看以前的决定
        </Link>
      </nav>
    </main>
  );
}
