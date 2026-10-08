"""真实正式入口的中断与共享连接释放。"""
import asyncio
import os
from pathlib import Path
import signal
import sys
import tempfile
import unittest

import httpx

from test_recovery import ExternalServices, free_port

ROOT = Path(__file__).resolve().parents[1]


class WebEntrypointTests(unittest.IsolatedAsyncioTestCase):
    async def test_sigint_waits_for_real_call_then_exits_cleanly_and_unlocks_history(self):
        external = ExternalServices()
        external.block = 'model'
        with tempfile.TemporaryDirectory() as directory:
            async with external.serving() as url:
                port = free_port()
                child = await asyncio.create_subprocess_exec(sys.executable,
                    str(ROOT / 'tests/web_main_process.py'), directory, str(port), url,
                    env={**os.environ, 'PYTHONPATH':str(ROOT)},
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
                try:
                    async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}') as client:
                        async def ready():
                            while child.returncode is None:
                                try:
                                    if (await client.get('/api/state')).status_code == 200:
                                        return
                                except httpx.HTTPError:
                                    pass
                                await asyncio.sleep(.02)
                            self.fail((await child.stdout.read()).decode())
                        await asyncio.wait_for(ready(), 10)
                        accepted = (await client.post('/api/turns', json={'input':'正常中断仍保存真实最终回答'})).json()
                        await asyncio.wait_for(external.entered.wait(), 3)
                        child.send_signal(signal.SIGINT)
                        with self.assertRaises(TimeoutError):
                            await asyncio.wait_for(asyncio.shield(child.wait()), 1.3)
                        external.release.set()
                        self.assertEqual(await asyncio.wait_for(child.wait(), 5), 0)
                        output = (await child.stdout.read()).decode()
                        self.assertNotIn('Traceback', output)
                        self.assertNotIn('KeyboardInterrupt', output)
                        self.assertNotIn('test-key', output)
                    # 同目录重新打开，证明锁已释放，已保存终局不恢复为未知。
                    from test_recovery import process
                    async with process(directory, url) as (_, client, _):
                        saved = (await client.get('/api/sessions/' + accepted['session_id'])).json()
                        self.assertEqual(saved['turns'][0]['status'], 'completed')
                        self.assertEqual(saved['turns'][0]['answer'], '完整旅行建议。')
                        self.assertNotIn('turn.recovered', [event['kind'] for event in saved['events']])
                        self.assertEqual(len(external.model_calls), 1)
                finally:
                    external.release.set()
                    if child.returncode is None:
                        child.kill()
                    await child.wait()
