#!/usr/bin/env python3
"""Synthetic retrieval and reply formatting in a disposable directory. No model/QQ."""
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'cie-qq-ops'), str(ROOT / 'qq-support-bot')]
from retrieval import create_index, retrieve
from support_reply import render_answer, validate_answer


def main():
    with tempfile.TemporaryDirectory(prefix='qq-bot-demo-') as directory:
        index = Path(directory) / 'demo.sqlite'
        quote = '这是专为离线演示编写的 K1 示例教程，不表示任何硬件兼容承诺。'
        create_index(index, [{'id': 'synthetic', 'path': 'demo/k1.md', 'title': 'K1 离线演示', 'body': '# 离线演示\n' + quote,
            'page_url': 'https://example.invalid/demo', 'source_url': '', 'source_kind': 'official'}], datetime.now(timezone.utc).isoformat())
        sources = retrieve('K1 离线演示', index)['results']
        assert sources and quote in sources[0]['content']
        answer = validate_answer({'status': 'answered', 'conclusion': {'text': '本地检索与短答排版演示成功。', 'evidence': [{'source_id': 0, 'quote': quote}]}, 'points': [], 'follow_up': ''}, sources)
        print(render_answer(answer, sources))
        print(json.dumps({'demo': 'passed', 'fixture': 'synthetic', 'model_calls': 0, 'qq_sent': False, 'persistent_database_created': False}, ensure_ascii=False))


if __name__ == '__main__':
    main()
