import {expect, test} from '@playwright/test';
import {spawn} from 'node:child_process';
import {mkdtemp, writeFile, rename, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {resolve, join} from 'node:path';

test('正式页面区分闲置等待、提取、有限重试和失败，已有偏好及普通回答仍可用', async ({page}) => {
  const root = resolve('..');
  const directory = await mkdtemp(join(tmpdir(), 'travel-preferences-ui-'));
  const clock = join(directory, 'clock');
  const release = join(directory, 'release');
  await writeFile(clock, '10000');
  const server = spawn(resolve(root, '.venv/bin/python'), [resolve(root, 'tests/preference_browser_server.py'), clock, release], {cwd: root});
  let output = '';
  server.stdout.on('data', chunk => {output += chunk.toString();});
  server.stderr.on('data', chunk => {output += chunk.toString();});
  const closed = new Promise<void>(done => server.once('close', () => done()));
  const advance = async (now: number) => {
    await writeFile(clock + '.next', String(now));
    await rename(clock + '.next', clock);
  };
  try {
    await expect.poll(() => output, {timeout: 15000}).toContain('READY http');
    const url = output.match(/READY (http:\/\/[^\s]+)/)![1];
    await page.goto(url);
    await page.getByRole('button', {name: '旅行者偏好', exact: true}).click();
    await expect(page.getByText('不吃辣', {exact: true})).toBeVisible();
    await expect(page.getByText('1 轮正在等待会话闲置。', {exact: true})).toBeVisible();
    await advance(17200);
    await expect.poll(async () => (await (await page.request.get(url + '/api/preferences')).json()).processing[0].status).toBe('processing');
    await page.getByRole('button', {name: '刷新偏好'}).click();
    await expect(page.getByText('1 轮正在提取偏好。', {exact: true})).toBeVisible();
    await writeFile(release, 'release');
    await expect.poll(async () => (await (await page.request.get(url + '/api/preferences')).json()).processing[0].status).toBe('pending');
    await page.getByRole('button', {name: '刷新偏好'}).click();
    await expect(page.getByText('1 轮将等待后重试。', {exact: true})).toBeVisible();
    await advance(17230);
    await expect.poll(async () => (await (await page.request.get(url + '/api/preferences')).json()).processing[0].attempts).toBe(2);
    await advance(17350);
    await expect.poll(async () => (await (await page.request.get(url + '/api/preferences')).json()).processing[0].status).toBe('failed');
    await page.getByRole('button', {name: '刷新偏好'}).focus();
    await page.keyboard.press('Enter');
    await expect(page.getByText('有 1 轮偏好提取未成功，已有偏好仍然保留。', {exact: true})).toBeVisible();
    await expect(page.getByText('已达到自动重试上限，尚未保存。可以在对话中重新表达需要保存的长期喜好。', {exact: true})).toBeVisible();
    await expect(page.getByText('喜欢博物馆', {exact: true})).toHaveCount(0);
    await page.setViewportSize({width: 1280, height: 900});
    await page.screenshot({path: test.info().outputPath('preferences-failure-desktop.png')});
    await page.setViewportSize({width: 390, height: 844});
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({path: test.info().outputPath('preferences-failure-mobile.png')});
    await page.getByRole('button', {name: '返回对话'}).click();
    await page.getByRole('textbox', {name: '旅行需求'}).fill('继续规划餐厅');
    await page.getByRole('button', {name: /^发送/}).click();
    await expect(page.getByText('可以继续规划。', {exact: true}).last()).toBeVisible();
    await page.getByRole('button', {name: '旅行者偏好', exact: true}).click();
    await expect(page.getByText('不吃辣', {exact: true})).toBeVisible();
    await page.reload();
    await page.getByRole('button', {name: '旅行者偏好', exact: true}).click();
    await expect(page.getByText('有 1 轮偏好提取未成功，已有偏好仍然保留。', {exact: true})).toBeVisible();
  } finally {
    await page.close();
    server.kill('SIGTERM');
    await closed;
    await rm(directory, {recursive: true, force: true});
  }
  expect(output).toContain('COUNTS {"failed_extract": 3}');
});
