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


def test_extended_level_mapping():
    """补充等级映射"""
    assert extract_level({"level": "TRACE"}) == LogLevel.DEBUG
    assert extract_level({"level": "NOTICE"}) == LogLevel.WARN
    assert extract_level({"level": "ALERT"}) == LogLevel.CRITICAL
    assert extract_level({"level": "EMERGENCY"}) == LogLevel.CRITICAL
    assert extract_level({"level": "EMERG"}) == LogLevel.CRITICAL


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


def test_extract_timestamp_timezone_offset():
    """带时区偏移的时间格式"""
    offset = extract_timestamp({"timestamp": "2024-03-15T10:00:00+08:00"})
    assert offset is not None
    # 10:00 +08:00 = 02:00 UTC
    assert offset.hour == 2, f"期望 02:00 UTC, 实际 {offset.hour}"

    offset2 = extract_timestamp({"timestamp": "2024-03-15T10:00:00.123+08:00"})
    assert offset2 is not None

    offset3 = extract_timestamp({"timestamp": "2024-03-15T02:00:00Z"})
    assert offset3 is not None


def test_extract_timestamp_unreasonable():
    """不合理时间戳被拒绝"""
    assert extract_timestamp({"timestamp": 9e12}) is None   # 未来时间
    assert extract_timestamp({"timestamp": "1800-01-01T00:00:00Z"}) is None  # 太早
    assert extract_timestamp({"timestamp": "3000-01-01T00:00:00Z"}) is None  # 太晚
    assert extract_timestamp({"timestamp": -1}) is None  # 负数


def test_non_string_source():
    """source 为非字符串时优雅降级"""
    entry = normalize({"source": {"host": "svc-1"}, "level": "error", "message": "fail"}, 0)
    # dict 类型不会被 _extract_first 提取
    assert entry.source == "unknown"
    assert "缺少来源系统" in entry.dirty_reason


def test_non_dict_input():
    """normalize 接收非 dict 输入不崩溃"""
    entry = normalize(None, 0)
    assert entry.is_dirty
    assert "输入类型错误" in entry.dirty_reason

    entry2 = normalize("string", 1)
    assert entry2.is_dirty
    assert "输入类型错误" in entry2.dirty_reason

    entry3 = normalize(42, 2)
    assert entry3.is_dirty
    assert "输入类型错误" in entry3.dirty_reason


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


def test_non_string_job_name():
    """job_name 非字符串时安全处理"""
    entry = normalize({"source": "x", "level": "error",
                       "message": "fail", "job": 12345}, 0)
    assert entry.job_name is None  # 数字类型不被提取


def test_non_utf8_characters():
    """包含特殊 Unicode 字符的消息不崩溃"""
    entry = normalize({"source": "x", "level": "error",
                       "message": "error: 𐀀 test"}, 0)
    assert entry.message is not None
