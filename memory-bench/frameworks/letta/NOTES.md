# Letta —— 建议记录状态后跳过实测

**调研基于**：`letta-ai/letta` @ `ff19ffeaf`（2026-08-01）
及 `letta-ai/letta-code` @ `9d8fb8d6`（2026-08-09）
**License**：两者均 Apache-2.0

## 状态
- 阶段2 静态调研：✅ 完成
- 实测：**建议跳过**（宪法第 5 条：停维护的项目记录原因后跳过，不硬耗）
- 适配器：❌ 未写（待确认是否仍要测）

## 跳过理由：主仓已由官方明确弃用

`letta-ai/letta` 根目录 `AGENTS.md` 原文：

> ## This repository is deprecated
> This repository contains the **legacy Letta server**: the self-hosted API server
> (`letta/letta` image) that powers the Letta V1 API and V1 SDKs. It is in
> maintenance mode and is no longer where active development happens.

提交曲线印证这不是文档笔误：

```
2025-06  467    2025-11  333    2026-04    3
2025-07  524    2025-12  155    2026-05    1
2025-08  548    2026-01  310    2026-06    2
2025-09  378    2026-02  201    2026-07    2
2025-10  430    2026-03  216    2026-08    1
```

2026-04 起断崖归零。近 90 天仅 6 次提交 / 2 位作者，内容多为 README 与 AI 使用
政策，无功能开发。

## 开发迁到了哪里

`letta-ai/letta-code`：近 90 天 1019 提交 / 33 位作者，非常活跃。但形态变了：

| 维度 | 旧 letta | 新 letta-code |
|---|---|---|
| 语言 | Python | TypeScript / Bun |
| 形态 | 可自托管的记忆 API 服务端 | CLI 编码 agent + TS SDK |
| 自托管 | 本仓库的 API server | 另一套「App Server」 |
| 嵌入方式 | Python 库 | `npm install @letta-ai/letta-agent-sdk` |

对我们"Python 记忆核心 + 养老/宠物/机器人专用适配"的选型目标，新形态已经错位：
它是个编码助手产品，不是可嵌入的记忆层。

## 它仍然值得借鉴的地方（写进报告的设计参考部分）

分层记忆的思路本身是好的，尤其 core memory 那部分——常驻上下文的**具名、可直接
读写的文本块**，让人一眼能看到 agent 当前"脑子里装着什么"。这个可解释性设计比
向量库的黑箱强得多，值得在我们自己的通用核心里借鉴。

## 如果仍要实测

说一声，我按 legacy Python 服务端写适配器并跑满 60 题。但结论只对 legacy 版本
有效，且需要 Postgres + 服务端进程（端侧最重的一个）。我的建议是把这份时间投给
Hindsight。
