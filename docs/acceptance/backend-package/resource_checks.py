import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

root = Path.cwd()
checks = []
with tempfile.TemporaryDirectory() as temp:
    base = Path(temp)
    project, cwd = base / 'project', base / 'cwd'
    project.mkdir(); cwd.mkdir()
    for entry in ('agent.py', 'web.py'):
        shutil.copy(root / entry, project / entry)
    shutil.copytree(root / 'travel_app', project / 'travel_app', ignore=shutil.ignore_patterns('__pycache__'))
    dist = project / 'frontend' / 'dist'; dist.mkdir(parents=True)
    (dist / 'index.html').write_text('PROJECT_ROOT_STATIC')
    override = base / 'override'; override.mkdir()
    (override / 'index.html').write_text('EXPLICIT_STATIC')
    (project / '.env').write_text('AMAP_MAPS_API_KEY=project-fixture-key\nMODEL_ID=project-fixture-model\n')
    (cwd / '.env').write_text('AMAP_MAPS_API_KEY=wrong-cwd-key\nMODEL_ID=wrong-cwd-model\n')
    env = {'PATH': os.environ['PATH'], 'PYTHONPATH': str(project), 'AMAP_MAPS_API_KEY':'environment-key'}
    code = '''import asyncio, os, runpy, sys
from travel_app import agent
async def run(api_key, *, check_amap=False):
    assert api_key == 'project-fixture-key'
    assert os.environ['MODEL_ID'] == 'project-fixture-model'
    assert check_amap
    return 0
agent.run_cli = run
sys.argv = ['agent.py','--check-amap']
runpy.run_path(sys.argv[0].replace('agent.py',%r), run_name='__main__')
''' % str(project / 'agent.py')
    result = subprocess.run([sys.executable, '-c', code], cwd=cwd, env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    checks.append('根 CLI 启动入口读取项目根 .env 并覆盖环境；--check-amap 不要求模型凭据')
    code = '''import asyncio, os, runpy, sys
from pathlib import Path
from contextlib import asynccontextmanager
import httpx
from travel_app import web
from types import SimpleNamespace
@asynccontextmanager
async def resources():
    yield web.Runtime(None, SimpleNamespace(failure=None), 'fixture')
async def verify():
    for data, static, expected in [('.travel-data',None,'PROJECT_ROOT_STATIC'),('relative-data',%r,'EXPLICIT_STATIC')]:
        app = web.create_app(data, resources=resources, static_dir=static)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                response = await client.get('/')
                assert response.status_code == 200 and response.text == expected
                assert (await client.get('/api/state')).json()['accepting']
        assert (Path.cwd() / data / 'travel.sqlite3').is_file()
asyncio.run(verify())
class Server:
    def __init__(self,config):
        assert config.host == '127.0.0.1' and config.port == 8123 and config.workers == 1
        self.started = True
    def run(self):
        assert os.environ['MODEL_ID'] == 'project-fixture-model'
web.uvicorn.Server = Server
sys.argv = ['web.py','--port','8123','--data-dir','relative-main']
runpy.run_path(%r,run_name='__main__')
''' % (str(override),str(project / 'web.py'))
    result = subprocess.run([sys.executable,'-c',code],cwd=cwd,env=env,text=True,capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    checks.append('Web 项目根 .env、默认静态资源、显式静态覆盖、启动参数有效')
    checks.append('默认 .travel-data 与相对 data-dir 均落于启动工作目录')
    shutil.rmtree(dist)
    for entry, args, expected, phrase in [('agent.py',[],1,'ANTHROPIC_API_KEY'),('web.py',[],1,'npm'),('agent.py',['--unknown'],2,'unrecognized arguments'),('web.py',['--port','invalid'],2,'invalid int value')]:
        result = subprocess.run([sys.executable,str(project / entry),*args],cwd=cwd,env=env,text=True,capture_output=True)
        assert result.returncode == expected and phrase in result.stdout + result.stderr
        assert 'Traceback' not in result.stdout + result.stderr
    checks.append('CLI 与 Web 缺少配置/构建及非法参数诊断、退出码保持')
print(json.dumps({'checks':checks,'passed':len(checks),'external_calls':0},ensure_ascii=False,indent=2))
