import { expect, test, type Page } from '@playwright/test';

async function conversation(page: Page) {
  const state = {active_turn_id: null as string | null, active_session_id: null as string | null,
    accepting: true, stopping: false, map_paused: false, map_pause_reason: null, service_status: 'available'};
  const sessions = [
    {session_id: 'trip', title: '杭州行程', updated_at: '2026-10-09T02:00:00Z', status: 'completed', reason: null, tool_error_count: 0},
    {session_id: 'old', title: '旧会话', updated_at: '2026-10-08T02:00:00Z', status: 'completed', reason: null, tool_error_count: 0},
  ];
  const turn = {turn_id: 'turn', ordinal: 1, input: '查询西湖', status: 'running', reason: null as string | null,
    answer: null as string | null, answer_source: 'model', tool_error_count: 0, created_at: new Date(Date.now() - 5000).toISOString()};
  const activity = {requests: [] as object[], tool_calls: [] as object[], events: [] as object[]};
  const writes: string[] = [];
  let cursor = 0;
  await page.addInitScript(() => {
    Object.defineProperty(window, 'EventSource', {value: class extends EventTarget {
      onerror: (() => void) | null = null;
      constructor() {super(); (window as unknown as {source: EventTarget}).source = this;}
      close() {}
    }});
  });
  await page.route('**/api/**', async route => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === '/api/snapshot') await route.fulfill({json: {schema_version: 1, state, cursor, stream_id: 'conversation-test'}});
    else if (url.pathname === '/api/sessions') await route.fulfill({json: {schema_version: 1, sessions, next_cursor: null}});
    else if (url.pathname === '/api/turns' && request.method() === 'POST') {
      writes.push(request.postDataJSON().input);
      state.active_turn_id = 'turn'; state.active_session_id = 'trip'; sessions[0].status = 'running';
      await route.fulfill({json: {schema_version: 1, submission_id: request.postDataJSON().submission_id, session_id: 'trip', turn_id: 'turn'}});
    } else if (url.pathname.endsWith('/stop')) {
      state.stopping = true;
      await route.fulfill({json: {schema_version: 1}});
    } else {
      const trip = url.pathname.includes('/trip');
      await route.fulfill({json: {schema_version: 1, session: {title: trip ? '杭州行程' : '旧会话', updated_at: sessions[trip ? 0 : 1].updated_at},
        turns: trip && (sessions[0].status !== 'completed' || turn.answer) ? [turn] : [],
        ...activity, next_before: null, total_turns: trip ? 1 : 0}});
    }
  });
  await page.goto('/');
  await expect(page.getByText('旧会话', {exact: true})).toHaveCount(1);
  await expect(page.getByRole('status').filter({hasText: '本机可接收输入'})).toBeVisible();
  return {state, sessions, turn, activity, writes, notify: async () => {
    cursor++;
    await page.evaluate(({state, cursor}) => {
      const source = (window as unknown as {source: EventTarget}).source;
      source.dispatchEvent(new MessageEvent('service.state', {data: JSON.stringify({schema_version: 1, state})}));
      source.dispatchEvent(new MessageEvent('trace', {data: JSON.stringify({schema_version: 1, cursor, event_id: `event-${cursor}`, kind: 'request.started'})}));
    }, {state, cursor});
  }};
}

test('Enter 发送、Shift+Enter 换行，中文候选确认和空白输入不会发送', async ({page}) => {
  const app = await conversation(page);
  const input = page.getByRole('textbox', {name: '旅行需求'});
  await expect(page.getByText('Enter 发送 · Shift+Enter 换行', {exact: true})).toHaveCount(0);
  await input.fill('   ');
  await input.press('Enter');
  expect(app.writes).toEqual([]);
  await input.fill('杭州');
  await input.press('Shift+Enter');
  await input.pressSequentially('西湖');
  await expect(input).toHaveValue('杭州\n西湖');
  await input.dispatchEvent('compositionstart');
  await input.dispatchEvent('keydown', {key: 'Enter', code: 'Enter', isComposing: true});
  await input.dispatchEvent('compositionend');
  await input.dispatchEvent('keydown', {key: 'Enter', code: 'Enter', keyCode: 229});
  expect(app.writes).toEqual([]);
  await input.press('Enter');
  await expect.poll(() => app.writes).toEqual(['杭州\n西湖']);
  await expect(input).toHaveValue('');
  await input.fill('下一条草稿');
  await input.press('Enter');
  await expect(input).toHaveValue('下一条草稿');
  expect(app.writes).toHaveLength(1);
});

test('当前阶段来自模型和并发工具事实，停止前保持执行状态', async ({page}) => {
  const app = await conversation(page);
  app.state.active_turn_id = 'turn'; app.state.active_session_id = 'trip'; app.sessions[0].status = 'running';
  app.activity.requests = [{request_id: 'request', turn_id: 'turn', ordinal: 1, status: 'running'}];
  app.activity.events = [{event_id: 'start', turn_id: 'turn', sequence: 1, kind: 'turn.accepted', timestamp: app.turn.created_at}];
  await app.notify();
  await page.getByRole('button', {name: /杭州行程.*正在执行/}).click();
  const progress = page.getByRole('region', {name: '第 1 轮处理进展'});
  await expect(progress.getByRole('status')).toContainText('正在请求助手');
  await expect(progress).toContainText(/已等待 \d+ 秒/);
  await progress.getByText('查看处理过程', {exact: true}).click();
  await expect(progress.getByText('已接收你的输入', {exact: true})).toBeVisible();
  app.activity.requests = [{request_id: 'request', turn_id: 'turn', ordinal: 1, status: 'completed'}];
  app.activity.tool_calls = [
    {tool_call_id: 'place', turn_id: 'turn', name: 'maps_text_search', status: 'running'},
    {tool_call_id: 'route', turn_id: 'turn', name: 'maps_direction_walking', status: 'waiting'},
  ];
  await app.notify();
  await expect(progress.getByRole('status')).toContainText('正在查询地点');
  await expect(progress.getByRole('status')).toContainText('1 项等待调用额度');
  app.activity.tool_calls = [
    {tool_call_id: 'place', turn_id: 'turn', name: 'maps_text_search', status: 'completed'},
    {tool_call_id: 'route', turn_id: 'turn', name: 'maps_direction_walking', status: 'waiting'},
  ];
  await app.notify();
  await expect(progress.getByRole('status')).toContainText('等待调用额度');
  await page.getByRole('button', {name: '停止执行中的轮次'}).click();
  await expect(progress.getByRole('status')).toContainText('正在停止');
  await expect(page.getByRole('button', {name: /杭州行程.*正在停止/}).getByRole('img', {name: '正在处理'})).toBeVisible();
  await page.screenshot({path: test.info().outputPath('desktop-progress.png')});
});

test('后台完成仅显示静态小点，刷新保留，查看后清除', async ({page}) => {
  const app = await conversation(page);
  app.state.active_turn_id = 'turn'; app.state.active_session_id = 'trip'; app.sessions[0].status = 'running';
  await app.notify();
  const trip = page.getByRole('button', {name: /杭州行程.*正在执行/});
  await expect(trip.getByRole('img', {name: '正在处理'})).toBeVisible();
  await page.getByRole('button', {name: /旧会话.*已完成/}).click();
  app.turn.status = 'completed'; app.turn.answer = '西湖位于杭州。'; app.sessions[0].status = 'completed';
  app.sessions[0].updated_at = '2026-10-09T03:00:00Z';
  app.state.active_turn_id = null; app.state.active_session_id = null;
  await app.notify();
  const completed = page.getByRole('button', {name: /杭州行程.*已完成/});
  await expect(completed.getByRole('img', {name: '正在处理'})).toHaveCount(0);
  await expect(completed.getByRole('img', {name: '有未查看的结果'})).toBeVisible();
  await expect(completed).not.toContainText('未查看');
  await page.reload();
  await expect(completed.getByRole('img', {name: '有未查看的结果'})).toBeVisible();
  await completed.click();
  await expect(page.getByText('西湖位于杭州。', {exact: true})).toBeVisible();
  await expect(completed.getByRole('img', {name: '有未查看的结果'})).toHaveCount(0);
  await page.getByRole('button', {name: /旧会话.*已完成/}).click();
  await expect(completed.getByRole('img', {name: '有未查看的结果'})).toHaveCount(0);
});

test('断线不冒充处理失败，恢复后显示真实终态并保留下一条草稿', async ({page}) => {
  await page.setViewportSize({width: 390, height: 844});
  const app = await conversation(page);
  const input = page.getByRole('textbox', {name: '旅行需求'});
  await input.fill('查询西湖');
  await input.press('Enter');
  const progress = page.getByRole('region', {name: '第 1 轮处理进展'});
  await expect(progress.getByRole('status')).toContainText('正在准备请求');
  await input.fill('接着查询天气');
  await input.press('Enter');
  expect(app.writes).toEqual(['查询西湖']);
  await page.evaluate(() => (window as unknown as {source: {onerror: () => void}}).source.onerror());
  await expect(progress.getByRole('status')).toContainText('连接中断，后台可能仍在处理');
  await expect(input).toHaveValue('接着查询天气');
  await page.screenshot({path: test.info().outputPath('mobile-progress.png')});
  app.turn.status = 'terminated'; app.turn.reason = 'service_interrupted'; app.turn.answer = '服务中断，请重新提问。';
  app.sessions[0].status = 'terminated'; app.state.active_turn_id = null; app.state.active_session_id = null;
  await app.notify();
  await expect(progress.getByRole('status')).toContainText('终止');
  await expect(progress.getByRole('status')).not.toContainText('正在');
  await expect(progress.locator('.activity-spinner')).toHaveCount(0);
  await expect(input).toHaveValue('接着查询天气');
  await expect(page.getByRole('button', {name: /^发送/})).toBeEnabled();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  expect(app.writes).toHaveLength(1);
});

test('已打开会话的旧快照不能清除新结果的小点', async ({page}) => {
  const app = await conversation(page);
  app.turn.status = 'completed'; app.turn.answer = '旧回答';
  await page.getByRole('button', {name: /杭州行程.*已完成/}).click();
  await expect(page.getByText('旧回答', {exact: true})).toBeVisible();
  const oldSnapshot = {schema_version: 1, session: {title: '杭州行程', updated_at: app.sessions[0].updated_at},
    turns: [{...app.turn}], next_before: null, total_turns: 1};
  await page.route('**/api/sessions/trip?**', route => route.fulfill({json: oldSnapshot}));
  app.sessions[0].status = 'running'; app.state.active_turn_id = 'turn'; app.state.active_session_id = 'trip';
  await app.notify();
  await expect(page.getByRole('img', {name: '正在处理'})).toBeVisible();
  app.sessions[0].status = 'completed'; app.sessions[0].updated_at = '2026-10-09T03:00:00Z';
  app.state.active_turn_id = null; app.state.active_session_id = null; app.turn.answer = '新回答';
  await app.notify();
  await expect(page.getByRole('img', {name: '有未查看的结果'})).toBeVisible();
  await page.unroute('**/api/sessions/trip?**');
  await app.notify();
  await expect(page.getByText('新回答', {exact: true})).toBeVisible();
  await expect(page.getByRole('img', {name: '有未查看的结果'})).toHaveCount(0);
});

test('展开过程后新事件不会打断回看，工具失败仍等待整轮结束', async ({page}) => {
  const app = await conversation(page);
  app.turn.input = '查询杭州的地点与路线。\n'.repeat(60);
  app.sessions[0].status = 'running'; app.state.active_turn_id = 'turn'; app.state.active_session_id = 'trip';
  app.activity.tool_calls = [{tool_call_id: 'place', turn_id: 'turn', name: 'maps_text_search', status: 'failed'}];
  app.activity.events = [{event_id: 'start', turn_id: 'turn', sequence: 1, kind: 'turn.accepted', timestamp: app.turn.created_at}];
  await app.notify();
  await page.getByRole('button', {name: /杭州行程.*正在执行/}).click();
  const progress = page.getByRole('region', {name: '第 1 轮处理进展'});
  await progress.getByText('查看处理过程', {exact: true}).click();
  await expect(page.getByRole('button', {name: '回到最新', exact: true})).toBeVisible();
  const messages = page.locator('.messages');
  await messages.evaluate(element => {element.scrollTop = 50; element.dispatchEvent(new Event('scroll'));});
  const position = await messages.evaluate(element => element.scrollTop);
  app.activity.events.push({event_id: 'tool-error', turn_id: 'turn', sequence: 2, kind: 'tool.failed', tool_call_id: 'place'});
  await app.notify();
  await expect(page.getByText('有新内容', {exact: true})).toBeVisible();
  expect(await messages.evaluate(element => element.scrollTop)).toBe(position);
  await page.getByRole('button', {name: '回到最新', exact: true}).click();
  await expect(progress.getByText('查询地点失败', {exact: true})).toBeVisible();
  await expect(progress.locator('.activity-spinner')).toHaveCount(1);
  expect(app.writes).toEqual([]);
});

test('提交尚未返回时立即反馈，重复提交不重复发送，编辑的新草稿不会被清空', async ({page}) => {
  const app = await conversation(page);
  let release = () => {};
  const accepted = new Promise<void>(resolve => {release = resolve;});
  await page.route('**/api/turns', async route => {
    const submitted = route.request().postDataJSON();
    app.writes.push(submitted.input);
    await accepted;
    app.sessions[0].status = 'running'; app.state.active_turn_id = 'turn'; app.state.active_session_id = 'trip';
    await route.fulfill({json: {schema_version: 1, submission_id: submitted.submission_id, session_id: 'trip', turn_id: 'turn'}});
  });
  try {
    const input = page.getByRole('textbox', {name: '旅行需求'});
    await input.fill('第一条需求');
    await input.evaluate(element => {
      const form = (element as HTMLTextAreaElement).form!;
      form.requestSubmit(); form.requestSubmit();
    });
    await expect(page.getByText('正在发送你的消息…', {exact: true})).toBeVisible();
    await expect.poll(() => app.writes).toEqual(['第一条需求']);
    await input.fill('接着编辑的新草稿');
    await input.press('Enter');
    expect(app.writes).toEqual(['第一条需求']);
    release();
    await page.getByRole('button', {name: /杭州行程.*正在执行/}).click();
    await expect(input).toHaveValue('');
    await page.getByRole('button', {name: '新会话', exact: true}).click();
    await expect(input).toHaveValue('接着编辑的新草稿');
    expect(app.writes).toHaveLength(1);
  } finally {release();}
});
