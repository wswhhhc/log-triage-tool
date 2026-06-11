from datetime import datetime
from typing import Optional

from app.models import LogEntry, LogLevel

# 时间字段名映射（按优先级查找）
TIME_FIELDS = ["timestamp", "time", "created_at", "@timestamp", "log_time"]

# 来源字段名映射
SOURCE_FIELDS = ["source", "service", "app", "system", "application"]

# 等级字段名映射
LEVEL_FIELDS = ["level", "severity", "status", "log_level"]

# 错误信息字段名映射
MESSAGE_FIELDS = ["message", "error", "msg", "detail", "description"]


def extract_timestamp(raw: dict) -> Optional[datetime]:
    """从多种时间格式中提取时间戳"""
    for field in TIME_FIELDS:
        val = raw.get(field)
        if val is None:
            continue

        if isinstance(val, (int, float)):
            if val > 1e12:
                val = val / 1000
            try:
                return datetime.fromtimestamp(val)
            except (OSError, ValueError, OverflowError):
                continue

        if isinstance(val, str):
            formats = [
                "%Y-%m-%dT%H:%M:%S.%fZ",
                "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M:%S.%f",
                "%Y/%m/%d %H:%M:%S",
            ]
            for fmt in formats:
                try:
                    return datetime.strptime(val, fmt)
                except ValueError:
                    continue
    return None


def _extract_first(raw: dict, fields: list) -> Optional[str]:
    for field in fields:
        val = raw.get(field)
        if val is not None:
            return str(val)
    return None


def extract_level(raw: dict) -> LogLevel:
    val = _extract_first(raw, LEVEL_FIELDS)
    if val is None:
        return LogLevel.UNKNOWN
    val_lower = val.strip().lower()
    level_map = {
        "debug": LogLevel.DEBUG,
        "info": LogLevel.INFO,
        "warn": LogLevel.WARN,
        "warning": LogLevel.WARN,
        "error": LogLevel.ERROR,
        "fatal": LogLevel.FATAL,
        "critical": LogLevel.CRITICAL,
    }
    return level_map.get(val_lower, LogLevel.UNKNOWN)


def extract_message(raw: dict) -> Optional[str]:
    val = _extract_first(raw, MESSAGE_FIELDS)
    if val is not None and len(val.strip()) >= 3:
        return val
    return None


def extract_source(raw: dict) -> Optional[str]:
    return _extract_first(raw, SOURCE_FIELDS)


def normalize(raw: dict, line_index: int) -> LogEntry:
    """将原始日志字典标准化为 LogEntry"""
    timestamp = extract_timestamp(raw)
    source = extract_source(raw)
    level = extract_level(raw)
    message = extract_message(raw)
    job_name = raw.get("job_name") or raw.get("job") or raw.get("task")
    trace_id = raw.get("trace_id") or raw.get("traceId") or raw.get("request_id")

    is_dirty = False
    dirty_reasons = []

    if timestamp is None:
        is_dirty = True
        dirty_reasons.append("缺少时间戳")
    if source is None:
        dirty_reasons.append("缺少来源系统")
    if message is None:
        is_dirty = True
        dirty_reasons.append("错误信息缺失或过短")

    return LogEntry(
        id=f"log_{line_index}",
        timestamp=timestamp,
        source=source or "unknown",
        level=level,
        message=message or "(无消息)",
        job_name=job_name,
        trace_id=trace_id,
        raw=raw,
        is_dirty=is_dirty,
        dirty_reason="; ".join(dirty_reasons) if dirty_reasons else None,
    )
