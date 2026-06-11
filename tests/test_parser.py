import json
import os
import tempfile

from app.parser import parse_jsonl


def test_parse_valid_jsonl():
    """测试读取合法 JSONL"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False, encoding='utf-8') as f:
        f.write('{"level":"error","message":"test1"}\n')
        f.write('{"level":"info","message":"test2"}\n')
        f.close()

        logs, dirty = parse_jsonl(f.name)
        assert len(logs) == 2
        assert len(dirty) == 0

    os.unlink(f.name)


def test_parse_invalid_json_not_crash():
    """无效 JSON 不导致崩溃"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False, encoding='utf-8') as f:
        f.write('{"level":"error","message":"ok"}\n')
        f.write('这不是JSON\n')
        f.write('{"level":"info","message":"ok2"}\n')
        f.close()

        logs, dirty = parse_jsonl(f.name)
        assert len(logs) == 2
        assert len(dirty) == 1
        assert dirty[0]["reason"] == "无效 JSON"

    os.unlink(f.name)


def test_parse_empty_file():
    """空文件处理"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
        f.close()
        logs, dirty = parse_jsonl(f.name)
        assert len(logs) == 0
        assert len(dirty) == 0

    os.unlink(f.name)


def test_parse_skip_empty_lines():
    """空行跳过不报错"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False, encoding='utf-8') as f:
        f.write('{"level":"error"}\n')
        f.write('\n')
        f.write('{"level":"info"}\n')
        f.close()

        logs, dirty = parse_jsonl(f.name)
        assert len(logs) == 2
        assert len(dirty) == 0

    os.unlink(f.name)
