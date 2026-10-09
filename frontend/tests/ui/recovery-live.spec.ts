import { expect, test } from '@playwright/test';
import { spawn } from 'node:child_process';
import { resolve } from 'node:path';

for (const width of [1280, 390]) {
  test(`正式页面 ${width}px：失败、摘要、轨迹与新轮次重试贯通`, async ({page}) => {
    const root = resolve('..');
    const server = spawn(resolve(root, '.venv/bin/python'), [resolve(root, 'tests/exception_browser_server.py')], {cwd: root});
    let output = '';
    server.stdout.on('data', chunk => {output += chunk.toString();});
    server.stderr.on('data', chunk => {output += chunk.toString();});
    const closed = new Promise<void>(done => server.once('close', () => done()));
    try {
      await expect.poll(() => output, {timeout: 15000}).toContain('READY http');
      const url = output.match(/READY (http:\/\/[^\s]+)/)![1];
      await page.setViewportSize({width, height: 900});
      await page.goto(url);
      const input = page.getByRole('textbox', {name: '旅行需求'});
      const original = '春节正月初一到初三去三亚';
      await input.fill(original);
      await page.getByRole('button', {name: /^发送/}).click();
      await expect(page.getByText(/这次请求未能完成/)).toBeVisible();
      await expect(page.getByText(/你的旅行需求已保留/)).toBeVisible();
      await expect(page.getByText(/MODEL_ID|maps_search_detail|maps_weather/)).toHaveCount(0);
      await expect(page.getByText('城市：三亚', {exact: true}).first()).not.toBeVisible();
      const summary = page.getByText('查看已有资料', {exact: true});
      await summary.focus(); await page.keyboard.press('Enter');
      await expect(page.getByText('预报日期：2026-10-09', {exact: true})).toBeVisible();
      await expect(page.getByText('预报日期：2026-10-12', {exact: true})).toBeVisible();
      await expect(page.getByText(/尚无法确认/)).toBeVisible();
      await expect(page.getByText(/常规开放时间不保证春节/)).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await page.screenshot({path: test.info().outputPath(`recovery-${width}.png`)});
      const sourceButton = page.getByRole('button', {name: '查看天气资料原始记录'});
      await sourceButton.evaluate(element => element.scrollIntoView({block: 'center'}));
      await sourceButton.focus();
      const dateAnchor = page.getByText('预报日期：2026-10-12', {exact: true});
      const readingPosition = (await dateAnchor.boundingBox())!.y + await page.evaluate(() => scrollY);
      await sourceButton.click();
      await expect(page.getByRole('button', {name: '复制完整脱敏内容'})).toBeVisible();
      await expect(page.getByRole('heading', {name: /工具 2.*maps_weather/})).toBeVisible();
      await page.getByRole('button', {name: '对话', exact: true}).click();
      await expect(dateAnchor).toBeVisible();
      await expect.poll(async () => Math.abs((await dateAnchor.boundingBox())!.y + await page.evaluate(() => scrollY) - readingPosition)).toBeLessThan(2);
      await expect(page.getByRole('button', {name: '回到最新', exact: true})).toBeVisible();
      const saved = await page.request.get(url + '/api/sessions');
      const sessionId = (await saved.json()).sessions[0].session_id;
      const before = await (await page.request.get(url + '/api/sessions/' + sessionId)).json();
      expect(before.requests).toHaveLength(2);
      expect(before.tool_calls).toHaveLength(2);
      await input.fill(original); // 即使草稿与原需求相同，重新尝试也不能清空它。
      await page.getByRole('button', {name: '重新尝试', exact: true}).evaluate(element => {
        (element as HTMLButtonElement).click(); (element as HTMLButtonElement).click();
      });
      await expect(page.getByText('请确认春节年份；已查询地点，旅行行程仍待安排。', {exact: true})).toBeVisible();
      await expect(input).toHaveValue(original);
      const after = await (await page.request.get(url + '/api/sessions/' + sessionId)).json();
      expect(after.turns).toHaveLength(2);
      expect(after.turns[0]).toEqual(before.turns[0]);
      expect(after.turns[1].input).toBe(original);
      expect(after.requests).toHaveLength(4);
      expect(after.tool_calls).toHaveLength(3);
      await page.reload();
      const reopened = await (await page.request.get(url + '/api/sessions/' + sessionId)).json();
      expect(reopened.turns).toEqual(after.turns);
    } finally {
      await page.close();
      server.kill('SIGTERM');
      await closed;
    }
    expect(output).toContain('COUNTS {"model": 4, "map": 3}');
  });
}
