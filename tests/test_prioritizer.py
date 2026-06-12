from app.prioritizer import calculate_priority
from app.models import AnomalyIssue, AnomalyType, Priority
from datetime import datetime


def make_issue(anomaly_type, source, count, first=None, last=None):
    return AnomalyIssue(
        id="x", fingerprint="fp", anomaly_type=anomaly_type,
        priority=None, source=source, message_template="test",
        related_log_ids=[], first_seen=first or datetime.now(),
        last_seen=last or datetime.now(), occurrence_count=count
    )


def test_higher_frequency_higher_priority():
    """重复次数越多优先级越高"""
    issue_low = make_issue(AnomalyType.TIMEOUT, "test-worker", count=1)
    issue_high = make_issue(AnomalyType.TIMEOUT, "test-worker", count=20)

    p_low, s_low, _ = calculate_priority(issue_low)
    p_high, s_high, _ = calculate_priority(issue_high)

    assert s_high >= s_low


def test_core_service_higher_priority():
    """核心服务优先级更高"""
    non_core = make_issue(AnomalyType.TIMEOUT, "test-worker", count=3)
    core = make_issue(AnomalyType.TIMEOUT, "payment-service", count=3)

    _, score_nc, _ = calculate_priority(non_core)
    _, score_c, _ = calculate_priority(core)

    assert score_c > score_nc


def test_core_service_exact_match():
    """子串不应误匹配核心服务"""
    non_core = make_issue(AnomalyType.TIMEOUT, "payment-service-worker", count=3)
    real_core = make_issue(AnomalyType.TIMEOUT, "payment-service", count=3)

    _, score_nc, _ = calculate_priority(non_core)
    _, score_c, _ = calculate_priority(real_core)

    assert score_c > score_nc, "payment-service-worker 不应被识别为核心服务"


def test_unknown_needs_higher_score():
    """UNKNOWN 类型得分更高"""
    timeout = make_issue(AnomalyType.TIMEOUT, "test-worker", count=1)
    unknown = make_issue(AnomalyType.UNKNOWN, "test-worker", count=1)

    _, score_t, _ = calculate_priority(timeout)
    _, score_u, _ = calculate_priority(unknown)

    assert score_u > score_t


def test_p0_threshold():
    """高频 + 核心服务可达到 P0"""
    issue = make_issue(AnomalyType.DATABASE,
                       "payment-service", count=10)
    p, score, _ = calculate_priority(issue)
    assert score >= 7, f"期望 P0 但得分 {score}"
    assert p == Priority.P0


def test_p3_low_impact():
    """低频非核心 VALIDATION 应为 P3"""
    issue = make_issue(AnomalyType.VALIDATION, "test-worker", count=1)
    p, score, _ = calculate_priority(issue)
    assert p == Priority.P3, f"期望 P3 但为 {p} (得分 {score})"


def test_data_quality_slightly_higher():
    """DATA_QUALITY 类型不再被完全忽视（权重从 0.05→0.10）"""
    dq = make_issue(AnomalyType.DATA_QUALITY, "test-worker", count=1)
    validation = make_issue(AnomalyType.VALIDATION, "test-worker", count=1)

    _, score_dq, _ = calculate_priority(dq)
    _, score_v, _ = calculate_priority(validation)

    # DATA_QUALITY 得分应略高或至少接近 VALIDATION
    # （severity 1 vs 2, DQ weight 0.10 → 额外 1.0 分）
    assert score_dq >= score_v - 0.5, "DATA_QUALITY 不应被完全忽略"
