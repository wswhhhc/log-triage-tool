import json
import sqlite3
from datetime import datetime
from typing import Dict, List

from app.models import AnomalyIssue, IssueStatus, LogEntry

DB_PATH = "anomaly_triage.db"


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _safe_json(value, default=None) -> str:
    """安全 JSON 序列化，失败时返回 fallback"""
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError, RecursionError):
        return json.dumps(default) if default is not None else "{}"


def _safe_timestamp(dt) -> str:
    """安全的时间戳转字符串"""
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return dt.isoformat()
    try:
        return str(dt)
    except Exception:
        return None


def init_db():
    """初始化数据库（含索引）"""
    conn = get_conn()
    c = conn.cursor()

    # 启用 WAL 模式：允许读写并发，提升大文件处理时的响应速度
    c.execute("PRAGMA journal_mode=WAL")

    c.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id TEXT PRIMARY KEY,
            timestamp TEXT,
            source TEXT,
            level TEXT,
            message TEXT,
            job_name TEXT,
            trace_id TEXT,
            raw TEXT,
            is_dirty INTEGER,
            dirty_reason TEXT,
            is_anomaly INTEGER DEFAULT 0
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS issues (
            id TEXT PRIMARY KEY,
            fingerprint TEXT,
            anomaly_type TEXT,
            priority TEXT,
            source TEXT,
            message_template TEXT,
            related_log_ids TEXT,
            first_seen TEXT,
            last_seen TEXT,
            occurrence_count INTEGER,
            status TEXT DEFAULT 'open',
            needs_human INTEGER DEFAULT 0,
            human_reason TEXT,
            recommendation TEXT,
            priority_reason TEXT
        )
    """)

    # ── 索引（使用 IF NOT EXISTS 以保证幂等） ──
    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_logs_source ON logs(source)",
        "CREATE INDEX IF NOT EXISTS idx_logs_level ON logs(level)",
        "CREATE INDEX IF NOT EXISTS idx_logs_is_dirty ON logs(is_dirty)",
        "CREATE INDEX IF NOT EXISTS idx_logs_is_anomaly ON logs(is_anomaly)",
        "CREATE INDEX IF NOT EXISTS idx_issues_anomaly_type ON issues(anomaly_type)",
        "CREATE INDEX IF NOT EXISTS idx_issues_priority ON issues(priority)",
        "CREATE INDEX IF NOT EXISTS idx_issues_status ON issues(status)",
        "CREATE INDEX IF NOT EXISTS idx_issues_source ON issues(source)",
        "CREATE INDEX IF NOT EXISTS idx_issues_fingerprint ON issues(fingerprint)",
    ]
    for idx_sql in indexes:
        try:
            c.execute(idx_sql)
        except sqlite3.OperationalError:
            pass  # 索引已存在（旧版 SQLite 兼容）

    conn.commit()
    conn.close()


def clear_all():
    """清空所有数据"""
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM logs")
    c.execute("DELETE FROM issues")
    conn.commit()
    conn.close()


def save_logs(logs: List[LogEntry]):
    """保存标准化日志"""
    conn = get_conn()
    c = conn.cursor()
    for log in logs:
        c.execute(
            "INSERT OR REPLACE INTO logs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                log.id,
                _safe_timestamp(log.timestamp),
                log.source,
                log.level.value,
                log.message,
                log.job_name,
                log.trace_id,
                _safe_json(log.raw, {}),
                int(log.is_dirty),
                log.dirty_reason,
                int(log.is_anomaly) if hasattr(log, 'is_anomaly') else 0,
            ),
        )
    conn.commit()
    conn.close()


def save_issues(issues: List[AnomalyIssue]):
    """保存异常问题"""
    conn = get_conn()
    c = conn.cursor()
    for issue in issues:
        c.execute(
            "INSERT OR REPLACE INTO issues VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                issue.id,
                issue.fingerprint,
                issue.anomaly_type.value,
                issue.priority.value if issue.priority else None,
                issue.source,
                issue.message_template,
                _safe_json(issue.related_log_ids, []),
                _safe_timestamp(issue.first_seen),
                _safe_timestamp(issue.last_seen),
                issue.occurrence_count,
                issue.status.value,
                int(issue.needs_human),
                issue.human_reason,
                issue.recommendation,
                issue.priority_reason,
            ),
        )
    conn.commit()
    conn.close()


def update_issue_status(issue_id: str, new_status: IssueStatus):
    """更新异常状态"""
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE issues SET status = ? WHERE id = ?",
              (new_status.value, issue_id))
    conn.commit()
    conn.close()


def get_all_issues() -> List[dict]:
    """获取所有异常问题"""
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM issues ORDER BY priority, occurrence_count DESC")
    rows = c.fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        if isinstance(d.get("related_log_ids"), str):
            try:
                d["related_log_ids"] = json.loads(d["related_log_ids"])
            except (json.JSONDecodeError, TypeError):
                d["related_log_ids"] = []
        if not isinstance(d.get("needs_human"), int):
            d["needs_human"] = int(d["needs_human"])
        result.append(d)
    return result


def get_stats() -> Dict:
    """获取统计信息"""
    conn = get_conn()
    c = conn.cursor()

    total = c.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
    valid = c.execute(
        "SELECT COUNT(*) FROM logs WHERE is_dirty = 0").fetchone()[0]
    dirty = c.execute(
        "SELECT COUNT(*) FROM logs WHERE is_dirty = 1").fetchone()[0]
    anomaly = c.execute(
        "SELECT COUNT(*) FROM logs WHERE is_anomaly = 1"
    ).fetchone()[0]
    merged = c.execute("SELECT COUNT(*) FROM issues").fetchone()[0]

    by_priority = {}
    for row in c.execute(
            "SELECT priority, COUNT(*) FROM issues GROUP BY priority"):
        by_priority[row[0]] = row[1]

    by_type = {}
    for row in c.execute(
            "SELECT anomaly_type, COUNT(*) FROM issues GROUP BY anomaly_type"):
        by_type[row[0]] = row[1]

    human = c.execute(
        "SELECT COUNT(*) FROM issues WHERE needs_human = 1").fetchone()[0]

    by_source = {}
    for row in c.execute(
            "SELECT source, COUNT(*) FROM issues GROUP BY source"):
        by_source[row[0]] = row[1]

    conn.close()

    return {
        "total_logs": total,
        "valid_logs": valid,
        "dirty_logs": dirty,
        "anomaly_logs": anomaly,
        "merged_issues": merged,
        "by_priority": by_priority,
        "by_type": by_type,
        "needs_human_count": human,
        "auto_fixable_count": merged - human,
        "by_source": by_source,
    }
