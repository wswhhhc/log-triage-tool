from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class LogLevel(Enum):
    DEBUG = "debug"
    INFO = "info"
    WARN = "warn"
    ERROR = "error"
    FATAL = "fatal"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


class AnomalyType(Enum):
    TIMEOUT = "TIMEOUT"
    AUTH = "AUTH"
    DATABASE = "DATABASE"
    NETWORK = "NETWORK"
    VALIDATION = "VALIDATION"
    RESOURCE_LIMIT = "RESOURCE_LIMIT"
    DATA_QUALITY = "DATA_QUALITY"
    UNKNOWN = "UNKNOWN"


class Priority(Enum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class IssueStatus(Enum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    MITIGATED = "mitigated"
    RESOLVED = "resolved"
    IGNORED = "ignored"


@dataclass
class LogEntry:
    """标准化后的日志条目"""
    id: str
    timestamp: Optional[datetime]
    source: str
    level: LogLevel
    message: str
    job_name: Optional[str] = None
    trace_id: Optional[str] = None
    raw: dict = field(default_factory=dict)
    is_dirty: bool = False
    dirty_reason: Optional[str] = None
    is_anomaly: bool = False


@dataclass
class AnomalyIssue:
    """合并后的异常问题"""
    id: str
    fingerprint: str
    anomaly_type: AnomalyType
    priority: Optional[Priority]
    source: str
    message_template: str
    related_log_ids: list
    first_seen: datetime
    last_seen: datetime
    occurrence_count: int
    status: IssueStatus = IssueStatus.OPEN
    needs_human: bool = False
    human_reason: Optional[str] = None
    recommendation: Optional[str] = None
    priority_reason: Optional[str] = None


@dataclass
class Stats:
    """统计信息"""
    total_logs: int = 0
    valid_logs: int = 0
    dirty_logs: int = 0
    anomaly_logs: int = 0
    merged_issues: int = 0
    by_priority: dict = field(default_factory=dict)
    by_type: dict = field(default_factory=dict)
    needs_human_count: int = 0
    auto_fixable_count: int = 0
    by_source: dict = field(default_factory=dict)
