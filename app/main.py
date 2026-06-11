import os

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.classifier import classify, is_anomaly
from app.deduplicator import merge_anomalies
from app.models import IssueStatus
from app.normalizer import normalize
from app.parser import parse_jsonl
from app.prioritizer import calculate_priority
from app.recommender import get_recommendation, need_human
from app.storage import (clear_all, get_all_issues, get_stats, init_db,
                         save_issues, save_logs, update_issue_status)

app = FastAPI(title="日志异常分诊工具")


@app.on_event("startup")
def startup():
    init_db()
    data_path = "data/sample_logs.jsonl"
    if os.path.exists(data_path):
        process_logs(data_path)


def process_logs(file_path: str):
    """完整的处理流水线"""
    # 0. 清空旧数据
    clear_all()

    # 1. 解析
    raw_logs, dirty = parse_jsonl(file_path)

    # 2. 标准化
    entries = [normalize(raw, i) for i, raw in enumerate(raw_logs)]

    # 将解析阶段的脏数据也记录为脏日志条目（用于统计）
    parse_dirty_logs = []
    for d in dirty:
        entry = normalize({}, -d["line_number"])
        entry.is_dirty = True
        entry.dirty_reason = d["reason"]
        entry.raw = d
        entry.id = f"parse_dirty_{d['line_number']}"
        entry.source = "unknown"
        entry.message = f"parse dirty(line {d['line_number']}): {d['raw_content'][:100]}"
        parse_dirty_logs.append(entry)

    # 标记异常日志
    for e in entries:
        e.is_anomaly = is_anomaly(e)

    all_entries = entries + parse_dirty_logs

    # 3. 保存全部日志
    save_logs(all_entries)

    # 4. 筛选异常
    anomalies = [e for e in entries if is_anomaly(e)]

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

    # 8. 保存
    save_issues(issues)


@app.get("/api/issues")
def list_issues():
    """异常列表 API"""
    return {"issues": get_all_issues()}


@app.get("/api/issues/{issue_id}")
def get_issue(issue_id: str):
    """异常详情 API"""
    issues = get_all_issues()
    for issue in issues:
        if issue["id"] == issue_id:
            return {"issue": issue}
    raise HTTPException(status_code=404, detail="issue not found")


@app.put("/api/issues/{issue_id}/status")
def change_status(issue_id: str, status: str = Query(...)):
    """更新状态 API"""
    try:
        new_status = IssueStatus(status)
        update_issue_status(issue_id, new_status)
        return {"ok": True}
    except ValueError:
        raise HTTPException(status_code=400, detail=f"无效状态: {status}")


@app.get("/api/stats")
def statistics():
    """统计 API"""
    return get_stats()


@app.get("/", response_class=HTMLResponse)
def index():
    """前端页面"""
    with open("static/index.html", "r", encoding="utf-8") as f:
        return f.read()


# 挂载静态文件
app.mount("/static", StaticFiles(directory="static"), name="static")
