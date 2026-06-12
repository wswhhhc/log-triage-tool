import logging
import os
import sys
import tempfile

from fastapi import FastAPI, HTTPException, Query, UploadFile, File
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.classifier import classify, is_anomaly
from app.deduplicator import merge_anomalies
from app.models import IssueStatus
from app.normalizer import normalize
from app.parser import parse_jsonl
from app.prioritizer import calculate_priority
from app.recommender import get_recommendation, need_human
from app.storage import (clear_all, get_all_issues, get_stats, get_conn,
                         init_db, save_issues, save_logs, update_issue_status,
                         _safe_json, _safe_timestamp)

logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                    format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="日志异常分诊工具")

# 最大上传字节数
MAX_UPLOAD_SIZE = 200 * 1024 * 1024  # 200 MB


@app.on_event("startup")
def startup():
    init_db()
    data_path = "data/sample_logs.jsonl"
    if os.path.exists(data_path):
        try:
            process_logs(data_path)
        except Exception:
            logger.exception("启动时处理样本数据失败")


def _run_pipeline(raw_logs, *, clear_existing=False):
    """
    核心处理流水线（单事务保护）

    步骤：标准化 → 标记异常 → 保存日志 → 筛选异常 → 分类 → 合并 → 优先级/建议
    """
    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute("BEGIN")

        if clear_existing:
            c.execute("DELETE FROM logs")
            c.execute("DELETE FROM issues")

        # 1. 标准化
        entries = [normalize(raw, i) for i, raw in enumerate(raw_logs)]

        # 2. 标记异常
        for e in entries:
            e.is_anomaly = is_anomaly(e)

        # 3. 保存全部日志
        for log in entries:
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
                    int(log.is_anomaly),
                ),
            )

        # 4. 筛选异常（直接用已设置的 flag）
        anomalies = [e for e in entries if e.is_anomaly]

        # 5. 分类
        classified = {e.id: classify(e) for e in anomalies}

        # 6. 合并
        issues = merge_anomalies(anomalies, classified)

        # 7. 优先级 + 建议 + 人工判断
        for issue in issues:
            priority, score, reason = calculate_priority(issue)
            issue.priority = priority
            issue.priority_reason = reason
            issue.recommendation = get_recommendation(issue)
            nh, nh_reason = need_human(issue)
            issue.needs_human = nh
            issue.human_reason = nh_reason

        # 8. 保存异常问题
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
        logger.info("流水线完成: %d 条日志, %d 个异常问题", len(entries), len(issues))
        return issues

    except Exception:
        conn.rollback()
        logger.exception("流水线处理失败，已回滚")
        raise
    finally:
        conn.close()


def _save_parse_dirty(dirty_records: list):
    """将解析阶段的脏数据写入 logs 表（仅用于统计，单独事务）"""
    if not dirty_records:
        return
    conn = get_conn()
    try:
        c = conn.cursor()
        for d in dirty_records:
            line_num = d.get("line_number", 0)
            raw_preview = d.get("raw_content", "")[:100]
            reason = d.get("reason", "未知")
            c.execute(
                "INSERT OR REPLACE INTO logs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    f"parse_dirty_{line_num}",
                    None,
                    "unknown",
                    "unknown",
                    f"parse dirty(line {line_num}): {raw_preview}",
                    None,
                    None,
                    "{}",
                    1,
                    reason,
                    1,
                ),
            )
        conn.commit()
        logger.info("已记录 %d 条解析脏数据", len(dirty_records))
    except Exception:
        conn.rollback()
        logger.exception("写入解析脏数据失败")
    finally:
        conn.close()


def process_logs(file_path: str):
    """
    完整的处理流水线（带事务保护，失败时可回滚）

    步骤:
      0. 解析 JSONL → 有效日志 + 脏数据
      1. 核心流水线（事务包裹，自动清空旧数据）
      2. 写入解析阶段的脏数据（独立事务，不参与异常归并）
    """
    raw_logs, parse_dirty = parse_jsonl(file_path)

    issues = _run_pipeline(raw_logs, clear_existing=True)
    _save_parse_dirty(parse_dirty)

    return issues


@app.get("/api/issues")
def list_issues():
    return {"issues": get_all_issues()}


@app.get("/api/issues/{issue_id}")
def get_issue(issue_id: str):
    issues = get_all_issues()
    for issue in issues:
        if issue["id"] == issue_id:
            return {"issue": issue}
    raise HTTPException(status_code=404, detail="issue not found")


@app.put("/api/issues/{issue_id}/status")
def change_status(issue_id: str, status: str = Query(...)):
    try:
        new_status = IssueStatus(status)
        update_issue_status(issue_id, new_status)
        return {"ok": True}
    except ValueError:
        raise HTTPException(status_code=400, detail=f"无效状态: {status}")


@app.get("/api/stats")
def statistics():
    return get_stats()


@app.post("/api/upload")
async def upload_logs(file: UploadFile = File(...)):
    """上传日志文件并重新处理（带安全校验）"""
    # 1. 文件名校验
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名不能为空")
    if not file.filename.endswith(('.jsonl', '.json', '.log')):
        raise HTTPException(status_code=400, detail="仅支持 .jsonl / .json / .log 文件")

    # 2. 读取内容（带大小限制）
    content = await file.read()
    if len(content) > MAX_UPLOAD_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"文件过大: {len(content) / 1024 / 1024:.1f}MB, 上限 {MAX_UPLOAD_SIZE / 1024 / 1024:.0f}MB",
        )

    # 3. 基本内容校验：至少是 UTF-8，且包含 JSON 结构特征
    try:
        text_content = content.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="文件编码不是 UTF-8，无法解析")

    if not any(ch in text_content for ch in "{}"):
        raise HTTPException(status_code=400, detail="文件中未找到 JSON 内容（缺少 { }）")

    # 4. 保存到临时文件并处理
    with tempfile.NamedTemporaryFile(mode='wb', suffix='.jsonl', delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        process_logs(tmp_path)
    except Exception:
        logger.exception("上传文件处理失败")
        raise HTTPException(status_code=500, detail="服务内部处理失败，请检查日志文件格式后重试")
    finally:
        os.unlink(tmp_path)

    return get_stats()


@app.get("/", response_class=HTMLResponse)
def index():
    with open("static/index.html", "r", encoding="utf-8") as f:
        return f.read()


app.mount("/static", StaticFiles(directory="static"), name="static")
