from datetime import datetime

from app.normalizer import normalize, extract_timestamp, extract_level, extract_message
from app.models import LogLevel


def test_normalize_different_field_names():
    """测试不同字段名标准化"""
    log_with_timestamp = {
        "timestamp": "2024-03-15T10:00:00Z",
        "service": "test",
        "level": "error",
        "message": "test",
    }
    entry = normalize(log_with_timestamp, 0)
    assert entry.source == "test"
    assert entry.timestamp is not None

    log_with_time = {"time": 1710498225, "app": "test2",
                     "level": "info", "message": "test"}
    entry2 = normalize(log_with_time, 1)
    assert entry2.source == "test2"
    assert entry2.timestamp is not None


def test_missing_fields_no_crash():
    """缺少字段不崩溃"""
    log = {"level": "error"}
    entry = normalize(log, 0)
    assert entry.source == "unknown"
    assert entry.level.value == "error"
    assert entry.message == "(无消息)"


def test_dirty_data_marked():
    """脏数据被正确标记"""
    log = {}
    entry = normalize(log, 0)
    assert entry.is_dirty
    assert "缺少时间戳" in entry.dirty_reason


def test_level_mapping():
    """等级字段映射"""
    assert extract_level({"status": "WARNING"}) == LogLevel.WARN
    assert extract_level({"severity": "FATAL"}) == LogLevel.FATAL
    assert extract_level({"log_level": "CRITICAL"}) == LogLevel.CRITICAL
    assert extract_level({"level": "debug"}) == LogLevel.DEBUG


def test_extract_timestamp_various_formats():
    """不同时间格式解析"""
    iso = extract_timestamp({"timestamp": "2024-03-15T10:00:00Z"})
    assert iso is not None
    assert iso.year == 2024

    unix = extract_timestamp({"timestamp": 1710498225})
    assert unix is not None

    unix_ms = extract_timestamp({"timestamp": 1710498225000})
    assert unix_ms is not None

    slash = extract_timestamp({"timestamp": "2024/03/15 12:00:00"})
    assert slash is not None


def test_job_name_extraction():
    """任务名称提取"""
    log = {"source": "x", "level": "error",
           "message": "fail", "job": "my_job"}
    entry = normalize(log, 0)
    assert entry.job_name == "my_job"

    log2 = {"source": "x", "level": "error",
            "message": "fail", "task": "my_task"}
    entry2 = normalize(log2, 0)
    assert entry2.job_name == "my_task"
