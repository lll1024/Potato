import {expect, test} from '@playwright/test';

test('集中查看偏好空状态及来源，阅读不会发起提问', async ({page}) => {
  let preferences: unknown[] = [];
  const writes: string[] = [];
  await page.addInitScript(() => Object.defineProperty(window, 'EventSource', {value: class extends EventTarget {close() {}}}));
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() !== 'GET') writes.push(path);
    if (path === '/api/preferences') await route.fulfill({json: {schema_version: 1, preferences, processing: []}});
    else if (path === '/api/snapshot') await route.fulfill({json: {schema_version: 1, cursor: 0, stream_id: 'test', active_turn_id: null, accepting: true}});
    else await route.fulfill({json: {schema_version: 1, sessions: [], next_cursor: null}});
  });
  await page.goto('/');
  await page.getByRole('button', {name: '旅行者偏好', exact: true}).click();
  await expect(page.getByRole('heading', {name: '旅行者偏好', exact: true})).toBeVisible();
  await expect(page.getByText('暂无已保存的偏好', {exact: true})).toBeVisible();
  await expect(page.getByText('删除聊天会保留已保存的偏好，聊天与偏好需要分别管理。')).toBeVisible();
  await page.screenshot({path: test.info().outputPath('preferences-desktop-empty.png')});
  preferences = [{id: 'diet-1', category: 'diet', content: '不吃辣', source_turn_id: 'turn-1', source_input: '我一直不吃辣', updated_at: '2026-10-10T04:00:00Z', input_order: 1, version: 1}];
  await page.getByRole('button', {name: '刷新偏好'}).click();
  await expect(page.getByRole('heading', {name: '饮食', exact: true})).toBeVisible();
  await expect(page.getByText('不吃辣', {exact: true})).toBeVisible();
  await expect(page.getByText('我一直不吃辣', {exact: true})).toBeVisible();
  await expect(page.getByText('暂无已保存的偏好', {exact: true})).toHaveCount(0);
  await page.setViewportSize({width: 390, height: 844});
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
  await page.screenshot({path: test.info().outputPath('preferences-mobile-saved.png')});
  expect(writes).toEqual([]);
  await page.getByRole('button', {name: '返回对话'}).click();
  await expect(page.getByRole('textbox', {name: '旅行需求'})).toBeVisible();
});
