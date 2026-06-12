from app.recommender import get_recommendation, need_human
from app.models import AnomalyIssue, AnomalyType, Priority
from datetime import datetime


def make_issue(anomaly_type, source, count, priority=Priority.P2):
    return AnomalyIssue(
        id="x", fingerprint="fp", anomaly_type=anomaly_type,
        priority=priority, source=source, message_template="test",
        related_log_ids=[], first_seen=datetime.now(),
        last_seen=datetime.now(), occurrence_count=count
    )


def test_unknown_needs_human():
    """UNKNOWN 类型必须人工"""
    issue = make_issue(AnomalyType.UNKNOWN, "test-worker",
                       count=1, priority=Priority.P3)
    nh, reason = need_human(issue)
    assert nh is True


def test_low_freq_timeout_can_auto():
    """低频 timeout 可自动处理"""
    issue = make_issue(AnomalyType.TIMEOUT, "test-worker",
                       count=1, priority=Priority.P3)
    nh, reason = need_human(issue)
    assert nh is False


def test_p0_needs_human():
    """P0 必须人工"""
    issue = make_issue(AnomalyType.TIMEOUT, "test-worker",
                       count=1, priority=Priority.P0)
    nh, reason = need_human(issue)
    assert nh is True


def test_high_frequency_needs_human():
    """重复超过5次需要人工"""
    issue = make_issue(AnomalyType.TIMEOUT, "test-worker",
                       count=6, priority=Priority.P3)
    nh, reason = need_human(issue)
    assert nh is True


def test_auth_needs_human():
    """认证问题需要人工"""
    issue = make_issue(AnomalyType.AUTH, "test-worker",
                       count=1, priority=Priority.P2)
    nh, reason = need_human(issue)
    assert nh is True
    assert "认证安全" in reason


def test_get_recommendation_not_none():
    """每种类型都有建议"""
    for at in AnomalyType:
        issue = make_issue(at, "test", count=1)
        assert get_recommendation(issue) is not None
        assert len(get_recommendation(issue)) > 0
