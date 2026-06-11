import math

from app.models import AnomalyIssue, AnomalyType, Priority

# 核心服务定义
CORE_SERVICES = {"payment-service", "order-worker", "auth-gateway", "risk-api"}

# 权重配置
WEIGHTS = {
    "severity": 0.30,
    "frequency": 0.25,
    "core_service": 0.25,
    "unknown": 0.15,
    "data_quality": 0.05,
}


def calculate_priority(issue: AnomalyIssue):
    """
    计算优先级分数并返回 (Priority, score, reason)
    加权分数量级为 0-10，直接映射到优先级阈值。
    """
    score = 0.0
    reasons = []

    # 1. 严重程度 (0-10)
    severity_scores = {
        AnomalyType.TIMEOUT: 5,
        AnomalyType.AUTH: 7,
        AnomalyType.DATABASE: 8,
        AnomalyType.NETWORK: 4,
        AnomalyType.VALIDATION: 2,
        AnomalyType.RESOURCE_LIMIT: 3,
        AnomalyType.DATA_QUALITY: 1,
        AnomalyType.UNKNOWN: 9,
    }
    severity = severity_scores.get(issue.anomaly_type, 5)
    score += WEIGHTS["severity"] * severity
    reasons.append(f"严重程度={severity}({issue.anomaly_type.value})")

    # 2. 重复次数 (对数缩放，0-10)
    frequency = min(10, math.log2(1 + issue.occurrence_count) * 2.5)
    score += WEIGHTS["frequency"] * frequency
    reasons.append(f"重复次数={issue.occurrence_count}(得分{frequency:.1f})")

    # 3. 核心业务链路 (0 或 10)
    is_core = any(svc in issue.source for svc in CORE_SERVICES)
    core_score = 10 if is_core else 0
    score += WEIGHTS["core_service"] * core_score
    reasons.append(f"核心链路={'是' if is_core else '否'}(得分{core_score})")

    # 4. 未知错误 (0 或 10)
    is_unknown = issue.anomaly_type == AnomalyType.UNKNOWN
    unknown_score = 10 if is_unknown else 0
    score += WEIGHTS["unknown"] * unknown_score
    reasons.append(f"未知错误={'是' if is_unknown else '否'}(得分{unknown_score})")

    # 5. 数据质量 (0 或 10)
    dq_score = 10 if issue.anomaly_type == AnomalyType.DATA_QUALITY else 0
    score += WEIGHTS["data_quality"] * dq_score
    reasons.append(f"数据质量={'是' if issue.anomaly_type == AnomalyType.DATA_QUALITY else '否'}(得分{dq_score})")

    # 6. 时间连续性加分
    if issue.first_seen and issue.last_seen:
        time_span = (issue.last_seen - issue.first_seen).total_seconds()
        if time_span > 3600:
            score += 1.0
            reasons.append("持续发生超1小时(+1.0)")

    # 阈值映射（分数已保证在 0-10 量级）
    if score >= 7:
        priority = Priority.P0
    elif score >= 4:
        priority = Priority.P1
    elif score >= 2:
        priority = Priority.P2
    else:
        priority = Priority.P3

    return priority, round(score, 1), "; ".join(reasons)
