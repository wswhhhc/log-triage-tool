from datetime import datetime, timezone
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


def _extract_first(raw: dict, fields: list, *, allow_types=(str,)) -> Optional[str]:
    """从多个候选字段中提取第一个符合类型的值"""
    for field in fields:
        val = raw.get(field)
        if val is not None and isinstance(val, allow_types):
            return str(val)
    return None


def extract_timestamp(raw: dict) -> Optional[datetime]:
    """从多种时间格式中提取时间戳（防御式解析）"""
    for field in TIME_FIELDS:
        val = raw.get(field)
        if val is None:
            continue

        # 1) 数值型时间戳
        # 注意：Python 中 bool 是 int 的子类，必须先排除
        if isinstance(val, bool):
            continue
        if isinstance(val, (int, float)):
            if val <= 0:
                continue
            if val > 1e12:
                val = val / 1000
            try:
                ts = datetime.fromtimestamp(val)
                # 合理性校验
                if ts.year < 1900 or ts.year > 2100:
                    continue
                return ts
            except (OSError, ValueError, OverflowError):
                continue

        # 2) 字符串型时间
        if isinstance(val, str):
            val = val.strip()
            if not val:
                continue

            # 2a) Python 3.11+ fromisoformat 覆盖绝大多数 ISO 8601 变体
            try:
                dt = datetime.fromisoformat(val)
                # 带时区的转换为 UTC 并去掉 tzinfo 保持 naive
                if dt.tzinfo is not None:
                    dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
                if 1900 <= dt.year <= 2100:
                    return dt
                continue
            except (ValueError, TypeError):
                pass

            # 2b) 手动格式兜底
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
        "emerg": LogLevel.CRITICAL,
        "emergency": LogLevel.CRITICAL,
        "alert": LogLevel.CRITICAL,
        "notice": LogLevel.WARN,
        "trace": LogLevel.DEBUG,
    }
    return level_map.get(val_lower, LogLevel.UNKNOWN)


def extract_message(raw: dict) -> Optional[str]:
    # 优先字符串
    val = _extract_first(raw, MESSAGE_FIELDS, allow_types=(str,))
    if val is not None:
        stripped = val.strip()
        if len(stripped) >= 3:
            return val
        return None

    # 兜底：数值类型（如 "error": 500, "message": 503）
    val = _extract_first(raw, MESSAGE_FIELDS, allow_types=(int, float))
    if val is not None and isinstance(val, bool) is False:
        str_val = str(val)
        if len(str_val) >= 1:
            return str_val
    return None


def extract_source(raw: dict) -> Optional[str]:
    return _extract_first(raw, SOURCE_FIELDS, allow_types=(str,))


def _extract_str(raw: dict, *keys) -> Optional[str]:
    """安全提取字符串字段"""
    for key in keys:
        val = raw.get(key)
        if val is not None and isinstance(val, str):
            return val
    return None


def normalize(raw: dict, line_index: int) -> LogEntry:
    """将原始日志字典标准化为 LogEntry（防御式）"""
    # 防御：如果 raw 不是 dict，生成一个标记脏数据的 entry
    if not isinstance(raw, dict):
        return LogEntry(
            id=f"log_{line_index}",
            timestamp=None,
            source="unknown",
            level=LogLevel.UNKNOWN,
            message="(非 dict 输入)",
            raw={},
            is_dirty=True,
            dirty_reason=f"输入类型错误: 期望 dict, 实际为 {type(raw).__name__}",
        )

    timestamp = extract_timestamp(raw)
    source = extract_source(raw)
    level = extract_level(raw)
    message = extract_message(raw)
    job_name = _extract_str(raw, "job_name", "job", "task")
    trace_id = _extract_str(raw, "trace_id", "traceId", "request_id")

    is_dirty = False
    dirty_reasons = []

    if timestamp is None:
        is_dirty = True
        dirty_reasons.append("缺少时间戳或格式无法解析")
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
