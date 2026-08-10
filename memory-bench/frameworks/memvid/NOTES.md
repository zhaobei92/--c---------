# Memvid —— 建议降级为"可行性验证"，不强求跑满 60 题

**调研基于**：`memvid/memvid` @ `e6bd9f7`（2026-07-14）
**License**：Apache-2.0

## 状态
- 阶段2 静态调研：✅ 完成
- 实测：**建议只做 CLI 桥接的可行性验证**
- 适配器：❌ 未写（阻塞原因见下）

## 阻塞原因：没有 Python 绑定

2026-01-05 发布的 v2.0.0 是**完整的 Rust 重写**，前一天（2026-01-04）Python 实现被
整体删除——`memvid/*.py`、`examples/*.py` 全部消失。当前仓库 158 个 `.rs`、
**0 个 `.py`**。

`Cargo.toml` 里包名是 `memvid-core`，是个**库 crate**：没有 `[[bin]]`、没有
`src/bin/`、仓库里也没有 PyO3 绑定。

CLI 不在这个仓库里——`docker/cli/Dockerfile` 显示它是通过 npm 分发的预编译二进制：

```dockerfile
FROM ubuntu:24.04
RUN npm install -g memvid-cli@latest
```

所以 Python 侧要接，只有两条路：

1. **CLI 子进程桥接**：装 npm 的 `memvid-cli`，用 `subprocess` 调
   `create` / `put` / `find`。实现快，但每次查询要付进程启动开销，
   延迟数据会被污染，报告里必须注明不可与其它框架直接比较。
2. **自己写 PyO3 绑定**：数据干净，但工作量已超出"选型调研"的边界，
   违背宪法"禁止开发产品功能"的精神。

**建议走第 1 条，只验证可行性与检索质量。**

## 它依然值得认真对待的地方

端侧形态上它是五个里**最理想**的。`MV2_SPEC.md` 定义的单文件 `.mv2`：

```
头部 4KB | 内嵌 WAL 1-64MB | 数据段 | Tantivy 全文索引段
        | HNSW 向量索引段 | 时间索引段 | TOC(校验尾)
```

无数据库、无 sidecar 文件、Blake3 校验、Ed25519 签名、可选 AES-256-GCM 加密、
崩溃安全的 WAL 写入。对离线/边缘设备，这个形态几乎是量身定做的。

**多模态是五个里最强的**：CLIP 视觉 embedding、Whisper 音频转写（Candle 推理，
带 `cuda` / `metal` feature）、PDF/DOCX/XLSX 解析全在核心库里。对"机器人场景要纳入
图像与传感器数据"这个需求，它是唯一原生支持的。

Jetson 上 Rust 交叉编译到 aarch64 可行，依赖里的 `ort`(ONNX Runtime)、`tantivy`
都有 ARM64 支持。

## 需要警惕的点

- **活跃度大幅回落**：2026-01 有 167 次提交（重写期），此后 2 月 20、3 月 6、
  5 月 3、7 月 2 次。近 90 天仅 4 次提交 / 2 位作者。一个刚重写完就几乎停更的
  项目，用在生产上风险很高。
- **时间能力待验证**：有 `temporal_track` / `temporal_enrich` feature 和 timeline
  查询，但读 spec 无法确定是否支持**事实级失效**（像 Graphiti 的 `invalid_at`）。
  看起来更像"按时间浏览"而非"按时间点回溯事实"。需实测确认。
- 核心库与 CLI 分离，CLI 走 npm 预编译二进制，**开源程度不如表面看上去彻底**。
