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
        assert "无效 JSON" in dirty[0]["reason"]

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


def test_parse_non_dict_json():
    """合法 JSON 但非 dict 结构 → 归为脏数据"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False, encoding='utf-8') as f:
        f.write('"just a string"\n')
        f.write('[1, 2, 3]\n')
        f.write('null\n')
        f.write('{"level":"error","message":"ok"}\n')
        f.close()

        logs, dirty = parse_jsonl(f.name)
        assert len(logs) == 1
        assert logs[0].get("message") == "ok"
        # 前三行都是脏数据
        assert len(dirty) == 3
        d_reasons = [d["reason"] for d in dirty]
        assert all("期望 JSON 对象" in r for r in d_reasons)

    os.unlink(f.name)


def test_parse_file_not_found():
    """文件不存在不崩溃"""
    logs, dirty = parse_jsonl("/tmp/__nonexistent_file__.jsonl")
    assert len(logs) == 0
    assert len(dirty) == 1
    assert "不存在" in dirty[0]["reason"]


def test_parse_very_long_line():
    """超长行被截断并标记"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False, encoding='utf-8') as f:
        # 构造一个超长行
        long_val = "x" * 200_000  # 超过 MAX_LINE_LENGTH 的 100k
        f.write('{"level":"error","message":"%s"}\n' % long_val)
        f.write('{"level":"info","message":"ok"}\n')
        f.close()

        logs, dirty = parse_jsonl(f.name)
        assert len(logs) == 1
        assert len(dirty) == 1
        assert "过长" in dirty[0]["reason"]

    os.unlink(f.name)


def test_parse_max_line_limit():
    """超过最大行数限制时截断"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False, encoding='utf-8') as f:
        for _ in range(1_000_010):
            f.write('{"level":"info","message":"ok"}\n')
        f.close()

        logs, dirty = parse_jsonl(f.name)
        # 最多处理 1_000_000 行
        assert len(logs) <= 1_000_000
        assert any("超过最大行数" in d["reason"] for d in dirty)

    os.unlink(f.name)


def test_parse_bom_first_line():
    """UTF-8 BOM 首行不应导致解析失败"""
    with tempfile.NamedTemporaryFile(mode='wb', suffix='.jsonl', delete=False) as f:
        bom = b'\xef\xbb\xbf'
        f.write(bom + b'{"level":"error","message":"first"}\n')
        f.write(b'{"level":"info","message":"second"}\n')
        f.close()

        logs, dirty = parse_jsonl(f.name)
        assert len(logs) == 2, f"期望 2 条合法日志, 实际 {len(logs)}"
        assert len(dirty) == 0, f"不应有脏数据: {dirty}"

    os.unlink(f.name)
