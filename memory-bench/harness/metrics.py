# -*- coding: utf-8 -*-
"""指标采集：延迟分位数、token 计数、内存/显存峰值。

同样只依赖标准库；tiktoken 和 pynvml 属于可选增强，缺了会降级并在结果里注明，
不会让测试跑不起来。
"""

from __future__ import annotations

import re
import resource
import threading
import time
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# 延迟
# --------------------------------------------------------------------------


def percentile(values: list[float], p: float) -> float:
    """线性插值分位数。p 取 50 表示 P50。"""
    if not values:
        return 0.0
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    k = (len(xs) - 1) * (p / 100.0)
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def latency_summary(values: list[float]) -> dict:
    return {
        "n": len(values),
        "p50_ms": round(percentile(values, 50), 2),
        "p95_ms": round(percentile(values, 95), 2),
        "mean_ms": round(sum(values) / len(values), 2) if values else 0.0,
        "min_ms": round(min(values), 2) if values else 0.0,
        "max_ms": round(max(values), 2) if values else 0.0,
    }


# --------------------------------------------------------------------------
# token 计数
# --------------------------------------------------------------------------

_CJK = re.compile("[一-鿿　-〿＀-￯]")
_WORD = re.compile(r"[A-Za-z0-9_.\-/]+")

try:  # pragma: no cover - 取决于环境是否装了 tiktoken
    import tiktoken

    _ENC = tiktoken.get_encoding("cl100k_base")
    TOKENIZER = "tiktoken:cl100k_base"
except Exception:  # noqa: BLE001
    _ENC = None
    TOKENIZER = "heuristic:cjk1+word0.75"


def count_tokens(text: str) -> int:
    """统计 token 数。

    装了 tiktoken 就用 cl100k_base；否则退化为启发式：一个中日韩字符约 1 token，
    拉丁词约 0.75 token。跨框架对比时只要口径一致就不影响相对结论，
    结果文件里会记录当前用的是哪一种。
    """
    if not text:
        return 0
    if _ENC is not None:
        return len(_ENC.encode(text))
    cjk = len(_CJK.findall(text))
    words = len(_WORD.findall(text))
    return cjk + int(words * 0.75 + 0.5)


# --------------------------------------------------------------------------
# 内存与显存
# --------------------------------------------------------------------------


def _peak_rss_mb() -> float:
    """进程历史峰值 RSS（MB）。Linux 上 ru_maxrss 单位是 KB。"""
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def _current_rss_mb() -> float:
    try:
        with open("/proc/self/status", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
    except OSError:
        pass
    return 0.0


def _gpu_used_mb() -> float | None:
    try:  # pragma: no cover - 无 GPU 环境下走不到
        import pynvml

        pynvml.nvmlInit()
        total = 0
        for i in range(pynvml.nvmlDeviceGetCount()):
            h = pynvml.nvmlDeviceGetHandleByIndex(i)
            total += pynvml.nvmlDeviceGetMemoryInfo(h).used
        pynvml.nvmlShutdown()
        return total / (1024.0 * 1024.0)
    except Exception:  # noqa: BLE001
        return None


@dataclass
class ResourceMonitor:
    """后台采样进程内存与显存占用，取峰值。

    只能看到"本进程"的占用。像 Letta、Neo4j 这种把重活放在独立服务/容器里的框架，
    本进程数字会严重偏低——这种情况必须在报告里注明，并另行用容器统计补齐，
    不能拿进程内数字冒充框架总占用。
    """

    interval: float = 0.2
    samples_rss: list[float] = field(default_factory=list)
    samples_gpu: list[float] = field(default_factory=list)
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None
    gpu_available: bool = False

    def __enter__(self) -> "ResourceMonitor":
        self.gpu_available = _gpu_used_mb() is not None
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.samples_rss.append(_current_rss_mb())
            if self.gpu_available:
                g = _gpu_used_mb()
                if g is not None:
                    self.samples_gpu.append(g)
            self._stop.wait(self.interval)

    def summary(self) -> dict:
        out = {
            "peak_rss_mb": round(max([_peak_rss_mb()] + self.samples_rss), 1),
            "mean_rss_mb": round(sum(self.samples_rss) / len(self.samples_rss), 1)
            if self.samples_rss
            else 0.0,
            "samples": len(self.samples_rss),
            "gpu_available": self.gpu_available,
        }
        if self.samples_gpu:
            out["peak_gpu_mb"] = round(max(self.samples_gpu), 1)
        else:
            out["peak_gpu_mb"] = None
        return out


class Timer:
    """上下文计时器，返回毫秒。"""

    def __enter__(self) -> "Timer":
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, *exc) -> None:
        self.ms = (time.perf_counter() - self._t0) * 1000.0
