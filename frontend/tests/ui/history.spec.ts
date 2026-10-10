import { expect, test, type Page } from '@playwright/test';

type Session = { session_id: string; title: string; updated_at: string; status: string; reason: null; tool_error_count: number };
const session = (id: string, title: string, status = 'completed'): Session => ({
  session_id: id, title, updated_at: '2026-10-09T02:00:00Z', status, reason: null, tool_error_count: 0,
});

async function history(page: Page, initial: Session[]) {
  let sessions = [...initial];
  const writes: string[] = [];
  await page.addInitScript(() => {
    Object.defineProperty(window, 'EventSource', { value: class extends EventTarget { close() {} } });
  });
  await page.route('**/api/**', async route => {
    const request = route.request();
    const url = new URL(request.url());
    const id = url.pathname.split('/').at(-1);
    const state = { active_turn_id: null, active_session_id: null, accepting: true, stopping: false,
      map_paused: false, map_pause_reason: null, service_status: 'available' };
    if (url.pathname === '/api/snapshot') {
      await route.fulfill({ json: { schema_version: 1, state, cursor: 0, stream_id: 'ui-test' } });
    } else if (url.pathname === '/api/turns' && request.method() === 'POST') {
      writes.push('send');
      sessions.push(session('accepted', '测试会话'));
      await route.fulfill({ json: { schema_version: 1, submission_id: request.postDataJSON().submission_id, session_id: 'accepted', turn_id: 'turn' } });
    } else if (url.pathname === '/api/sessions') {
      await route.fulfill({ json: { schema_version: 1, sessions, next_cursor: null } });
    } else if (request.method() === 'PATCH') {
      writes.push('rename');
      sessions = sessions.map(item => item.session_id === id ? { ...item, title: request.postDataJSON().title } : item);
      await route.fulfill({ json: { schema_version: 1 } });
    } else if (request.method() === 'DELETE') {
      writes.push('delete');
      sessions = sessions.filter(item => item.session_id !== id);
      await route.fulfill({ json: { schema_version: 1 } });
    } else {
      await route.fulfill({ json: { schema_version: 1, session: { title: sessions.find(item => item.session_id === id)?.title ?? '' }, turns: [], next_before: null, total_turns: 0 } });
    }
  });
  await page.goto('/');
  return writes;
}

test('空历史仅显示灰色的暂无已发送的会话', async ({ page }) => {
  await history(page, []);
  const sidebar = page.getByRole('complementary', { name: '历史会话' });
  await expect(sidebar.getByText('暂无已发送的会话', { exact: true })).toBeVisible();
  await expect(sidebar.getByRole('heading')).toHaveCount(0);
  await expect(sidebar.getByText(/按最近对话活动排序|回看不会发起查询/)).toHaveCount(0);
  await expect(sidebar.getByText('暂无已发送的会话', { exact: true })).toHaveCSS('color', 'rgb(100, 106, 120)');
  await page.screenshot({ path: test.info().outputPath('desktop-empty.png') });
});

test('卡片悬停或键盘聚焦显示三点，菜单可用键盘与外部点击关闭', async ({ page }) => {
  const writes = await history(page, [session('hangzhou', '杭州行程')]);
  const trigger = page.getByRole('button', { name: '会话操作“杭州行程”' });
  await expect(trigger).toHaveCSS('opacity', '0');
  await page.getByRole('button', { name: /杭州行程.*已完成/ }).hover();
  await expect(trigger).toHaveCSS('opacity', '1');
  await page.mouse.move(700, 100);
  await trigger.focus();
  await expect(trigger).toHaveCSS('opacity', '1');
  await trigger.press('ArrowDown');
  const rename = page.getByRole('menuitem', { name: '重命名', exact: true });
  const remove = page.getByRole('menuitem', { name: '删除会话', exact: true });
  await expect(rename).toBeFocused();
  await rename.press('ArrowDown');
  await expect(remove).toBeFocused();
  await remove.press('Escape');
  await expect(page.getByRole('menu')).toBeHidden();
  await expect(trigger).toBeFocused();
  await trigger.click();
  await expect(rename).toBeVisible();
  await page.screenshot({ path: test.info().outputPath('desktop-menu.png') });
  await page.getByRole('textbox', { name: '旅行需求' }).click();
  await expect(page.getByRole('menu')).toBeHidden();
  expect(writes).toEqual([]);
});

test('在会话卡片内重命名，Esc 取消，Enter 保存', async ({ page }) => {
  const writes = await history(page, [session('hangzhou', '杭州行程')]);
  const trigger = page.getByRole('button', { name: '会话操作“杭州行程”' });
  await trigger.focus();
  await trigger.press('Enter');
  await page.getByRole('menuitem', { name: '重命名', exact: true }).click();
  const title = page.getByRole('textbox', { name: '会话标题' });
  await expect(title).toBeFocused();
  await title.fill('取消的标题');
  await title.press('Escape');
  await expect(title).toHaveCount(0);
  await expect(trigger).toBeFocused();
  expect(writes).toEqual([]);
  await trigger.press('Enter');
  await page.getByRole('menuitem', { name: '重命名', exact: true }).click();
  await title.fill('杭州周末');
  await title.press('Enter');
  await expect(page.getByRole('button', { name: /杭州周末.*已完成/ })).toBeVisible();
  await expect(title).toHaveCount(0);
  expect(writes).toEqual(['rename']);
});

test('删除需要确认，取消返回三点按钮，运行中的会话不能删除', async ({ page }) => {
  const writes = await history(page, [session('hangzhou', '杭州行程'), session('busy', '执行中行程', 'running')]);
  const trigger = page.getByRole('button', { name: '会话操作“杭州行程”' });
  await trigger.focus();
  await trigger.press('Enter');
  await page.getByRole('menuitem', { name: '删除会话', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '删除会话？' });
  await expect(dialog.getByRole('button', { name: '取消', exact: true })).toBeFocused();
  await dialog.press('Escape');
  await expect(dialog).toBeHidden();
  await expect(trigger).toBeFocused();
  await trigger.press('Enter');
  await page.getByRole('menuitem', { name: '删除会话', exact: true }).click();
  await dialog.getByRole('button', { name: '取消', exact: true }).click();
  await expect(dialog).toBeHidden();
  await expect(trigger).toBeFocused();
  expect(writes).toEqual([]);
  await trigger.press('Enter');
  await page.getByRole('menuitem', { name: '删除会话', exact: true }).click();
  await dialog.getByRole('button', { name: '确认删除' }).click();
  await expect(trigger).toHaveCount(0);
  await expect(dialog).toBeHidden();
  await expect(page.getByRole('complementary', { name: '历史会话' })).toBeFocused();
  expect(writes).toEqual(['delete']);
  const busy = page.getByRole('button', { name: '会话操作“执行中行程”' });
  await busy.focus();
  await busy.press('Enter');
  await expect(page.getByRole('menuitem', { name: '删除会话', exact: true })).toBeDisabled();
});

test('输入框一行起步，多行自动增高，切换保留草稿并恢复对应高度', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await history(page, [session('hangzhou', '杭州行程')]);
  const input = page.getByRole('textbox', { name: '旅行需求' });
  const initial = (await input.boundingBox())!.height;
  expect(initial).toBeLessThanOrEqual(40);
  await input.fill('杭州\n西湖\n餐饮\n交通');
  await expect.poll(async () => (await input.boundingBox())!.height).toBeGreaterThan(initial + 40);
  await page.getByRole('button', { name: /杭州行程.*已完成/ }).click();
  await expect(input).toHaveValue('');
  await expect.poll(async () => (await input.boundingBox())!.height).toBeLessThanOrEqual(40);
  await page.getByRole('button', { name: '新会话', exact: true }).click();
  await expect(input).toHaveValue('杭州\n西湖\n餐饮\n交通');
  await expect.poll(async () => (await input.boundingBox())!.height).toBeGreaterThan(initial + 40);
  await input.fill('');
  await expect.poll(async () => (await input.boundingBox())!.height).toBeLessThanOrEqual(40);
  await input.fill('旅行需求\n'.repeat(40));
  await expect.poll(async () => (await input.boundingBox())!.height).toBeLessThanOrEqual(160);
  expect(await input.evaluate(element => element.scrollHeight)).toBeGreaterThan(160);
  await page.getByRole('button', { name: /^发送/ }).click();
  await expect(input).toHaveValue('');
  await expect.poll(async () => (await input.boundingBox())!.height).toBeLessThanOrEqual(40);
  expect(errors).toEqual([]);
});

test('触屏始终显示三点，菜单关闭后仍能继续操作侧栏', async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
  const page = await context.newPage();
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    await history(page, [session('hangzhou', '杭州行程')]);
    const navigation = page.getByRole('button', { name: '打开会话导航' });
    if (await navigation.count()) await navigation.tap();
    const trigger = page.getByRole('button', { name: '会话操作“杭州行程”' });
    await expect(trigger).toHaveCSS('opacity', '1');
    await trigger.tap();
    const rename = page.getByRole('menuitem', { name: '重命名', exact: true });
    await expect(rename).toBeVisible();
    await page.screenshot({ path: test.info().outputPath('mobile-menu.png') });
    await rename.press('Escape');
    await expect(page.getByRole('menu')).toBeHidden();
    await expect(trigger).toBeVisible();
    await trigger.tap();
    await rename.tap();
    await page.getByRole('textbox', { name: '会话标题' }).fill('手机会话');
    await page.getByRole('button', { name: '保存标题' }).tap();
    await expect(page.getByRole('button', { name: /手机会话.*已完成/ })).toBeVisible();
    const closeNavigation = page.getByRole('button', { name: '收起会话导航' });
    if (await closeNavigation.count()) await closeNavigation.tap();
    await page.screenshot({ path: test.info().outputPath('mobile-input.png') });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    expect(errors).toEqual([]);
  } finally { await context.close(); }
});
