"""端到端集成测试：模拟各种脏数据场景跑通全流水线"""
import json
import os
import tempfile

from app.deduplicator import merge_anomalies
from app.classifier import classify, is_anomaly
from app.normalizer import normalize
from app.parser import parse_jsonl
from app.prioritizer import calculate_priority
from app.storage import get_conn, init_db, _safe_json, _safe_timestamp


def _build_edge_case_jsonl(tmpdir):
    """构造包含各种脏数据场景的 JSONL 文件"""
    rows = [
        # 0: 正常日志
        '{"source":"svc-a","level":"error","timestamp":"2024-01-01T00:00:00Z","message":"connection timeout"}',
        # 1: 正常日志（INFO，不应标记为异常）
        '{"source":"svc-a","level":"info","timestamp":"2024-01-01T00:00:01Z","message":"health check passed"}',
        # 2: bool 时间戳（曾是 bug）
        '{"source":"svc-b","level":"error","timestamp":true,"message":"db failed"}',
        # 3: 数值消息
        '{"source":"svc-b","level":"error","timestamp":"2024-01-01T00:00:02Z","message":500}',
        # 4: 非 dict JSON
        '["list", "not", "dict"]',
        # 5: 完全无效 JSON
        'NOT JSON AT ALL',
        # 6: 空行（应跳过）
        '',
        # 7: 缺少 source
        '{"level":"error","timestamp":"2024-01-01T00:00:03Z","message":"no source field"}',
        # 8: 时间戳格式带时区偏移
        '{"source":"svc-c","level":"error","timestamp":"2024-06-15T10:00:00+08:00","message":"timeout from Asia"}',
        # 9: message 过短 (<3 chars)
        '{"source":"svc-d","level":"error","timestamp":"2024-01-01T00:00:04Z","message":"OK"}',
    ]
    filepath = os.path.join(tmpdir, "edge_cases.jsonl")
    with open(filepath, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(row + "\n")
    return filepath


def test_end_to_end_edge_cases():
    """全流水线脏数据场景验证"""
    init_db()

    with tempfile.TemporaryDirectory() as tmpdir:
        filepath = _build_edge_case_jsonl(tmpdir)

        # 1) 解析
        raw_logs, parse_dirty = parse_jsonl(filepath)
        assert len(parse_dirty) == 2, f"应有 2 条解析脏数据, 实际 {len(parse_dirty)}"
        # 非 dict (#4) 和 无效 JSON (#5)
        reasons = [d["reason"] for d in parse_dirty]
        assert any("非法结构" in r for r in reasons), f"应捕获非 dict: {reasons}"
        assert any("无效 JSON" in r for r in reasons), f"应捕获无效 JSON: {reasons}"

        # 2) 标准化
        entries = [normalize(raw, i) for i, raw in enumerate(raw_logs)]

        # 验证 bool 时间戳 (#2) 被正确拒绝
        assert entries[2].timestamp is None, "bool 不应被解析为时间戳"
        assert entries[2].is_dirty, "bool 时间戳应标记为脏"

        # 验证数值消息 (#3) 被提取
        assert entries[3].message == "500", f"数值消息应为 '500', 实际 '{entries[3].message}'"

        # 验证缺少 source (行#7 → raw_logs[4]) 降级为 unknown
        assert entries[4].source == "unknown", "缺少 source 应为 unknown"

        # 验证时区偏移时间戳 (行#8 → raw_logs[5]) 正确解析
        assert entries[5].timestamp is not None, "带时区偏移的时间戳应被解析"

        # 验证短消息 (行#9 → raw_logs[6]) 被标记为脏
        assert entries[6].is_dirty, "短消息应标记为脏"

        # 3) 标记异常
        for e in entries:
            e.is_anomaly = is_anomaly(e)

        anomalies = [e for e in entries if e.is_anomaly]
        # svc-a timeout → 异常；svc-b 两条 → 异常；svc-c timeout → 异常；
        # 缺少 source → 异常；短消息 → 异常；数值 500 → 异常（level=error）
        assert len(anomalies) >= 5, f"应有至少 5 条异常, 实际 {len(anomalies)}"

        # 4) 分类 + 合并 + 优先级（不崩溃即通过）
        classified = {e.id: classify(e) for e in anomalies}
        issues = merge_anomalies(anomalies, classified)

        for issue in issues:
            priority, score, reason = calculate_priority(issue)
            issue.priority = priority
            issue.priority_reason = reason

        # 5) 保存到 SQLite 不崩溃
        conn = get_conn()
        try:
            c = conn.cursor()
            for entry in entries:
                c.execute(
                    "INSERT OR REPLACE INTO logs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        entry.id,
                        _safe_timestamp(entry.timestamp),
                        entry.source,
                        entry.level.value,
                        entry.message,
                        entry.job_name,
                        entry.trace_id,
                        _safe_json(entry.raw, {}),
                        int(entry.is_dirty),
                        entry.dirty_reason,
                        int(entry.is_anomaly),
                    ),
                )
            conn.commit()
        finally:
            conn.close()
