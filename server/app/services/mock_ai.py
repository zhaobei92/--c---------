"""Mock AI Provider(Phase 3):写入真实数据库行的固定转写与摘要。

用途:无真机/无真实 ASR 时打通完整产品闭环(Demo Mode 与商店审核同路径)。
输出契约与真实供应商一致:speakers + transcript_segments(词句级时间戳)
+ summaries(每条结论/待办强制携带 evidence 时间戳,PRD §6.3)。
真实 ASR/LLM 在阶段3 替换本模块,表结构与 API 不变。
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from ..models.tables import Speaker, Summary, TranscriptSegment, TranscriptionJob

# 固定演示脚本:一场双人项目会议(时间戳单位 ms)
MOCK_SEGMENTS: list[dict] = [
    {"speaker": "Speaker 1", "start_ms": 0, "end_ms": 6500,
     "text": "大家好,今天我们对齐一下录音笔项目的进度。", "language": "zh", "confidence": 0.96},
    {"speaker": "Speaker 2", "start_ms": 6500, "end_ms": 15200,
     "text": "好的。硬件协议文档昨天已经发给方案商,样机预计两周内到。", "language": "zh", "confidence": 0.94},
    {"speaker": "Speaker 1", "start_ms": 15200, "end_ms": 24800,
     "text": "服务端这边,转写管线和计费系统已经在测试环境跑通了。", "language": "zh", "confidence": 0.95},
    {"speaker": "Speaker 2", "start_ms": 24800, "end_ms": 36000,
     "text": "App 需要在下周五之前完成 Demo 模式,给苹果审核用。", "language": "zh", "confidence": 0.93},
    {"speaker": "Speaker 1", "start_ms": 36000, "end_ms": 45500,
     "text": "明白,那我们把阿拉伯语界面的验收也排进下个迭代。", "language": "zh", "confidence": 0.95},
    {"speaker": "Speaker 2", "start_ms": 45500, "end_ms": 52000,
     "text": "OK, let's sync again next Monday.", "language": "en", "confidence": 0.9},
]

# 摘要条目以 MOCK_SEGMENTS 下标声明证据,写库时解析为真实 segment_id
MOCK_SUMMARY_ITEMS: list[dict] = [
    {"type": "conclusion", "text": "硬件协议已发方案商,样机预计两周内到位。",
     "confidence": 0.92, "evidence_segments": [1]},
    {"type": "conclusion", "text": "服务端转写管线与计费系统已在测试环境跑通。",
     "confidence": 0.93, "evidence_segments": [2]},
    {"type": "todo", "text": "下周五前完成 App Demo 模式(苹果审核用)。",
     "confidence": 0.9, "evidence_segments": [3]},
    {"type": "todo", "text": "阿拉伯语界面验收排入下个迭代。",
     "confidence": 0.88, "evidence_segments": [4]},
]


def write_mock_transcript(s: Session, job: TranscriptionJob) -> None:
    """幂等:同 recording 已有分段则跳过(重复投递/重试不产生重复行)。"""
    from sqlalchemy import select

    existing = s.execute(select(TranscriptSegment.id).where(
        TranscriptSegment.recording_id == job.recording_id).limit(1)).first()
    if existing is not None:
        return
    speakers: dict[str, Speaker] = {}
    for label in {seg["speaker"] for seg in MOCK_SEGMENTS}:
        row = s.execute(select(Speaker).where(
            Speaker.recording_id == job.recording_id,
            Speaker.label == label)).scalar_one_or_none()
        if row is None:
            row = Speaker(id=str(uuid.uuid4()),
                          recording_id=job.recording_id, label=label)
            s.add(row)
        speakers[label] = row
    s.flush()
    for i, seg in enumerate(MOCK_SEGMENTS):
        s.add(TranscriptSegment(
            id=str(uuid.uuid4()), job_id=job.id, recording_id=job.recording_id,
            seq=i, start_ms=seg["start_ms"], end_ms=seg["end_ms"],
            speaker_id=speakers[seg["speaker"]].id,
            language=seg["language"], text=seg["text"],
            confidence=seg["confidence"],
        ))
    s.flush()


def write_mock_summary(s: Session, job: TranscriptionJob) -> None:
    """每条结论/待办解析出真实 segment_id 证据(验收红线:待办 100% 带时间戳)。"""
    from sqlalchemy import select

    existing = s.execute(select(Summary.id).where(
        Summary.recording_id == job.recording_id).limit(1)).first()
    if existing is not None:
        return
    segments = s.execute(
        select(TranscriptSegment).where(
            TranscriptSegment.recording_id == job.recording_id)
        .order_by(TranscriptSegment.seq)
    ).scalars().all()
    items = []
    for item in MOCK_SUMMARY_ITEMS:
        evidence = [
            {"segment_id": str(segments[idx].id),
             "start_ms": segments[idx].start_ms,
             "end_ms": segments[idx].end_ms}
            for idx in item["evidence_segments"] if idx < len(segments)
        ]
        items.append({"type": item["type"], "text": item["text"],
                      "confidence": item["confidence"], "evidence": evidence})
    s.add(Summary(
        id=str(uuid.uuid4()), recording_id=job.recording_id, job_id=job.id,
        content={"template": "meeting", "items": items},
    ))
    s.flush()
