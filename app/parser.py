import json
import os
from typing import List, Tuple

# 安全限制
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100 MB
MAX_LINES = 1_000_000
MAX_LINE_LENGTH = 100_000  # 单行最大字符数


def parse_jsonl(file_path: str) -> Tuple[List[dict], List[dict]]:
    """
    读取 JSONL 文件（带安全限制）
    返回: (有效日志字典列表, 脏数据列表)
    """
    valid_logs = []
    dirty_data = []

    # 1. 检查文件是否存在
    if not os.path.exists(file_path):
        dirty_data.append({
            "line_number": 0,
            "raw_content": "",
            "reason": "文件不存在"
        })
        return valid_logs, dirty_data

    # 2. 检查文件大小
    file_size = os.path.getsize(file_path)
    if file_size > MAX_FILE_SIZE:
        dirty_data.append({
            "line_number": 0,
            "raw_content": f"<文件过大: {file_size / 1024 / 1024:.1f}MB, 超过限制 {MAX_FILE_SIZE / 1024 / 1024:.0f}MB>",
            "reason": "文件大小超过限制"
        })
        # 仍然尝试处理前 MAX_LINES 行
    elif file_size == 0:
        return valid_logs, dirty_data  # 空文件直接返回

    # 3. 逐行解析
    with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
        for line_num, line in enumerate(f, 1):
            # 行数限制
            if line_num > MAX_LINES:
                dirty_data.append({
                    "line_number": line_num,
                    "raw_content": "<超过最大行数限制，已截断>",
                    "reason": f"超过最大行数限制 {MAX_LINES}"
                })
                break

            line = line.strip()
            if not line:
                continue

            # 单行长度限制
            if len(line) > MAX_LINE_LENGTH:
                dirty_data.append({
                    "line_number": line_num,
                    "raw_content": line[:200],
                    "reason": f"单行过长 ({len(line)} 字符)"
                })
                continue

            try:
                raw = json.loads(line)
                # 校验：必须是 dict
                if not isinstance(raw, dict):
                    dirty_data.append({
                        "line_number": line_num,
                        "raw_content": line[:200],
                        "reason": f"非法结构: 期望 JSON 对象, 实际为 {type(raw).__name__}"
                    })
                    continue
                valid_logs.append(raw)
            except json.JSONDecodeError as e:
                dirty_data.append({
                    "line_number": line_num,
                    "raw_content": line[:200],
                    "reason": f"无效 JSON: {e.msg}"
                })

    return valid_logs, dirty_data
