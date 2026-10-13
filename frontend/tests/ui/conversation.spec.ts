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
    else if (url.pathname.endsWith('/stop') && request.method() === 'POST') {
      writes.push('stop'); state.stopping = true;
      await route.fulfill({json: {schema_version: 1, stopping: true}});
    } else if (url.pathname === '/api/turns' && request.method() === 'POST') {
      writes.push(request.postDataJSON().input);
      state.active_turn_id = 'turn'; state.active_session_id = 'trip'; sessions[0].status = 'running';
      await route.fulfill({json: {schema_version: 1, submission_id: request.postDataJSON().submission_id, session_id: 'trip', turn_id: 'turn'}});
    } else {
      const trip = url.pathname.includes('/trip');
      await route.fulfill({json: {schema_version: 1, session: {title: trip ? '杭州行程' : '旧会话', updated_at: sessions[trip ? 0 : 1].updated_at},
        turns: trip && (sessions[0].status !== 'completed' || turn.answer) ? [turn] : [],
        ...activity, next_before: null, total_turns: trip ? 1 : 0}});
    }
  });
  await page.goto('/');
  await expect(page.getByText('旧会话', {exact: true})).toHaveCount(1);
  await expect(page.getByRole('status').filter({hasText: '本机可接收输入'})).toHaveCount(0);
  return {state, sessions, turn, activity, writes, notify: async () => {
    cursor++;
    await page.evaluate(({state, cursor}) => {
      const source = (window as unknown as {source: EventTarget}).source;
      source.dispatchEvent(new MessageEvent('service.state', {data: JSON.stringify({schema_version: 1, state})}));
      source.dispatchEvent(new MessageEvent('trace', {data: JSON.stringify({schema_version: 1, cursor, event_id: `event-${cursor}`, kind: 'request.started'})}));
    }, {state, cursor});
  }};
}

test('模型等待重试有真实次数、延迟及可用停止入口，停止不会误记为新请求', async ({page}) => {
  const app = await conversation(page);
  app.state.active_turn_id = 'turn'; app.state.active_session_id = 'trip'; app.sessions[0].status = 'running';
  app.activity.requests = [{request_id: 'request', turn_id: 'turn', ordinal: 1, step: 1, attempt: 1,
    status: 'failed', started_at: '2026-10-12T02:00:00Z', finished_at: '2026-10-12T02:00:01Z', duration_ms: 1000}];
  app.activity.events = [{event_id: 'retry', turn_id: 'turn', request_id: 'request', sequence: 2,
    kind: 'request.retry_scheduled', timestamp: '2026-10-12T02:00:01Z',
    data: {retry_id: 'chain', step: 1, retry: 1, max_retries: 5, delay_ms: 2000, code: 'TRANSPORT', message: '连接失败'}}];
  await app.notify();
  await page.getByRole('button', {name: /杭州行程.*正在执行/}).click();
  const progress = page.getByRole('region', {name: '第 1 轮处理进展'});
  await expect(progress.getByRole('status')).toContainText('等待重试（1/5） · 2 秒');
  await page.setViewportSize({width: 1440, height: 900});
  await progress.getByText('模型重试记录 · 1 次').click();
  await expect(progress).toContainText('连接失败');
  await page.screenshot({path: test.info().outputPath('model-retry-desktop.png')});
  await page.setViewportSize({width: 390, height: 844});
  await page.getByRole('button', {name: '收起会话导航'}).first().click();
  await expect(progress.getByRole('status')).toBeVisible();
  await expect(page.getByRole('button', {name: '停止本轮重试'})).toBeVisible();
  await page.screenshot({path: test.info().outputPath('model-retry-mobile.png')});
  await page.getByRole('button', {name: '停止本轮重试'}).click();
  await expect.poll(() => app.writes).toEqual(['stop']);
  await expect(progress.getByRole('status')).toContainText('正在停止');
  await expect(page.getByRole('button', {name: '停止本轮重试'})).toBeDisabled();
});

test('轨迹保留每次尝试和重试等待，耗尽后最终失败不会被重试历史遮蔽', async ({page}) => {
  const app = await conversation(page);
  app.sessions[0].status = 'failed'; app.turn.status = 'failed'; app.turn.reason = 'model_error'; app.turn.answer_source = 'application';
  app.turn.answer = '模型请求未能完成';
  app.activity.requests = [{request_id: 'failed', turn_id: 'turn', ordinal: 6, step: 1, attempt: 6,
    status: 'failed', started_at: '2026-10-12T02:00:02Z', finished_at: '2026-10-12T02:00:03Z', duration_ms: 1000}];
  app.activity.events = [{event_id: 'retry', turn_id: 'turn', request_id: 'failed', sequence: 2,
    kind: 'request.retry_scheduled', timestamp: '2026-10-12T02:00:00Z',
    data: {retry_id: 'chain', step: 1, retry: 5, max_retries: 5, delay_ms: 2000, code: 'TRANSPORT', message: '连接失败'}},
    {event_id: 'started', turn_id: 'turn', request_id: 'failed', sequence: 3,
      kind: 'request.retry_started', timestamp: '2026-10-12T02:00:02Z', data: {retry_id: 'chain', retry: 5}}];
  await app.notify();
  await page.getByRole('button', {name: /杭州行程.*请求未完成/}).click();
  await expect(page.getByRole('region', {name: '第 1 轮处理进展'})).toContainText('失败');
  await page.getByRole('button', {name: '轨迹', exact: true}).click();
  await expect(page.getByText('模型步骤 1 · 实际尝试 6', {exact: true})).toBeVisible();
  await page.getByText('模型重试记录 · 1 次').click();
  await expect(page.getByText('退避结束，恢复尝试', {exact: true})).toBeVisible();
  await page.getByText(/查看 \d+ 个已采集时间边界/).click();
  await expect(page.getByRole('button', {name: /重试 5 · 等待开始/})).toBeVisible();
  await expect(page.getByRole('button', {name: /重试 5 · 退避结束/})).toBeVisible();
  await expect(page.getByText('请求失败', {exact: true})).toBeVisible();
});

test('预订入口点击时只访问官网，不包含后续中文说明', async ({page}) => {
  const app = await conversation(page);
  app.turn.status = 'completed';
  app.turn.answer = '- **预订入口**：湖北省博物馆官方微信公众号及官方网站（https://hbww.org.cn/）。官方界面正文未读取，请在放票周期内（提前5天）通过官方渠道按页面提示自行办理实名预约；余票未确认，不代为预约。';
  await app.notify();
  await page.getByRole('button', {name: /杭州行程.*已完成/}).click();
  const link = page.getByRole('link', {name: 'https://hbww.org.cn/', exact: true});
  await expect(link).toHaveAttribute('href', 'https://hbww.org.cn/');
  await expect(page.locator('.answer-body')).toContainText('）。官方界面正文未读取');
  await page.route('https://hbww.org.cn/**', route => route.fulfill({body: '官网入口测试'}));
  await link.click();
  await expect(page).toHaveURL('https://hbww.org.cn/');
});

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

test('只显示真实阶段和耗时，移除停止入口与处理过程日志', async ({page}) => {
  await page.setViewportSize({width: 1440, height: 900});
  const app = await conversation(page);
  app.state.active_turn_id = 'turn'; app.state.active_session_id = 'trip'; app.sessions[0].status = 'running';
  app.activity.requests = [{request_id: 'request', turn_id: 'turn', ordinal: 1, status: 'running'}];
  app.activity.events = [{event_id: 'start', turn_id: 'turn', sequence: 1, kind: 'turn.accepted', timestamp: app.turn.created_at}];
  await app.notify();
  await page.getByRole('button', {name: /杭州行程.*正在执行/}).click();
  const progress = page.getByRole('region', {name: '第 1 轮处理进展'});
  await expect(progress.getByRole('status')).toContainText('正在请求助手');
  await expect(progress).toContainText(/已等待 \d+ 秒/);
  await expect(page.getByText('查看处理过程', {exact: true})).toHaveCount(0);
  await expect(page.getByRole('button', {name: '停止执行中的轮次'})).toHaveCount(0);
  await expect(page.locator('.global-status')).toHaveCount(0);
  await expect(progress.getByText('已接收你的输入', {exact: true})).toHaveCount(0);
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
  await page.screenshot({path: test.info().outputPath('desktop-progress.png')});
  app.state.stopping = true;
  await app.notify();
  await expect(progress.getByRole('status')).toContainText('正在停止');
  await expect(page.getByRole('button', {name: /杭州行程.*正在停止/}).getByRole('img', {name: '正在处理'})).toBeVisible();
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

test('新事件不会打断滚动回看，工具失败仍等待整轮结束', async ({page}) => {
  const app = await conversation(page);
  app.turn.input = '查询杭州的地点与路线。\n'.repeat(60);
  app.sessions[0].status = 'running'; app.state.active_turn_id = 'turn'; app.state.active_session_id = 'trip';
  app.activity.tool_calls = [{tool_call_id: 'place', turn_id: 'turn', name: 'maps_text_search', status: 'failed'}];
  app.activity.events = [{event_id: 'start', turn_id: 'turn', sequence: 1, kind: 'turn.accepted', timestamp: app.turn.created_at}];
  await app.notify();
  await page.getByRole('button', {name: /杭州行程.*正在执行/}).click();
  const progress = page.getByRole('region', {name: '第 1 轮处理进展'});
  await expect(progress.getByRole('status')).toHaveText('正在准备请求');
  const messages = page.locator('.messages');
  await messages.evaluate(element => {element.scrollTop = 50; element.dispatchEvent(new Event('scroll'));});
  await expect(page.getByRole('button', {name: '回到最新', exact: true})).toBeVisible();
  const position = await messages.evaluate(element => element.scrollTop);
  app.activity.events.push({event_id: 'tool-error', turn_id: 'turn', sequence: 2, kind: 'tool.failed', tool_call_id: 'place'});
  await app.notify();
  await expect(page.getByText('有新内容', {exact: true})).toBeVisible();
  expect(await messages.evaluate(element => element.scrollTop)).toBe(position);
  await page.getByRole('button', {name: '回到最新', exact: true}).click();
  await expect(progress.getByRole('status')).toHaveText('正在准备请求');
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

test('并发查询乱序完成时状态及时更新，工具列表保持提出顺序', async ({page}) => {
  const app = await conversation(page);
  app.sessions[0].status = 'running'; app.state.active_turn_id = 'turn'; app.state.active_session_id = 'trip';
  app.activity.requests = [{request_id: 'batch', turn_id: 'turn', ordinal: 1, status: 'completed',
    started_at: '2026-10-13T02:00:00Z', finished_at: '2026-10-13T02:00:01Z', duration_ms: 1000}];
  const tools = ['maps_text_search', 'web_search_exa', 'web_fetch_exa', 'web_search_exa'].map((name, index) => ({
    tool_call_id: `tool-${index}`, request_id: 'batch', turn_id: 'turn', ordinal: index + 1, name,
    status: index === 3 ? 'waiting' : 'running', call_started: index !== 3,
    proposed_at: '2026-10-13T02:00:01Z', started_at: '2026-10-13T02:00:01Z',
    waiting_at: index === 3 ? '2026-10-13T02:00:01Z' : undefined,
    call_started_at: index !== 3 ? `2026-10-13T02:00:0${index + 1}Z` : undefined,
    finished_at: undefined as string | undefined, call_finished_at: undefined as string | undefined,
    call_duration_ms: null as number | null, total_duration_ms: null as number | null,
  }));
  app.activity.tool_calls = tools;
  await app.notify();
  await page.getByRole('button', {name: /杭州行程.*正在执行/}).click();
  await page.getByRole('button', {name: '轨迹', exact: true}).click();
  const request = page.locator('.trace-request').filter({hasText: '请求 #1'});
  await request.locator('summary').click();
  const rows = request.locator('.trace-tool');
  await expect(rows.locator('.trace-row-status')).toHaveText(['#1 · 执行中', '#2 · 执行中', '#3 · 执行中', '#4 · 等待查询']);
  tools[2].status = 'completed'; tools[2].finished_at = '2026-10-13T02:00:04Z';
  tools[2].call_finished_at = tools[2].finished_at; tools[2].call_duration_ms = 1000; tools[2].total_duration_ms = 3000;
  await app.notify();
  await expect(rows.locator('.trace-row-status')).toHaveText(['#1 · 执行中', '#2 · 执行中', '#3 · 成功', '#4 · 等待查询']);
  await expect(rows.locator('.trace-row-text')).toHaveText(['maps_text_search', 'web_search_exa', 'web_fetch_exa', 'web_search_exa']);
  for (const width of [1440, 390]) {
    await page.setViewportSize({width, height: 900});
    if (width === 390) await page.getByRole('button', {name: '收起会话导航'}).first().click();
    if (!await rows.nth(2).isVisible()) await request.locator('summary').click();
    await expect(rows.nth(2)).toBeVisible();
    await expect(rows.nth(2)).toContainText('#3 · 成功');
    await page.screenshot({path: test.info().outputPath(`concurrent-tools-${width}.png`)});
  }
  expect(app.writes).toEqual([]);
});
