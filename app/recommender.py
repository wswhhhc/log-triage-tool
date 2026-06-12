from app.models import AnomalyIssue, AnomalyType, Priority

RECOMMENDATIONS = {
    AnomalyType.TIMEOUT: "检查下游服务可用性、网络延迟和重试配置。确认超时阈值是否合理。",
    AnomalyType.AUTH: "检查 token 有效性、权限配置和凭证是否过期。排查认证服务日志。",
    AnomalyType.DATABASE: "检查数据库连接池状态、慢查询日志、锁等待和实例健康状态。",
    AnomalyType.NETWORK: "检查网络连通性、DNS 解析、防火墙规则和服务发现配置。",
    AnomalyType.VALIDATION: "检查输入数据格式、必填字段完整性和上游数据质量。",
    AnomalyType.RESOURCE_LIMIT: "检查限流配置、队列积压情况、容量水位和扩容策略。",
    AnomalyType.DATA_QUALITY: "检查日志采集管道，确认字段映射规则和数据格式规范。",
    AnomalyType.UNKNOWN: "需要人工查看原始日志和上下文信息，确定根因后补充分类规则。",
}

CORE_SERVICES = {"payment-service", "order-worker", "auth-gateway"}


def get_recommendation(issue: AnomalyIssue) -> str:
    """获取处理建议"""
    return RECOMMENDATIONS.get(issue.anomaly_type, "需要进一步分析")


def need_human(issue: AnomalyIssue):
    """判断是否需要人工介入，返回 (是否需要人工, 原因)"""
    reasons = []

    if issue.anomaly_type == AnomalyType.UNKNOWN:
        reasons.append("未知类型异常，需人工确认根因")

    if issue.priority in (Priority.P0, Priority.P1):
        reasons.append(f"{issue.priority.value} 高优先级问题")

    if issue.occurrence_count > 5:
        reasons.append(f"重复发生 {issue.occurrence_count} 次，超过自动处理阈值")

    if any(svc in issue.source for svc in CORE_SERVICES):
        if issue.priority in (Priority.P0, Priority.P1):
            reasons.append(f"涉及核心服务 {issue.source}")

    if issue.anomaly_type == AnomalyType.AUTH:
        reasons.append("涉及认证安全，需人工确认")

    can_auto = []
    if issue.anomaly_type == AnomalyType.TIMEOUT and issue.occurrence_count <= 2:
        can_auto.append("低频超时，可自动重试")
    if issue.priority == Priority.P3:
        can_auto.append("低优先级，可自动处理")
    if issue.anomaly_type == AnomalyType.NETWORK and issue.occurrence_count <= 2:
        can_auto.append("低频网络抖动，可自动恢复")

    if can_auto and not reasons:
        return False, "可自动处理: " + "; ".join(can_auto)

    if reasons:
        return True, "; ".join(reasons)

    return False, "低影响问题，可自动处理"
