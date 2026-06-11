import json
from typing import List, Tuple


def parse_jsonl(file_path: str) -> Tuple[List[dict], List[dict]]:
    """
    读取 JSONL 文件
    返回: (有效日志字典列表, 脏数据列表)
    """
    valid_logs = []
    dirty_data = []

    with open(file_path, 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue

            try:
                raw = json.loads(line)
                valid_logs.append(raw)
            except json.JSONDecodeError:
                dirty_data.append({
                    "line_number": line_num,
                    "raw_content": line[:200],
                    "reason": "无效 JSON"
                })

    return valid_logs, dirty_data
