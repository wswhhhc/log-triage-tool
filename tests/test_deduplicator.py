from app.deduplicator import normalize_message, generate_fingerprint, merge_anomalies
from app.models import LogEntry, LogLevel, AnomalyType
from datetime import datetime


def make_log(message, source="test-service", raw=None):
    return LogEntry(
        id="x", timestamp=datetime.now(), source=source,
        level=LogLevel.ERROR, message=message, trace_id="tr-123",
        raw=raw or {},
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


def test_normalize_removes_url():
    msg = "callback failed: https://api.example.com/v1/hook?token=abc"
    normalized = normalize_message(msg)
    assert "https://" not in normalized


def test_normalize_removes_path():
    msg = "write failed to /data/output/file.parquet"
    normalized = normalize_message(msg)
    assert "/data/" not in normalized


def test_normalize_synonym():
    """中英文同义词被归一化"""
    msg = "数据库 connection timeout"
    normalized = normalize_message(msg)
    assert "数据库" not in normalized
    assert "database" in normalized


def test_normalize_abbreviation():
    """常见缩写被归一化"""
    normalized = normalize_message("conn lost")
    assert "connection" in normalized, f"conn 应扩展为 connection, 实际: {normalized}"
    assert "error" in normalize_message("err: failed")
    assert "request" in normalize_message("req failed")
    assert "response" in normalize_message("resp invalid")


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


def test_unknown_source_different_raw_content_different_fingerprint():
    """
    unknown source 但原始内容不同 → 不同 fingerprint
    这是抗脏数据的关键：缺少 source 的两个服务不会被错误合并
    """
    log1 = make_log("database timeout", source="unknown",
                    raw={"service": "payment-service", "message": "timeout"})
    log2 = make_log("database timeout", source="unknown",
                    raw={"app": "order-worker", "message": "timeout"})

    fp1 = generate_fingerprint(log1, AnomalyType.TIMEOUT)
    fp2 = generate_fingerprint(log2, AnomalyType.TIMEOUT)

    assert fp1 != fp2, "不同服务的 unknown source 不应合并"


def test_unknown_source_same_raw_content_same_fingerprint():
    """
    unknown source 但原始内容相同 → 同一 fingerprint
    同一服务多条日志仍应合并
    """
    log1 = make_log("database timeout", source="unknown",
                    raw={"service": "my-svc", "job": "job-1"})
    log2 = make_log("database timeout", source="unknown",
                    raw={"service": "my-svc", "job": "job-1"})

    fp1 = generate_fingerprint(log1, AnomalyType.TIMEOUT)
    fp2 = generate_fingerprint(log2, AnomalyType.TIMEOUT)

    assert fp1 == fp2, "同一 unknown 来源的日志应合并"


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


def test_merge_anomalies_missing_classification():
    """merge_anomalies 在 classified 缺失某个 ID 时不崩溃，回退为 UNKNOWN"""
    logs = [
        make_log("timeout error", source="svc"),
        make_log("database error", source="svc"),
    ]
    logs[0].id = "log_a"
    logs[1].id = "log_b"
    # 故意不提供 log_b 的分类
    classified = {"log_a": AnomalyType.TIMEOUT}

    issues = merge_anomalies(logs, classified)
    # 不崩溃即成功，log_b 用 UNKNOWN 兜底
    assert len(issues) >= 1
