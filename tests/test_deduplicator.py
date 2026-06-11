from app.deduplicator import normalize_message, generate_fingerprint, merge_anomalies
from app.models import LogEntry, LogLevel, AnomalyType
from datetime import datetime


def make_log(message, source="test-service"):
    return LogEntry(
        id="x", timestamp=datetime.now(), source=source,
        level=LogLevel.ERROR, message=message, trace_id="tr-123"
    )


def test_normalize_removes_order_id():
    msg = "timeout for order_id=ORD-88271"
    normalized = normalize_message(msg)
    assert "ORD-88271" not in normalized
    assert "<ORDER_ID>" in normalized or "<order_id>" in normalized


def test_normalize_removes_uuid():
    msg = "error at a1b2c3d4-e5f6-7890-abcd-ef1234567890 while processing"
    normalized = normalize_message(msg)
    assert "a1b2c3d4" not in normalized
    assert "<UUID>" in normalized or "<uuid>" in normalized


def test_normalize_removes_ip():
    msg = "connection from 192.168.1.100 refused"
    normalized = normalize_message(msg)
    assert "192.168.1.100" not in normalized


def test_normalize_synonym():
    """中英文同义词被归一化"""
    msg = "数据库 connection timeout"
    normalized = normalize_message(msg)
    # "数据库"被归一化为"database"，"超时"不在msg中所以不用检查
    assert "数据库" not in normalized
    assert "database" in normalized


def test_same_error_different_ids_same_fingerprint():
    """同一错误含不同ID → 同一 fingerprint"""
    log1 = make_log("timeout for order_id=ORD-88271")
    log2 = make_log("timeout for order_id=ORD-99231")

    fp1 = generate_fingerprint(log1, AnomalyType.TIMEOUT)
    fp2 = generate_fingerprint(log2, AnomalyType.TIMEOUT)

    assert fp1 == fp2


def test_different_root_cause_different_fingerprint():
    """不同根因相似文案 → 不同 fingerprint"""
    log1 = make_log("database timeout")
    log2 = make_log("permission denied")

    fp1 = generate_fingerprint(log1, AnomalyType.TIMEOUT)
    fp2 = generate_fingerprint(log2, AnomalyType.AUTH)

    assert fp1 != fp2


def test_chinese_english_same_type_same_fingerprint():
    """中英文同类错误 → 同一 fingerprint"""
    log1 = make_log("database timeout")
    log2 = make_log("数据库超时 db timeout")

    fp1 = generate_fingerprint(log1, AnomalyType.TIMEOUT)
    fp2 = generate_fingerprint(log2, AnomalyType.TIMEOUT)

    assert fp1 == fp2


def test_different_source_different_fingerprint():
    """不同来源相同异常 → 不同 fingerprint"""
    log1 = make_log("database timeout", source="payment-service")
    log2 = make_log("database timeout", source="order-worker")

    fp1 = generate_fingerprint(log1, AnomalyType.TIMEOUT)
    fp2 = generate_fingerprint(log2, AnomalyType.TIMEOUT)

    assert fp1 != fp2


def test_merge_anomalies():
    logs = [
        make_log("timeout for order=123", source="pay"),
        make_log("timeout for order=456", source="pay"),
        make_log("permission denied", source="pay"),
    ]
    classified = {"x": AnomalyType.TIMEOUT}
    # force distinct IDs
    logs[0].id = "log_0"
    logs[1].id = "log_1"
    logs[2].id = "log_2"
    classified = {"log_0": AnomalyType.TIMEOUT,
                  "log_1": AnomalyType.TIMEOUT,
                  "log_2": AnomalyType.AUTH}

    issues = merge_anomalies(logs, classified)
    assert len(issues) == 2
    # find TIMEOUT issue
    timeout_issues = [
        i for i in issues if i.anomaly_type == AnomalyType.TIMEOUT]
    assert len(timeout_issues) == 1
    assert timeout_issues[0].occurrence_count == 2
