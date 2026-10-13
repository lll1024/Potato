import {expect, test} from '@playwright/test';
import {spawn, type ChildProcessWithoutNullStreams} from 'node:child_process';
import {mkdtemp, rm, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';

const original = '2026-10-18杭州，预算3000元人民币，保留湖滨酒店，未预订。' + '阅读背景资料，不是新要求。'.repeat(310);
async function start(directory: string, mode: string) {
  const server = spawn('../.venv/bin/python', ['../tests/context_browser_server.py', '--data-dir', directory, '--mode', mode], {cwd: process.cwd()});
  let output = '';
  server.stdout.on('data', chunk => {output += chunk.toString();});
  server.stderr.on('data', chunk => {output += chunk.toString();});
  const closed = new Promise<void>(done => server.once('close', () => done()));
  await expect.poll(() => output, {timeout: 15000}).toContain('READY http');
  return {server, closed, url: output.match(/READY (http:\/\/[^\s]+)/)![1]};
}
async function selectHistory(page: import('@playwright/test').Page) {
  const toggle = page.getByRole('button', {name: '打开会话导航'});
  if (await toggle.isVisible()) await toggle.click();
  await page.getByRole('button', {name: /2026-10-18杭州/}).first().click();
}
async function close(server: ChildProcessWithoutNullStreams, closed: Promise<void>, signal: NodeJS.Signals = 'SIGTERM') {
  server.kill(signal); await closed;
}
for (const width of [1440, 390]) {
  test(`真实服务的上下文采用、保存、停止和中断事实 ${width}`, async ({browser}) => {
    test.setTimeout(90000);
    const directory = await mkdtemp(path.join(tmpdir(), 'context-browser-'));
    const page = await browser.newPage({viewport: {width, height: 900}});
    let running = await start(directory, 'success');
    try {
      await page.goto(running.url);
      const input = page.getByRole('textbox', {name: '旅行需求'});
      await input.fill(original); await page.getByRole('button', {name: /^发送/}).click();
      await expect(page.getByText('酒店未预订，预算3000元人民币。', {exact: true})).toBeVisible();
      await input.fill('继续保留原条件'); await page.getByRole('button', {name: /^发送/}).click();
      await page.getByRole('button', {name: '轨迹', exact: true}).click();
      const maintenance = page.locator('.model-retries').filter({hasText: '旅行记录整理'}).last();
      await expect(maintenance.locator('summary')).toContainText('尚未保存供续聊');
      await maintenance.locator('summary').click();
      await expect(maintenance).toContainText('摘要候选已记录');
      await expect(maintenance).toContainText('摘要已用于本轮，尚未保存供续聊');
      const sessions = (await (await page.request.get(running.url + '/api/sessions')).json()).sessions;
      const sessionId = sessions[0].session_id;
      await expect.poll(async () => (await (await page.request.get(running.url + '/api/sessions/' + sessionId)).json()).events.some((e: {kind: string}) => e.kind === 'context.saved'), {timeout: 10000}).toBe(true);
      await expect(maintenance.locator('summary')).toContainText('已保存供续聊');
      await expect(maintenance).toContainText('摘要校验通过');
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await page.screenshot({path: test.info().outputPath(`context-saved-${width}.png`)});
      const saved = await (await page.request.get(running.url + '/api/sessions/' + sessionId)).json();
      expect(saved.turns.at(-1).messages[0].content).toBe(original);
      expect(saved.requests.filter((r: {purpose: string}) => r.purpose === 'context_summary')).toHaveLength(1);
      await page.getByRole('button', {name: '对话', exact: true}).click();
      await expect(page.getByText('继续保留原条件，酒店仍未预订。', {exact: true})).toBeVisible();
      await expect(page.getByText(/travel_conditions|travel_state_summary/)).toHaveCount(0);
      await expect(page.getByRole('button', {name: /手动.*压缩|压缩上下文|整理上下文/})).toHaveCount(0);
      await close(running.server, running.closed);
      running = await start(directory, 'stop');
      await page.goto(running.url); await selectHistory(page);
      const stopped = await (await page.request.post(running.url + '/api/turns', {data: {input: '继续' + '背景资料不是新增要求。'.repeat(350), session_id: sessionId}})).json();
      await expect.poll(async () => (await (await page.request.get(running.url + '/api/sessions/' + sessionId)).json()).requests.some((r: {turn_id: string; purpose: string; status: string}) => r.turn_id === stopped.turn_id && r.purpose === 'context_summary' && r.status === 'running')).toBe(true);
      await page.request.post(running.url + '/api/turns/' + stopped.turn_id + '/stop');
      await page.getByRole('button', {name: '轨迹', exact: true}).click();
      await expect(page.getByRole('status').filter({hasText: '已停止'})).toBeVisible();
      await expect(page.locator('.model-retries').last().locator('summary')).toContainText('摘要未采用');
      await page.locator('.model-retries').last().locator('summary').click();
      await expect(page.locator('.model-retries').last()).toContainText('已停止或来源版本已变化，未采用');
      const stoppedSnapshot = await (await page.request.get(running.url + '/api/sessions/' + sessionId)).json();
      const stoppedRequests = stoppedSnapshot.requests.filter((r: {turn_id: string}) => r.turn_id === stopped.turn_id);
      expect(stoppedRequests).toHaveLength(1);
      expect(stoppedRequests[0].usage_state).toBe('returned');
      expect(stoppedSnapshot.events.some((e: {turn_id: string; kind: string}) => e.turn_id === stopped.turn_id && ['context.adopted', 'context.saved'].includes(e.kind))).toBe(false);
      await page.screenshot({path: test.info().outputPath(`context-stopped-${width}.png`)});
      await close(running.server, running.closed);
      running = await start(directory, 'interrupted');
      const interrupted = await (await page.request.post(running.url + '/api/turns', {data: {input: '继续' + '更多阅读背景。'.repeat(550), session_id: sessionId}})).json();
      await expect.poll(async () => (await (await page.request.get(running.url + '/api/sessions/' + sessionId)).json()).requests.some((r: {turn_id: string; status: string}) => r.turn_id === interrupted.turn_id && r.status === 'running')).toBe(true);
      await close(running.server, running.closed, 'SIGKILL');
      running = await start(directory, 'success');
      await page.goto(running.url); await selectHistory(page); await page.getByRole('button', {name: '轨迹', exact: true}).click();
      await expect(page.locator('.model-retries').last().locator('summary')).toContainText('摘要请求结果未知');
      await page.locator('.model-retries').last().locator('summary').click();
      await expect(page.locator('.model-retries').last()).toContainText('续聊恢复上一有效版本');
      const unknown = await (await page.request.get(running.url + '/api/sessions/' + sessionId)).json();
      const request = unknown.requests.find((r: {turn_id: string}) => r.turn_id === interrupted.turn_id);
      expect(request.status).toBe('running'); expect(request.usage_state).toBe('not_completed'); expect(request.usage).toBeNull();
      await page.getByRole('button', {name: '全部展开', exact: true}).click();
      await expect(page.getByText(/输入：未知/).first()).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await page.locator('.model-retries').last().scrollIntoViewIfNeeded();
      await page.screenshot({path: test.info().outputPath(`context-unknown-${width}.png`)});
      await writeFile(test.info().outputPath(`context-browser-${width}.json`), JSON.stringify({saved, stopped: stoppedSnapshot, interrupted: unknown}, null, 2));
    } finally {
      if (running.server.exitCode === null && running.server.signalCode === null) await close(running.server, running.closed);
      await page.close(); await rm(directory, {recursive: true, force: true});
    }
  });
}
