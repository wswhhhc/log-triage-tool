from app.models import LogEntry, LogLevel, AnomalyType

# 错误等级集合
ERROR_LEVELS = {LogLevel.ERROR, LogLevel.FATAL, LogLevel.CRITICAL}

# 异常关键词（中文+英文）
ANOMALY_KEYWORDS = {
    "timeout", "超时", "timed out", "timed-out",
    "exception", "异常", "error",
    "failed", "失败", "failure",
    "denied", "拒绝", "rejected",
    "unavailable", "不可用", "503",
    "connection refused", "连接被拒绝",
    "aborted", "中止",
}


def is_anomaly(log: LogEntry) -> bool:
    """判断日志是否异常"""
    if log.level in ERROR_LEVELS:
        return True

    msg_lower = log.message.lower()
    for kw in ANOMALY_KEYWORDS:
        if kw.lower() in msg_lower:
            return True

    if log.is_dirty and log.dirty_reason:
        return True

    return False


def classify(log: LogEntry) -> AnomalyType:
    """对异常日志进行分类"""
    msg = log.message.lower() if log.message else ""

    if any(kw in msg for kw in ["timeout", "超时", "timed out", "timed-out"]):
        return AnomalyType.TIMEOUT

    if any(kw in msg for kw in [
        "auth", "认证", "token", "permission", "权限",
        "unauthorized", "未授权", "denied", "forbidden",
    ]):
        return AnomalyType.AUTH

    if any(kw in msg for kw in [
        "database", "数据库", "db", "sql", "mysql", "postgres",
        "mongo", "redis", "连接池", "connection pool",
    ]):
        return AnomalyType.DATABASE

    if any(kw in msg for kw in [
        "network", "网络", "connection refused", "连接失败",
        "dns", "tcp", "socket", "host", "端口",
    ]):
        return AnomalyType.NETWORK

    if any(kw in msg for kw in [
        "validation", "校验", "invalid", "无效",
        "param", "参数", "required", "必填", "format", "格式",
    ]):
        return AnomalyType.VALIDATION

    if any(kw in msg for kw in [
        "limit", "限流", "throttle", "quota", "容量",
        "overload", "过载", "queue", "积压", "full", "已满",
    ]):
        return AnomalyType.RESOURCE_LIMIT

    if log.is_dirty:
        return AnomalyType.DATA_QUALITY

    return AnomalyType.UNKNOWN
