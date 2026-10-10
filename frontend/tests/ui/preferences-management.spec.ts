import {expect, test} from '@playwright/test';

test('键盘编辑、冲突刷新、真实失败反馈及删除空态', async ({page}) => {
  const original = {id: 'diet-1', category: 'diet', content: '不吃辣', source_turn_id: 'turn-1', source_input: '我一直不吃辣', updated_at: '2026-10-10T04:00:00Z', input_order: 1, version: 1};
  let preferences = [original];
  let conflict = true;
  let deletionFailure = true;
  const writes: {method: string; version: number; content?: string}[] = [];
  await page.addInitScript(() => Object.defineProperty(window, 'EventSource', {value: class extends EventTarget {close() {}}}));
  await page.route('**/api/**', async route => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname.startsWith('/api/preferences/')) {
      if (request.method() === 'PATCH') {
        const update = request.postDataJSON();
        writes.push({method: 'PATCH', ...update});
        if (conflict) {
          conflict = false;
          preferences = [{...original, content: '少吃辣', version: 2}];
          await route.fulfill({status: 409, json: {detail: {message: '该偏好已更新，请刷新后重新修改。'}}});
          return;
        }
        preferences = [{...preferences[0], ...update, version: 3}];
      } else {
        writes.push({method: 'DELETE', version: Number(url.searchParams.get('version'))});
        if (deletionFailure) {
          deletionFailure = false;
          await route.fulfill({status: 503, json: {detail: {message: '存储暂时不可用，请重试。'}}});
          return;
        }
        preferences = [];
      }
      await route.fulfill({json: {schema_version: 1, preferences, processing: []}});
    } else if (url.pathname === '/api/preferences') await route.fulfill({json: {schema_version: 1, preferences, processing: []}});
    else if (url.pathname === '/api/snapshot') await route.fulfill({json: {schema_version: 1, cursor: 0, stream_id: 'test', active_turn_id: null, accepting: true}});
    else await route.fulfill({json: {schema_version: 1, sessions: [], next_cursor: null}});
  });
  await page.goto('/');
  await page.getByRole('button', {name: '旅行者偏好', exact: true}).click();
  await page.getByRole('button', {name: '编辑偏好：不吃辣'}).focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('textbox', {name: '偏好内容'})).toBeFocused();
  await page.getByRole('textbox', {name: '偏好内容'}).fill('喜欢清淡口味');
  await page.getByRole('button', {name: '保存修改'}).click();
  await expect(page.getByRole('alert')).toContainText('该偏好已更新，请刷新后重新修改。');
  await expect(page.getByRole('textbox', {name: '偏好内容'})).toHaveValue('喜欢清淡口味');
  await page.getByRole('button', {name: '刷新偏好'}).click();
  await expect(page.getByText('少吃辣', {exact: true})).toBeVisible();
  await page.getByRole('button', {name: '编辑偏好：少吃辣'}).click();
  await page.getByRole('textbox', {name: '偏好内容'}).fill('喜欢清淡口味');
  await page.getByRole('button', {name: '保存修改'}).click();
  await expect(page.getByText('喜欢清淡口味', {exact: true})).toBeVisible();
  await expect(page.getByRole('button', {name: '刷新偏好'})).toBeFocused();
  await page.screenshot({path: test.info().outputPath('preferences-managed-desktop.png')});
  await page.getByRole('button', {name: '返回对话'}).click();
  await page.getByRole('button', {name: '旅行者偏好', exact: true}).click();
  await expect(page.getByText('喜欢清淡口味', {exact: true})).toBeVisible();
  await page.setViewportSize({width: 390, height: 844});
  await page.getByRole('button', {name: '删除偏好：喜欢清淡口味'}).click();
  await expect(page.getByText('删除只影响长期偏好，原聊天仍保留。')).toBeVisible();
  await page.getByRole('button', {name: '确认删除偏好'}).click();
  await expect(page.getByRole('alert')).toContainText('存储暂时不可用，请重试。');
  await expect(page.getByText('喜欢清淡口味', {exact: true})).toBeVisible();
  await page.getByRole('button', {name: '确认删除偏好'}).click();
  await expect(page.getByText('暂无已保存的偏好', {exact: true})).toBeVisible();
  await expect(page.getByRole('button', {name: '刷新偏好'})).toBeFocused();
  await page.screenshot({path: test.info().outputPath('preferences-managed-mobile-empty.png')});
  expect(writes).toEqual([
    {method: 'PATCH', version: 1, content: '喜欢清淡口味'},
    {method: 'PATCH', version: 2, content: '喜欢清淡口味'},
    {method: 'DELETE', version: 3}, {method: 'DELETE', version: 3},
  ]);
});
