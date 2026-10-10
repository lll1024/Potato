import {expect, test} from '@playwright/test';
import {spawn} from 'node:child_process';
import {resolve} from 'node:path';

test('后台歧义不改写已结束回答，问题只出现在相关新轮次', async ({page}) => {
  const root = resolve('..');
  const server = spawn(resolve(root, '.venv/bin/python'), [resolve(root, 'tests/preference_clarification_browser_server.py')], {cwd: root});
  let output = '';
  server.stdout.on('data', chunk => {output += chunk.toString();});
  server.stderr.on('data', chunk => {output += chunk.toString();});
  const closed = new Promise<void>(done => server.once('close', () => done()));
  try {
    await expect.poll(() => output, {timeout: 15000}).toContain('READY http');
    const url = output.match(/READY (http:\/\/[^\s]+)/)![1];
    await page.goto(url);
    await page.getByRole('button', {name: /我一直不吃辣.*已完成/}).click();
    await expect(page.getByText('那个现在也可以了', {exact: true})).toBeVisible();
    await expect(page.getByText('可以继续规划。', {exact: true})).toHaveCount(2);
    const question = '你说现在可以吃辣，是只限这次，还是以后长期接受辣味？';
    await expect(page.getByText(question, {exact: true})).toHaveCount(0);
    await page.getByRole('textbox', {name: '旅行需求'}).fill('杭州东站怎么坐地铁到西湖？');
    await page.getByRole('button', {name: /^发送/}).click();
    await expect(page.getByText('可以继续规划。', {exact: true})).toHaveCount(3);
    await expect(page.getByText(question, {exact: true})).toHaveCount(0);
    await page.getByRole('textbox', {name: '旅行需求'}).fill('以后给我推荐餐馆时怎么选口味？');
    await page.getByRole('button', {name: /^发送/}).click();
    await expect(page.getByText(question, {exact: true})).toBeVisible();
    await expect(page.getByText('可以继续规划。', {exact: true})).toHaveCount(3);
    await page.screenshot({path: test.info().outputPath('preferences-clarification.png')});
    await page.getByRole('button', {name: '旅行者偏好', exact: true}).click();
    await expect(page.getByText('不吃辣', {exact: true})).toBeVisible();
  } finally {
    await page.close();
    server.kill('SIGTERM');
    await closed;
  }
});
