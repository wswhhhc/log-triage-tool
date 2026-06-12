from app.classifier import is_anomaly, classify
from app.models import LogEntry, LogLevel, AnomalyType
from datetime import datetime


def make_log(message, level=LogLevel.ERROR, source="test", is_dirty=False):
    return LogEntry(
        id="x", timestamp=datetime.now(), source=source,
        level=level, message=message, is_dirty=is_dirty,
        dirty_reason="脏数据" if is_dirty else None,
    )


def test_is_anomaly_error_level():
    assert is_anomaly(make_log("something", level=LogLevel.ERROR))
    assert is_anomaly(make_log("fatal", level=LogLevel.FATAL))
    assert is_anomaly(make_log("huge problem", level=LogLevel.CRITICAL))


def test_is_anomaly_keyword():
    assert is_anomaly(make_log("request timeout", level=LogLevel.WARN))
    assert is_anomaly(make_log("请求超时", level=LogLevel.INFO))
    assert is_anomaly(make_log("connection refused", level=LogLevel.WARN))


def test_normal_not_anomaly():
    assert not is_anomaly(
        make_log("all systems operational", level=LogLevel.INFO))
    assert not is_anomaly(
        make_log("健康检查通过", level=LogLevel.INFO))


def test_dirty_is_anomaly():
    """脏数据被视为异常"""
    assert is_anomaly(make_log("", level=LogLevel.INFO, is_dirty=True))


def test_classify_timeout_both_languages():
    """中英文 timeout 都归为 TIMEOUT"""
    log_en = make_log("database timeout after 30s", level=LogLevel.ERROR)
    assert classify(log_en) == AnomalyType.TIMEOUT

    log_cn = make_log("数据库超时", level=LogLevel.ERROR)
    assert classify(log_cn) == AnomalyType.TIMEOUT


def test_classify_auth():
    log = make_log("permission denied for user admin", level=LogLevel.ERROR)
    assert classify(log) == AnomalyType.AUTH

    log_cn = make_log("用户认证失败: 无权限访问", level=LogLevel.ERROR)
    assert classify(log_cn) == AnomalyType.AUTH


def test_classify_database():
    log = make_log("mysql connection pool exhausted", level=LogLevel.ERROR)
    assert classify(log) == AnomalyType.DATABASE

    log_cn = make_log("数据库连接失败", level=LogLevel.ERROR)
    assert classify(log_cn) == AnomalyType.DATABASE


def test_classify_network():
    log = make_log("connection refused to service:9090", level=LogLevel.ERROR)
    assert classify(log) == AnomalyType.NETWORK


def test_classify_unknown():
    log = make_log("something strange happened", level=LogLevel.ERROR)
    assert classify(log) == AnomalyType.UNKNOWN


def test_classify_timeout_before_database():
    """timeout 优先级高于 database — 同时出现时归为 TIMEOUT"""
    log = make_log("database timeout after 30s")
    assert classify(log) == AnomalyType.TIMEOUT


def test_dirty_log_classified_by_content_first():
    """
    脏数据但消息内容能识别类型时，以内容分类为准
    不应因为是脏数据就全部归为 DATA_QUALITY
    """
    log = make_log("database timeout", level=LogLevel.WARN, is_dirty=True)
    assert classify(log) == AnomalyType.TIMEOUT, "应该优先按内容分类"


def test_dirty_log_without_clear_type_becomes_data_quality():
    """脏数据且消息无法归类时 → DATA_QUALITY"""
    log = make_log("", level=LogLevel.INFO, is_dirty=True)
    assert classify(log) == AnomalyType.DATA_QUALITY


def test_classify_resource_limit_new_keywords():
    """新增关键词分类"""
    log = make_log("disk quota exceeded", level=LogLevel.ERROR)
    assert classify(log) == AnomalyType.RESOURCE_LIMIT


def test_classify_resource_limit_quota():
    log = make_log("quota exceeded for namespace", level=LogLevel.ERROR)
    assert classify(log) == AnomalyType.RESOURCE_LIMIT
