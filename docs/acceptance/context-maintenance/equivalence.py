"""固定时钟下比较现有公开回归的真实请求载荷与上下文事件。

从仓库根运行，PYTHONPATH=tests；第一个参数是输出 JSON，--before 加载 #67 基准助手循环。
仅省略随机请求身份、时间耗时和持久化载荷身份，完整模型输入继续参与比较。
"""
import copy
from datetime import datetime
import json
import re
import sys
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
if '--before' in sys.argv:
    import importlib.util
    import tempfile
    source = subprocess.check_output(['git', 'show', '7563199:travel_app/agent.py'], text=True)
    baseline = Path(tempfile.gettempdir()) / 'issue68-agent-baseline.py'
    baseline.write_text(source)
    spec = importlib.util.spec_from_file_location('travel_app.agent', baseline)
    module = importlib.util.module_from_spec(spec)
    sys.modules['travel_app.agent'] = module
    spec.loader.exec_module(module)
from travel_app.store import Store

def normalize(records):
    normalized = {}
    for case, events in records.items():
        identities = {}
        def visit(value, field=None):
            if isinstance(value, dict):
                return {key: ('<本次检索时间>' if key == 'retrieved_at' and case.endswith('test_source_body_duplicates_use_references_without_losing_conditions_or_failures') else visit(item, key)) for key, item in value.items()}
            if isinstance(value, list):
                return [visit(item, field) for item in value]
            if isinstance(value, str):
                try:
                    parsed = json.loads(value)
                except ValueError:
                    parsed = None
                if isinstance(parsed, (dict, list)):
                    return {'serialized_json': visit(parsed)}
                def identity(match):
                    return identities.setdefault(match[0], f'<随机身份{len(identities) + 1}>')
                if field in ('payload_id', 'turn_id', 'tool_call_id', 'request_id'):
                    return re.sub(r'^[0-9a-f]{32}$', identity, value)
                if field == 'record_id' and value.startswith('payload:'):
                    return re.sub(r'(?<=payload:)[0-9a-f]{32}', identity, value)
                return value
            return value
        normalized[case] = visit(events)
    return normalized

records = {}
current = None
original = Store.event
class Result(unittest.TextTestResult):
    def startTest(self, test):
        global current
        current = test.id()
        records[current] = []
        super().startTest(test)

def event(self, session_id, turn_id, kind, data, **kwargs):
    if current and (kind.startswith('context.') or kind == 'request.started'):
        value = copy.deepcopy(data)
        for key in ('request_id', 'recovery_of', 'started_at', 'duration_ms'):
            value.pop(key, None)
        if kind == 'request.started':
            value.pop('retry_id', None)
            payload_id = value.pop('input_payload_id')
            value['input'] = json.loads(self.db.execute('SELECT content FROM payloads WHERE payload_id=?', (payload_id,)).fetchone()[0])
        records[current].append([kind, value])
    return original(self, session_id, turn_id, kind, data, **kwargs)

suite = unittest.defaultTestLoader.loadTestsFromNames([
    'test_context_limit_recovery', 'test_context_incremental', 'test_context_organization',
    'test_context_summary.ContextSummaryTests.test_old_history_summary_answers_preserves_original_and_restarts',
])
with patch.object(Store, 'event', event), patch('travel_app.agent.current_time', return_value=datetime.fromisoformat('2026-10-13T12:00:00+08:00')):
    result = unittest.TextTestRunner(verbosity=1, resultclass=Result).run(suite)
with open(sys.argv[1], 'w') as stream:
    json.dump(normalize(records), stream, ensure_ascii=False, sort_keys=True)
sys.exit(not result.wasSuccessful())
