# frameworks/

每个被测框架一个子目录，阶段2 逐个填充：

```
frameworks/<名字>/
├── setup.sh      建独立虚拟环境 + 安装依赖的全部命令（宪法第 3 条）
├── adapter.py    实现 harness/adapters/base.py 里的 MemoryAdapter 接口
└── NOTES.md      安装耗时、依赖体积、踩坑、官方最小示例是否跑通
```

## adapter.py 的要求

必须导出一个名为 `Adapter` 的类，继承 `MemoryAdapter`，实现 `setup` / `ingest`
/ `query`，并在 `describe()` 里如实报告：所用 LLM 与 embedding 模型、存储后端、
是否支持 `as_of` 时间点过滤、是否支持指定 top-k。

**不得**在适配器里写针对本问题集的特殊提示词、关键词硬编码或答案后处理——
详见 `docs/plan.md` 第一节"公平性约束"。

## setup.sh 的要求

- 建立**独立**虚拟环境，不污染其它框架
- 记录安装耗时与依赖体积（脚本末尾打印 `du -sh` 结果即可）
- 失败要退出非零并打印原因，不要静默跳过

## 参考实现

`harness/adapters/baseline_bm25.py` 是一个完整、可运行的适配器示例。
