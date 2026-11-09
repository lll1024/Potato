import { expect, test, type Page } from '@playwright/test';
import { writeFile } from 'node:fs/promises';

async function conversation(page: Page) {
  const state = {instance_id: 'process', answer_revision: 0, answer_preview: null as null | {
    instance_id: string; stream_id: string; session_id: string; turn_id: string;
    attempt_id: string; revision: number; text: string; status: string;
  }, active_turn_id: null as string | null, active_session_id: null as string | null,
    accepting: true, stopping: false, map_paused: false, map_pause_reason: null, service_status: 'available'};
  const sessions = [
    {session_id: 'trip', title: '杭州行程', updated_at: '2026-10-09T02:00:00Z', status: 'completed', reason: null, tool_error_count: 0},
    {session_id: 'old', title: '旧会话', updated_at: '2026-10-08T02:00:00Z', status: 'completed', reason: null, tool_error_count: 0},
  ];
  const turn = {turn_id: 'turn', ordinal: 1, input: '查询西湖', status: 'running', reason: null as string | null,
    answer: null as string | null, answer_source: 'model', model_error_code: null as string | null,
    answer_delivery: null as string | null, tool_error_count: 0, created_at: new Date(Date.now() - 5000).toISOString()};
  const activity = {requests: [] as object[], tool_calls: [] as object[], events: [] as object[]};
  const writes: string[] = [];
  let cursor = 0;
  await page.addInitScript(() => {
    Object.defineProperty(window, 'EventSource', {value: class extends EventTarget {
      onerror: (() => void) | null = null;
      constructor() {super(); const browser = window as unknown as {source: EventTarget; sources: EventTarget[]}; browser.source = this; (browser.sources ??= []).push(this);}
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

test('异常紧贴用户消息显示短提示和图标重试，不插入助手说明或资料空态', async ({page}) => {
  const app = await conversation(page);
  app.sessions[0].status = 'failed'; app.turn.status = 'failed'; app.turn.reason = 'model_error';
  app.turn.answer_source = 'application'; app.turn.answer = '模型请求未能完成';
  await app.notify();
  await page.getByRole('button', {name: /杭州行程.*请求未完成/}).click();
  const failure = page.getByRole('region', {name: '第 1 轮异常提示'});
  await expect(failure.getByRole('status')).toHaveText('请求失败');
  const retry = failure.getByRole('button', {name: '重新尝试', exact: true});
  await expect(retry).toBeEnabled();
  await expect(retry).toHaveText('');
  await expect(page.getByRole('heading', {name: '旅行助手', exact: true})).toHaveCount(0);
  await expect(page.getByText('你的旅行需求已保留。', {exact: true})).toHaveCount(0);
  await expect(page.getByText(/本轮没有可展示的查询资料/)).toHaveCount(0);
  await expect(page.getByRole('region', {name: '第 1 轮处理进展'})).toHaveCount(0);
  await expect(page.getByRole('button', {name: '查看本轮轨迹', exact: true})).toBeVisible();
});

test('模型失败按最终原因显示短提示，旧错误或供应商文字不会冒充当前原因', async ({page}) => {
  const app = await conversation(page);
  app.sessions[0].status = 'failed'; app.turn.status = 'failed'; app.turn.reason = 'model_error';
  app.turn.answer_source = 'application'; app.turn.answer = 'provider failed: secret';
  app.activity.events = [{event_id: 'old-error', turn_id: 'turn', sequence: 1, kind: 'request.retry_scheduled',
    data: {code: 'TIMEOUT', message: 'provider failed: secret'}}];
  app.turn.model_error_code = 'AUTH';
  await app.notify();
  await page.getByRole('button', {name: /杭州行程.*请求未完成/}).click();
  const hint = page.getByRole('region', {name: '第 1 轮异常提示'}).getByRole('status');
  await expect(hint).toHaveText('模型认证失败');
  for (const [code, text] of [['QUOTA', '额度不足'], ['RATE_LIMIT', '请求过于频繁'], ['TIMEOUT', '连接超时'],
    ['TRANSPORT', '连接失败'], ['SERVER', '服务暂时不可用'], ['EMPTY_RESPONSE', '模型返回空响应'],
    ['CONTEXT_WINDOW_EXCEEDED', '上下文超过模型上限'], ['INVALID_REQUEST', '请求参数无效'], ['UNKNOWN', '请求失败']]) {
    app.turn.model_error_code = code; await app.notify();
    await expect(hint).toHaveText(text);
  }
  await expect(page.getByText('provider failed: secret', {exact: true})).toHaveCount(0);
});

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
  await expect(progress.getByText('模型重试记录 · 1 次')).toHaveCount(0);
  await expect(progress.getByText('连接失败', {exact: true})).toHaveCount(0);
  await expect(page.getByRole('region', {name: '第 1 轮异常提示'})).toHaveCount(0);
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
  await expect(page.getByRole('region', {name: '第 1 轮异常提示'}).getByRole('status')).toHaveText('请求失败');
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
  await expect(progress).toHaveCount(0);
  await expect(page.getByRole('region', {name: '第 1 轮异常提示'}).getByRole('status')).toHaveText('服务中断');
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
  // 真实滚轮同时表达阅读意图，避免人工 scroll 在初次布局保护期间被忽略。
  await messages.hover();
  await page.mouse.wheel(0, -500);
  await expect(page.getByRole('button', {name: '回到最新', exact: true})).toBeVisible();
  const position = await messages.evaluate(element => element.scrollTop);
  app.activity.events.push({event_id: 'tool-error', turn_id: 'turn', sequence: 2, kind: 'tool.failed', tool_call_id: 'place'});
  await app.notify();
  await expect(page.getByText('有新内容', {exact: true})).toBeVisible();
  expect(await messages.evaluate(element => element.scrollTop)).toBe(position);
  await page.getByRole('button', {name: '回到最新', exact: true}).click();
  await expect.poll(() => messages.evaluate(element => element.scrollHeight - element.scrollTop - element.clientHeight)).toBeLessThan(8);
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


test('最终回答在保存之前分批展示 Markdown，保存后使用历史正文', async ({page}) => {
  const app = await conversation(page);
  app.state.active_turn_id = 'turn'; app.state.active_session_id = 'trip'; app.sessions[0].status = 'running';
  await app.notify();
  await page.getByRole('button', {name: /杭州行程.*正在执行/}).click();
  async function preview(text: string, revision: number) {
    app.state.answer_revision = revision;
    await page.evaluate(({text, revision}) => {
      (window as unknown as {source: EventTarget}).source.dispatchEvent(new MessageEvent('answer.preview', {data: JSON.stringify({
        schema_version: 1, kind: 'answer.preview', instance_id: 'process', revision, preview: {
          instance_id: 'process', stream_id: 'conversation-test', session_id: 'trip', turn_id: 'turn',
          attempt_id: 'answer-request', revision, text, status: 'generating',
        },
      })}));
    }, {text, revision});
  }
  await preview('## 西湖安排\n\n上午漫步湖畔。\n', 1);
  await expect(page.getByRole('heading', {name: '西湖安排', exact: true})).toBeVisible();
  await expect(page.getByText('正在生成 · 尚未保存', {exact: true})).toBeVisible();
  await expect(page.getByText('上午漫步湖畔。', {exact: true})).toBeVisible();
  await preview('## 西湖安排\n\n上午漫步湖畔。\n\n[预约](javascript:alert(1))\n下午品尝杭帮菜。', 2);
  await expect(page.getByText(/下午品尝杭帮菜/)).toBeVisible();
  await expect(page.locator('a[href^="javascript:"]')).toHaveCount(0);
  await page.setViewportSize({width: 1440, height: 900});
  await page.screenshot({path: test.info().outputPath('answer-stream-desktop.png')});
  await page.setViewportSize({width: 390, height: 844});
  await page.getByRole('button', {name: '收起会话导航'}).first().click();
  await page.screenshot({path: test.info().outputPath('answer-stream-mobile.png')});
  app.turn.status = 'completed'; app.turn.answer = '## 已保存安排\n\n完整旅行回答。';
  app.state.active_turn_id = null; app.state.active_session_id = null;
  await app.notify();
  await expect(page.getByRole('heading', {name: '已保存安排'})).toBeVisible();
  await expect(page.getByText('正在生成 · 尚未保存', {exact: true})).toHaveCount(0);
  await preview('迟到的旧正文', 3);
  await expect(page.getByText('迟到的旧正文', {exact: true})).toHaveCount(0);

});


for (const width of [1440, 390]) test(`流式中文 Markdown 重排、同轮跟随、上翻和当前全文复制 ${width}`, async ({page, context}) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await page.setViewportSize({width: 1440, height: 844});
  const app = await conversation(page);
  app.state.active_turn_id = 'turn'; app.state.active_session_id = 'trip'; app.sessions[0].status = 'running';
  await app.notify();
  await page.getByRole('button', {name: /杭州行程.*正在执行/}).click();
  await page.setViewportSize({width, height: 844});
  if (width < 760) await page.getByRole('button', {name: '收起会话导航'}).first().click();
  const messages = page.locator('.messages');
  const body = messages.locator('.answer-body');
  let text = '';
  let revision = 0;
  async function append(part: string, rendered: string) {
    text += part; revision++;
    await page.evaluate(({text, revision}) => {
      (window as unknown as {source: EventTarget}).source.dispatchEvent(new MessageEvent('answer.preview', {data: JSON.stringify({
        schema_version: 1, kind: 'answer.preview', instance_id: 'process', revision,
        preview: {instance_id: 'process', stream_id: 'conversation-test', session_id: 'trip', turn_id: 'turn',
          attempt_id: 'reading', revision, text, status: 'generating'},
      })}));
    }, {text, revision});
    await expect(body).toContainText(rendered);
  }
  const bottomGap = () => messages.evaluate(element => element.scrollHeight - element.scrollTop - element.clientHeight);
  await append('## 中文旅行安排\n\n' + '沿湖慢行，按体力安排休息。\n\n'.repeat(25) + '```swift\nlet city = "杭州"', 'let city = "杭州"');
  await expect(body.locator('pre code')).toContainText('let city = "杭州"');
  await expect.poll(bottomGap).toBeLessThan(8);
  await append('\n```\n\n| 日期 | 上午 | 下午 | 提醒 |\n', '| 日期 | 上午 | 下午 | 提醒 |');
  await expect(body.locator('table')).toHaveCount(0);
  // 分隔行和数据到达后，同一段文本重新成为表格。
  text += '| --- | --- | --- | --- |\n| 周六 | 西湖漫步 | 博物馆 | 提前预约 |\n\n[预约说明](https://example.com';
  await append('', '提前预约');
  await expect(body.locator('table')).toHaveCount(1);
  await append(')\n\n- 午后留出休息时间。\n', '午后留出休息时间。');
  await expect(body.getByRole('link', {name: '预约说明'})).toHaveAttribute('href', 'https://example.com');
  await expect.poll(bottomGap).toBeLessThan(8);
  await page.getByRole('button', {name: '复制当前正文', exact: true}).click();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(text);
  await messages.hover();
  await page.mouse.wheel(0, -500);
  const latest = page.getByRole('button', {name: '回到最新', exact: true});
  await expect(latest).toBeVisible();
  const position = await messages.evaluate(element => element.scrollTop);
  await append('\n' + '继续安排中文旅行活动。\n\n'.repeat(20), '继续安排中文旅行活动。');
  expect(await messages.evaluate(element => element.scrollTop)).toBeCloseTo(position, 0);
  await latest.click();
  await expect.poll(bottomGap).toBeLessThan(8);
  await append('\n最新到达的旅行建议。\n', '最新到达的旅行建议。');
  await expect.poll(bottomGap).toBeLessThan(8);
  await page.getByRole('button', {name: '复制当前正文', exact: true}).click();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(text);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({path: test.info().outputPath(`stream-reading-${width}.png`)});
});

test('恢复正文采用累计快照，重复、迟到与失效尝试不会回滚当前回答', async ({page}) => {
  const app = await conversation(page);
  app.state.active_turn_id = 'turn'; app.state.active_session_id = 'trip'; app.sessions[0].status = 'running';
  app.state.answer_revision = 4;
  app.state.answer_preview = {instance_id: 'process', stream_id: 'conversation-test', session_id: 'trip', turn_id: 'turn',
    attempt_id: 'first', revision: 4, text: '恢复时已有的正文', status: 'generating'};
  await page.reload();
  await page.getByRole('button', {name: /杭州行程.*正在执行/}).click();
  await expect(page.getByText('恢复时已有的正文', {exact: true})).toBeVisible();
  await page.evaluate(state => (window as unknown as {source: EventTarget}).source.dispatchEvent(new MessageEvent('service.state', {
    data: JSON.stringify({schema_version: 1, state: {...state, answer_revision: 3, active_turn_id: null, active_session_id: null}}),
  })), app.state);
  await expect(page.getByText('恢复时已有的正文', {exact: true})).toBeVisible();
  async function publish(revision: number, text: string | null, attempt = 'first', status = 'generating') {
    await page.evaluate(({revision, text, attempt, status}) => {
      (window as unknown as {source: EventTarget}).source.dispatchEvent(new MessageEvent('answer.preview', {data: JSON.stringify({
        schema_version: 1, kind: 'answer.preview', instance_id: 'process', revision,
        preview: text === null ? null : {instance_id: 'process', stream_id: 'conversation-test', session_id: 'trip', turn_id: 'turn',
          attempt_id: attempt, revision, text, status},
      })}));
    }, {revision, text, attempt, status});
  }
  await publish(6, '订阅交接期间已增加的正文');
  await expect(page.getByText('订阅交接期间已增加的正文', {exact: true})).toBeVisible();
  await publish(5, '旧次序正文');
  await publish(6, '重复事件不得修改正文');
  await expect(page.getByText('订阅交接期间已增加的正文', {exact: true})).toBeVisible();
  await publish(7, '', 'first', 'invalidated');
  await expect(page.getByText('订阅交接期间已增加的正文', {exact: true})).toHaveCount(0);
  await publish(8, '另一尝试从头开始', 'second');
  await expect(page.getByText('另一尝试从头开始', {exact: true})).toBeVisible();
  await publish(7, '旧尝试迟到');
  await expect(page.getByText('另一尝试从头开始', {exact: true})).toBeVisible();
  await publish(9, null);
  await publish(8, '已清除的预览不得复活', 'second');
  await expect(page.getByText('已清除的预览不得复活', {exact: true})).toHaveCount(0);
});


for (const outcome of ['running', 'retrying', 'restarted']) test(`断线先核对累计正文并隔离旧连接：${outcome}`, async ({page}) => {
  const restart = outcome === 'restarted';
  const retrying = outcome === 'retrying';
  const app = await conversation(page);
  app.state.active_turn_id = 'turn'; app.state.active_session_id = 'trip'; app.sessions[0].status = 'running';
  app.state.answer_revision = 1;
  app.state.answer_preview = {instance_id: 'process', stream_id: 'conversation-test', session_id: 'trip', turn_id: 'turn',
    attempt_id: 'attempt', revision: 1, text: '断线前的正文', status: 'generating'};
  await page.reload();
  await page.getByRole('button', {name: /杭州行程.*正在执行/}).click();
  await expect(page.getByText('断线前的正文', {exact: true})).toBeVisible();
  await page.evaluate(() => {
    const source = (window as unknown as {source: EventSource}).source;
    source.onerror?.(new Event('error'));
    source.onerror?.(new Event('error'));
  });
  if (restart) {
    app.state.instance_id = 'new-process'; app.state.answer_revision = 0; app.state.answer_preview = null;
    app.state.active_turn_id = null; app.state.active_session_id = null;
    app.turn.status = 'terminated'; app.turn.reason = 'service_interrupted'; app.sessions[0].status = 'terminated';
  } else {
    app.state.answer_revision = 4;
    app.state.answer_preview = {...app.state.answer_preview, revision: 4,
      text: retrying ? '' : '断线期间的累计正文', status: retrying ? 'retrying' : 'generating'};
  }
  await expect.poll(() => page.evaluate(() => (window as unknown as {sources: EventTarget[]}).sources.length)).toBe(2);
  await page.evaluate(state => (window as unknown as {source: EventTarget}).source.dispatchEvent(new MessageEvent('service.state', {
    data: JSON.stringify({schema_version: 1, state}),
  })), app.state);
  if (restart) {
    await expect(page.getByRole('region', {name: '第 1 轮异常提示'}).getByRole('status')).toHaveText('服务中断');
    await expect(page.getByText('断线前的正文', {exact: true})).toHaveCount(0);
  } else if (retrying) {
    await expect(page.getByText('正在重试，新尝试将从头作答。', {exact: true})).toBeVisible();
    await expect(page.getByText('断线前的正文', {exact: true})).toHaveCount(0);
  } else await expect(page.getByText('断线期间的累计正文', {exact: true})).toBeVisible();
  // 旧连接中已排队的正文、服务状态、删除事件及 error 均无效。
  await page.evaluate(() => {
    const old = (window as unknown as {sources: EventSource[]}).sources[0];
    old.dispatchEvent(new MessageEvent('answer.preview', {data: JSON.stringify({schema_version: 1, kind: 'answer.preview',
      instance_id: 'process', revision: 99, preview: {instance_id: 'process', stream_id: 'conversation-test', session_id: 'trip',
        turn_id: 'turn', attempt_id: 'old', revision: 99, text: '失效连接的迟到正文', status: 'generating'}})}));
    old.dispatchEvent(new MessageEvent('service.state', {data: JSON.stringify({schema_version: 1,
      state: {instance_id: 'process', active_turn_id: 'turn', active_session_id: 'trip', accepting: false}})}));
    old.dispatchEvent(new MessageEvent('trace', {data: JSON.stringify({schema_version: 1, cursor: 100, event_id: 'old-delete',
      kind: 'session.deleted', session_id: 'trip'})}));
    old.onerror?.(new Event('error'));
  });
  await expect(page.getByText('失效连接的迟到正文', {exact: true})).toHaveCount(0);
  await expect(page.getByRole('heading', {name: '杭州行程', exact: true})).toBeVisible();
  if (!restart) {
    await page.evaluate(() => (window as unknown as {source: EventTarget}).source.dispatchEvent(new MessageEvent('answer.preview', {
      data: JSON.stringify({schema_version: 1, kind: 'answer.preview', instance_id: 'process', revision: 5,
        preview: {instance_id: 'process', stream_id: 'conversation-test', session_id: 'trip', turn_id: 'turn',
          attempt_id: 'new-attempt', revision: 5, text: '重连后继续增长', status: 'generating'}}),
    })));
    await expect(page.getByText('重连后继续增长', {exact: true})).toBeVisible();
  }
  await page.screenshot({path: test.info().outputPath(restart ? 'restarted.png' : 'reconnected.png')});
  expect(app.writes).toEqual([]);
});


test('状态通知与新轮正文交错时，核对归属后仍能读到暂停中的当前正文', async ({page}) => {
  const app = await conversation(page);
  app.state.active_turn_id = 'turn'; app.state.active_session_id = 'trip'; app.sessions[0].status = 'running';
  await app.notify();
  await page.getByRole('button', {name: /杭州行程.*正在执行/}).click();
  // 服务已切换到下一轮，但旧状态通知先于新轮正文到达。
  await page.evaluate(() => (window as unknown as {source: EventTarget}).source.dispatchEvent(new MessageEvent('answer.preview', {
    data: JSON.stringify({schema_version: 1, kind: 'answer.preview', instance_id: 'process', revision: 8,
      preview: {instance_id: 'process', stream_id: 'conversation-test', session_id: 'trip', turn_id: 'next-turn',
        attempt_id: 'next-attempt', revision: 8, text: '新轮已生成但暂时暂停的正文', status: 'generating'}}),
  })));
  await expect(page.getByText('新轮已生成但暂时暂停的正文', {exact: true})).toHaveCount(0);
  app.state.active_turn_id = 'next-turn'; app.state.answer_revision = 8;
  app.turn.turn_id = 'next-turn';
  await app.notify();
  await expect(page.getByText('新轮已生成但暂时暂停的正文', {exact: true})).toBeVisible();
});


test('快照与订阅之间服务重启时，发现进程变化立即清除旧预览', async ({page}) => {
  const app = await conversation(page);
  app.state.active_turn_id = 'turn'; app.state.active_session_id = 'trip'; app.sessions[0].status = 'running';
  app.state.answer_revision = 2;
  app.state.answer_preview = {instance_id: 'process', stream_id: 'conversation-test', session_id: 'trip', turn_id: 'turn',
    attempt_id: 'attempt', revision: 2, text: '已经失去进程归属的正文', status: 'generating'};
  await page.reload();
  await page.getByRole('button', {name: /杭州行程.*正在执行/}).click();
  await expect(page.getByText('已经失去进程归属的正文', {exact: true})).toBeVisible();
  await page.evaluate(() => (window as unknown as {source: EventTarget}).source.dispatchEvent(new MessageEvent('answer.preview', {
    data: JSON.stringify({schema_version: 1, kind: 'answer.preview', instance_id: 'new-process', revision: 0, preview: null}),
  })));
  // 新快照尚未返回，此刻仍须清除已确认属于旧进程的预览，不虚构持久终态。
  await expect(page.getByText('已经失去进程归属的正文', {exact: true})).toHaveCount(0);
  await expect(page.getByText('第 1 轮 · 正在执行', {exact: true})).toBeVisible();
});

test('失败正文清空并提示重试，降级后保留完整回答与原因', async ({page}) => {
  const app = await conversation(page);
  app.state.active_turn_id = 'turn'; app.state.active_session_id = 'trip'; app.sessions[0].status = 'running';
  await app.notify();
  await page.getByRole('button', {name: /杭州行程.*正在执行/}).click();
  async function publish(text: string, revision: number, status: string, attempt: string, delivery = 'stream') {
    await page.evaluate(values => {
      (window as unknown as {source: EventTarget}).source.dispatchEvent(new MessageEvent('answer.preview', {data: JSON.stringify({
        schema_version: 1, kind: 'answer.preview', instance_id: 'process', revision: values.revision,
        preview: {instance_id: 'process', stream_id: 'conversation-test', session_id: 'trip', turn_id: 'turn',
          text: values.text, revision: values.revision, status: values.status, attempt_id: values.attempt, delivery: values.delivery},
      })}));
    }, {text, revision, status, attempt, delivery});
  }
  await publish('失败旧正文', 1, 'generating', 'first');
  await expect(page.getByText('失败旧正文', {exact: true})).toBeVisible();
  await publish('', 2, 'retrying', 'first');
  await expect(page.getByText('失败旧正文', {exact: true})).toHaveCount(0);
  await expect(page.getByText('正在重试，新尝试将从头作答。', {exact: true})).toBeVisible();
  await page.screenshot({path: test.info().outputPath('answer-retrying.png')});
  await publish('新尝试正文', 3, 'generating', 'second');
  await expect(page.getByText('新尝试正文', {exact: true})).toBeVisible();
  await expect(page.getByText('正在重试，新尝试将从头作答。', {exact: true})).toHaveCount(0);
  await publish('', 4, 'generating', 'complete', 'complete');
  const notice = page.getByText('模型服务不支持流式，改为生成完整回答。', {exact: true});
  await expect(notice).toBeVisible();
  await expect(page.getByText('新尝试正文', {exact: true})).toHaveCount(0);
  app.turn.status = 'completed'; app.turn.answer = '完整的杭州安排。'; app.turn.answer_delivery = 'complete';
  app.state.active_turn_id = null; app.state.active_session_id = null;
  await app.notify();
  await expect(page.getByText('完整的杭州安排。', {exact: true})).toBeVisible();
  await expect(notice).toBeVisible();
  await page.setViewportSize({width: 390, height: 844});
  await page.getByRole('button', {name: '收起会话导航'}).first().click();
  await page.screenshot({path: test.info().outputPath('answer-fallback-mobile.png')});
});


test('点击停止立即冻结正文，保存故障后保留可复制的未保存全文', async ({page, context}) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  const app = await conversation(page);
  app.state.active_turn_id = 'turn'; app.state.active_session_id = 'trip'; app.sessions[0].status = 'running';
  app.state.answer_revision = 1;
  app.state.answer_preview = {instance_id: 'process', stream_id: 'conversation-test', session_id: 'trip', turn_id: 'turn',
    attempt_id: 'answer', revision: 1, text: '停止前正文。', status: 'generating'};
  await page.reload();
  await page.getByRole('button', {name: /杭州行程.*正在执行/}).click();
  await expect(page.getByText('停止前正文。', {exact: true})).toBeVisible();
  let release!: () => void;
  const pending = new Promise<void>(resolve => {release = resolve;});
  await page.route('**/api/turns/turn/stop', async route => {await pending; await route.fulfill({json: {schema_version: 1, stopping: true}});});
  await page.getByRole('button', {name: '停止生成'}).click();
  await expect(page.getByText('正在停止 · 正文已暂停追加', {exact: true})).toBeVisible();
  Object.assign(app.state, {storage_error: '改名未保存', unsaved_fact: {turn_id: '', kind: 'session.rename'}});
  await app.notify();
  app.state.answer_revision = 2;
  app.state.answer_preview = {...app.state.answer_preview!, revision: 2, text: '停止前正文。收束后的完整正文。', status: 'generated'};
  await page.evaluate(preview => (window as unknown as {source: EventTarget}).source.dispatchEvent(new MessageEvent('answer.preview', {
    data: JSON.stringify({schema_version: 1, kind: 'answer.preview', instance_id: 'process', revision: 2, preview}),
  })), app.state.answer_preview);
  await expect(page.locator('.message.answer')).not.toContainText('收束后的完整正文');
  release();
  app.state.stopping = true;
  await app.notify();
  await expect(page.getByRole('button', {name: '发送旅行需求'})).toBeDisabled();
  app.state.active_turn_id = null; app.state.stopping = false; app.state.accepting = false;
  Object.assign(app.state, {storage_error: '轮次结果未保存', unsaved_fact: {turn_id: 'turn', kind: 'turn.finished', message: '未保存'}});
  await app.notify();
  await expect(page.getByText('未保存 · 请复制保留，刷新后可能丢失', {exact: true})).toBeVisible();
  await expect(page.locator('.message.answer')).toContainText('收束后的完整正文');
  await page.getByRole('button', {name: '复制未保存正文', exact: true}).click();
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe('停止前正文。收束后的完整正文。');
  await page.screenshot({path: test.info().outputPath('unsaved-answer-desktop.png')});
  await page.setViewportSize({width: 390, height: 844});
  await page.getByRole('button', {name: '收起会话导航'}).first().click();
  await page.screenshot({path: test.info().outputPath('unsaved-answer-mobile.png')});
});


test('远端停止冻结当前预览，真实保存结果与截断状态优先于迟到正文', async ({page}) => {
  const app = await conversation(page);
  app.state.active_turn_id = 'turn'; app.state.active_session_id = 'trip'; app.sessions[0].status = 'running';
  app.state.answer_revision = 1;
  app.state.answer_preview = {instance_id: 'process', stream_id: 'conversation-test', session_id: 'trip', turn_id: 'turn',
    attempt_id: 'answer', revision: 1, text: '正在阅读的正文。', status: 'generating'};
  await page.reload();
  await page.getByRole('button', {name: /杭州行程.*正在执行/}).click();
  await expect(page.getByText('正在阅读的正文。', {exact: true})).toBeVisible();
  app.state.stopping = true;
  await app.notify();
  app.state.answer_revision = 2;
  await page.evaluate(() => (window as unknown as {source: EventTarget}).source.dispatchEvent(new MessageEvent('answer.preview', {
    data: JSON.stringify({schema_version: 1, kind: 'answer.preview', instance_id: 'process', revision: 2,
      preview: {instance_id: 'process', stream_id: 'conversation-test', session_id: 'trip', turn_id: 'turn',
        attempt_id: 'answer', revision: 2, text: '不应追加的正文', status: 'generating'}}),
  })));
  await expect(page.getByText('正在停止 · 正文已暂停追加', {exact: true})).toBeVisible();
  await expect(page.getByText('不应追加的正文', {exact: true})).toHaveCount(0);
  app.turn.status = 'completed'; app.turn.answer = '真实保存的完整结果。';
  app.state.active_turn_id = null; app.state.stopping = false;
  await app.notify();
  await expect(page.getByText('真实保存的完整结果。', {exact: true})).toBeVisible();
  await expect(page.getByRole('button', {name: '复制完整回答', exact: true})).toBeVisible();
  for (const reason of ['output_limit', 'budget']) {
    app.turn.status = 'terminated'; app.turn.reason = reason; app.turn.answer = '达到限制时已保存的部分安排。';
    await app.notify();
    await expect(page.getByText('回答不完整', {exact: true})).toBeVisible();
    await expect(page.getByRole('button', {name: '复制不完整回答', exact: true})).toBeVisible();
    await expect(page.getByRole('button', {name: '复制完整回答', exact: true})).toHaveCount(0);
  }
});

test('查询说明与工具真实状态持续更新，正文保持独立且停止立即冻结', async ({page}) => {
  const app = await conversation(page);
  app.sessions[0].status = 'running';
  app.state.active_turn_id = 'turn'; app.state.active_session_id = 'trip';
  await app.notify();
  await page.getByRole('button', {name: /正在处理 杭州行程/}).click();
  async function progress(revision: number, text: string, status = 'running') {
    await page.evaluate(({revision, text, status}) => {
      const preview = {instance_id: 'process', stream_id: 'conversation-test', session_id: 'trip', turn_id: 'turn', revision,
        entries: [{id: 'note', attempt_id: 'request', kind: 'model', status: 'generated', text},
          {id: 'tool', attempt_id: 'request', kind: 'tool', status, text: '查找地点资料'}]};
      (window as unknown as {source: EventTarget}).source.dispatchEvent(new MessageEvent('progress.preview', {
        data: JSON.stringify({schema_version: 1, kind: 'progress.preview', instance_id: 'process', revision, preview}),
      }));
    }, {revision, text, status});
  }
  await progress(1, '准备查找西湖');
  const region = page.getByRole('region', {name: '查询进展', exact: true});
  await expect(region).toContainText('准备查找西湖');
  await expect(region).toContainText('执行中');
  await expect(page.getByRole('heading', {name: '旅行助手', exact: true})).toHaveCount(0);
  await progress(2, '准备查找西湖的开放资料。', 'failed');
  await expect(region).toContainText('准备查找西湖的开放资料。');
  await expect(region).toContainText('查询失败');
  await progress(1, '迟到旧说明');
  await expect(region).not.toContainText('迟到旧说明');
  // 查询阶段也能停止，不必等待最终回答开始。
  await page.getByRole('button', {name: '停止生成', exact: true}).click();
  await progress(3, '停止后迟到说明');
  await expect(region).not.toContainText('停止后迟到说明');
});

test('无换行中文与未闭合引号按连续小片段展示并保留最终原文', async ({page}) => {
  const app = await conversation(page);
  app.state.active_turn_id = 'turn'; app.state.active_session_id = 'trip'; app.sessions[0].status = 'running';
  await app.notify();
  await page.getByRole('button', {name: /杭州行程.*正在执行/}).click();
  let text = '';
  for (let index = 0; index < 9; index++) {
    text += index === 0 ? '连续阅读："先沿西湖步行' : `第${index}站观察湖畔风景🙂`;
    const revision = index + 1;
    app.state.answer_revision = revision;
    await page.evaluate(({text, revision}) => {
      (window as unknown as {source: EventTarget}).source.dispatchEvent(new MessageEvent('answer.preview', {data: JSON.stringify({
        schema_version: 1, kind: 'answer.preview', instance_id: 'process', revision,
        preview: {instance_id: 'process', stream_id: 'conversation-test', session_id: 'trip', turn_id: 'turn',
          attempt_id: 'continuous-answer', revision, text, status: 'generating'},
      })}));
    }, {text, revision});
    await expect(page.locator('.answer-body').first()).toHaveText(text);
    await expect(page.getByText('正在生成 · 尚未保存', {exact: true})).toBeVisible();
    await expect(page.getByRole('button', {name: '复制当前正文', exact: true})).toBeVisible();
  }
  app.turn.status = 'completed'; app.turn.answer = text;
  app.state.active_turn_id = null; app.state.active_session_id = null;
  await app.notify();
  await expect(page.locator('.answer-body').first()).toHaveText(text);
  await expect(page.getByRole('button', {name: '复制完整回答', exact: true})).toBeVisible();
});

test('正文大块追加在200毫秒内赶上并且持续追加不延长截止时间', async ({page}) => {
  const app = await conversation(page);
  app.state.active_turn_id = 'turn'; app.state.active_session_id = 'trip'; app.sessions[0].status = 'running';
  await app.notify();
  await page.getByRole('button', {name: /杭州行程.*正在执行/}).click();
  async function publish(text: string, revision: number, status = 'generating') {
    await page.evaluate(({text, revision, status}) => {
      (window as unknown as {source: EventTarget}).source.dispatchEvent(new MessageEvent('answer.preview', {data: JSON.stringify({
        schema_version: 1, kind: 'answer.preview', instance_id: 'process', revision,
        preview: {instance_id: 'process', stream_id: 'conversation-test', session_id: 'trip', turn_id: 'turn',
          attempt_id: 'smooth', revision, text, status},
      })}));
    }, {text, revision, status});
  }
  await publish('先看西湖。', 1);
  await expect(page.locator('.answer-body').first()).toHaveText('先看西湖。');
  await page.clock.install();
  await page.clock.pauseAt(new Date(Date.now() + 1000));
  const large = '先看西湖。' + '连续中文🙂'.repeat(200);
  await publish(large, 2);
  const first = await page.locator('.answer-body').first().innerText();
  expect(first.length).toBeGreaterThan('先看西湖。'.length);
  expect(first.length).toBeLessThan(large.length);
  await page.clock.runFor(100);
  const middle = await page.locator('.answer-body').first().innerText();
  expect(middle.length).toBeGreaterThan(first.length);
  const final = large + '后续收到的安全正文👨‍👩‍👧‍👦'.repeat(100);
  await publish(final, 3);
  await page.clock.runFor(116);
  await expect(page.locator('.answer-body').first()).toHaveText(final);
  await writeFile(test.info().outputPath('bounded-growth.json'), JSON.stringify({deadline_ms: 200,
    samples: [{elapsed_ms: 0, chars: first.length}, {elapsed_ms: 100, chars: middle.length}, {elapsed_ms: 216, chars: final.length}],
    final_markdown: final}, null, 2));
  await publish(final + '短小追加。', 4);
  await expect(page.locator('.answer-body').first()).toHaveText(final + '短小追加。');
});

test('平滑期间停止冻结可见原文，终态不等待显示队列', async ({page, context}) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  const app = await conversation(page);
  app.state.active_turn_id = 'turn'; app.state.active_session_id = 'trip'; app.sessions[0].status = 'running';
  await app.notify();
  await page.getByRole('button', {name: /杭州行程.*正在执行/}).click();
  async function publish(text: string, revision: number, status = 'generating') {
    await page.evaluate(({text, revision, status}) => (window as unknown as {source: EventTarget}).source.dispatchEvent(
      new MessageEvent('answer.preview', {data: JSON.stringify({schema_version: 1, kind: 'answer.preview', instance_id: 'process', revision,
        preview: {instance_id: 'process', stream_id: 'conversation-test', session_id: 'trip', turn_id: 'turn',
          attempt_id: 'smooth-stop', revision, text, status}})})), {text, revision, status});
  }
  await publish('可读正文。', 1);
  await expect(page.locator('.answer-body').first()).toHaveText('可读正文。');
  await page.clock.install(); await page.clock.pauseAt(new Date(Date.now() + 1000));
  const large = '可读正文。' + '中文👨‍👩‍👧‍👦继续阅读。'.repeat(200);
  await publish(large, 2);
  const frozen = await page.locator('.answer-body').first().innerText();
  expect(frozen.length).toBeLessThan(large.length);
  await page.getByRole('button', {name: '停止生成', exact: true}).click();
  await page.clock.runFor(500);
  await expect(page.locator('.answer-body').first()).toHaveText(frozen);
  await page.getByRole('button', {name: '复制当前正文', exact: true}).click();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(frozen);
  app.turn.status = 'completed'; app.turn.answer = large;
  app.state.active_turn_id = null; app.state.active_session_id = null;
  await app.notify();
  await expect(page.locator('.answer-body').first()).toHaveText(large);
  await page.getByRole('button', {name: '复制完整回答', exact: true}).click();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(large);
});

test('生成完成立即展示全文，减少动态效果直接呈现并保留安全Markdown', async ({page}) => {
  const app = await conversation(page);
  app.state.active_turn_id = 'turn'; app.state.active_session_id = 'trip'; app.sessions[0].status = 'running';
  await app.notify(); await page.getByRole('button', {name: /杭州行程.*正在执行/}).click();
  async function publish(text: string, revision: number, status = 'generating') {
    await page.evaluate(({text, revision, status}) => (window as unknown as {source: EventTarget}).source.dispatchEvent(
      new MessageEvent('answer.preview', {data: JSON.stringify({schema_version: 1, kind: 'answer.preview', instance_id: 'process', revision,
        preview: {instance_id: 'process', stream_id: 'conversation-test', session_id: 'trip', turn_id: 'turn',
          attempt_id: 'smooth-complete', revision, text, status}})})), {text, revision, status});
  }
  await publish('正文。', 1); await expect(page.locator('.answer-body').first()).toHaveText('正文。');
  await page.clock.install(); await page.clock.pauseAt(new Date(Date.now() + 1000));
  const text = '正文。' + '安全中文🙂'.repeat(300);
  await publish(text, 2); expect((await page.locator('.answer-body').first().innerText()).length).toBeLessThan(text.length);
  await publish(text, 3, 'generated');
  await expect(page.locator('.answer-body').first()).toHaveText(text);
  await page.emulateMedia({reducedMotion: 'reduce'});
  await publish(text + '继续追加中文'.repeat(300), 4);
  await expect(page.locator('.answer-body').first()).toHaveText(text + '继续追加中文'.repeat(300));
});

test('正文出现自动折叠查询进展，手动展开和刷新历史回看保持真实顺序', async ({page}) => {
  const app = await conversation(page);
  app.sessions[0].status = 'running';
  app.state.active_turn_id = 'turn'; app.state.active_session_id = 'trip';
  Object.assign(app.turn, {query_progress: [
    {id:'note', attempt_id:'query', kind:'model', text:'准备查找西湖资料。', status:'generated'},
    {id:'tool', attempt_id:'query', kind:'tool', text:'查找地点资料', status:'failed'},
  ]});
  await app.notify();
  await page.getByRole('button', {name: /正在处理 杭州行程/}).click();
  const progress = page.getByRole('region', {name:'查询进展', exact:true});
  await expect(progress).toContainText('准备查找西湖资料。');
  await expect(progress).toHaveJSProperty('open', true);
  async function answer(revision: number, text: string) {
    await page.evaluate(({revision,text}) => {
      (window as unknown as {source:EventTarget}).source.dispatchEvent(new MessageEvent('answer.preview', {data:JSON.stringify({
        schema_version:1, kind:'answer.preview', instance_id:'process', revision,
        preview:{instance_id:'process',stream_id:'conversation-test',session_id:'trip',turn_id:'turn',attempt_id:'answer',revision,text,status:'generating'},
      })}));
    }, {revision,text});
  }
  await answer(1,'# 旅行安排');
  await expect(progress).toHaveJSProperty('open', false);
  await progress.locator('summary').click();
  await expect(progress).toHaveJSProperty('open', true);
  await expect(progress).toContainText('查询失败');
  await answer(2,'# 旅行安排\n\n按需调整。');
  await expect(progress).toHaveJSProperty('open', true);
  app.sessions[0].status='completed'; app.turn.status='completed'; app.turn.answer='# 旅行安排\n\n按需调整。';
  app.state.active_turn_id=null; app.state.active_session_id=null;
  await app.notify();
  await expect(page.getByRole('button',{name:'复制完整回答',exact:true})).toBeVisible();
  await page.reload();
  await page.getByRole('button', {name:/杭州行程.*已完成/}).click();
  const saved = page.getByRole('region',{name:'查询进展',exact:true});
  await expect(saved).toHaveJSProperty('open',false);
  await saved.locator('summary').click();
  await expect(saved.locator('li')).toHaveText(['准备查找西湖资料。 行动说明','查找地点资料 查询失败']);
  await expect(saved).toContainText('已保存的查询进展');
});


test('查询进展大块追加有界展开，失效尝试和切换会话清除待展示内容', async ({page}) => {
  const app = await conversation(page);
  app.state.active_turn_id = 'turn'; app.state.active_session_id = 'trip'; app.sessions[0].status = 'running';
  await app.notify(); await page.getByRole('button', {name: /杭州行程.*正在执行/}).click();
  async function progress(text: string, revision: number, status = 'generating', attempt = 'query') {
    await page.evaluate(({text, revision, status, attempt}) => (window as unknown as {source: EventTarget}).source.dispatchEvent(
      new MessageEvent('progress.preview', {data: JSON.stringify({schema_version: 1, kind: 'progress.preview', instance_id: 'process', revision,
        preview: {instance_id: 'process', stream_id: 'conversation-test', session_id: 'trip', turn_id: 'turn', revision,
          entries: [{id: 'query', attempt_id: attempt, kind: 'model', text, status}]}})})), {text, revision, status, attempt});
  }
  await progress('准备查找西湖资料。', 1);
  const text = page.locator('.query-progress li span').first();
  await expect(text).toHaveText('准备查找西湖资料。');
  await page.clock.install(); await page.clock.pauseAt(new Date(Date.now() + 1000));
  const large = '准备查找西湖资料。' + '比较沿湖出行方式🙂'.repeat(25);
  await progress(large, 2);
  const first = await text.innerText(); expect(first.length).toBeLessThan(large.length);
  await page.clock.runFor(100);
  expect((await text.innerText()).length).toBeGreaterThan(first.length);
  await page.clock.runFor(116); await expect(text).toHaveText(large);
  await progress(large + '仍待核实的信息。'.repeat(30), 3);
  await progress('', 4, 'invalidated');
  await expect(page.locator('.query-progress')).toHaveCount(0);
  await page.clock.runFor(500);
  await expect(page.locator('.query-progress')).toHaveCount(0);
  await progress('重新查找资料。', 5, 'generating', 'retry');
  await expect(text).toHaveText('重新查找资料。');
  await progress('重新查找资料。' + '后续安全片段。'.repeat(80), 6, 'generating', 'retry');
  await page.getByRole('button', {name: /旧会话/}).first().click();
  await page.clock.runFor(500);
  await expect(page.locator('.query-progress')).toHaveCount(0);
});

test('断线累计快照直接恢复全文而不重新播放', async ({page}) => {
  const app = await conversation(page);
  app.state.active_turn_id = 'turn'; app.state.active_session_id = 'trip'; app.sessions[0].status = 'running';
  await app.notify(); await page.getByRole('button', {name: /杭州行程.*正在执行/}).click();
  await page.clock.install(); await page.clock.pauseAt(new Date(Date.now() + 1000));
  const restored = '恢复时已经收到的正文👨‍👩‍👧‍👦'.repeat(150);
  app.state.answer_revision = 9;
  app.state.answer_preview = {instance_id: 'process', stream_id: 'conversation-test', session_id: 'trip', turn_id: 'turn',
    attempt_id: 'restored', revision: 9, text: restored, status: 'generating'};
  await page.evaluate(() => (window as unknown as {source: {onerror: () => void}}).source.onerror());
  await page.clock.runFor(2000);
  await expect(page.locator('.answer-body').first()).toHaveText(restored);
  await expect(page.getByRole('button', {name: '复制当前正文', exact: true})).toBeVisible();
});

for (const width of [1440, 390]) test(`进展折叠保持上翻阅读锚点并能回到最新 ${width}`, async ({page}) => {
  const app = await conversation(page);
  app.sessions[0].status = 'completed'; app.turn.status = 'completed';
  app.turn.answer = '# 阅读保位\n\n' + '已经读到的中文旅行安排。\n\n'.repeat(100);
  Object.assign(app.turn, {query_progress: Array.from({length: 12}, (_, index) => ({
    id: `saved-${index}`, attempt_id: 'query', kind: 'model', text: `第${index + 1}项资料获取说明，信息仍待按旅行日期核实。`, status: 'generated',
  }))});
  await app.notify(); await page.getByRole('button', {name: /杭州行程.*已完成/}).click();
  await page.setViewportSize({width, height: 844});
  if (width < 760) await page.getByRole('button', {name: '收起会话导航'}).first().click();
  const progress = page.getByRole('region', {name: '查询进展', exact: true});
  await progress.locator('summary').click();
  const messages = page.locator('.messages'); const answer = page.locator('.message.answer');
  await messages.hover(); await page.mouse.wheel(0, -10000);
  await expect(page.getByRole('button', {name: '回到最新', exact: true})).toBeVisible();
  await messages.evaluate(element => {
    const body = element.querySelector('.message.answer') as HTMLElement;
    element.scrollTop += body.getBoundingClientRect().top - element.getBoundingClientRect().top + 40;
  });
  // 先让这次人工定位提交可见布局与滚动事件，再检查随后的折叠保位。
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
  await expect(page.getByRole('button', {name: '回到最新', exact: true})).toBeVisible();
  const before = await answer.evaluate(element => element.getBoundingClientRect().top);
  await progress.evaluate(element => {(element as HTMLDetailsElement).open = false;});
  await expect(progress).toHaveJSProperty('open', false);
  await expect.poll(async () => Math.abs((await answer.evaluate(element => element.getBoundingClientRect().top)) - before)).toBeLessThan(3);
  await page.screenshot({path: test.info().outputPath(`fold-reading-${width}.png`)});
  await page.getByRole('button', {name: '回到最新', exact: true}).click();
  await expect.poll(() => messages.evaluate(element => element.scrollHeight - element.scrollTop - element.clientHeight)).toBeLessThan(8);
});

for (const width of [1440, 390]) test(`查询重试清除平滑旧说明，保留可靠事实并解释完整等待 ${width}`, async ({page}) => {
  const app = await conversation(page);
  app.sessions[0].status = 'running'; app.state.active_turn_id = 'turn'; app.state.active_session_id = 'trip';
  await app.notify(); await page.getByRole('button', {name: /杭州行程.*正在执行/}).click();
  await page.setViewportSize({width, height: 844});
  if (width < 760) await page.getByRole('button', {name: '收起会话导航'}).first().click();
  async function progress(text: string, revision: number, status: string, attempt: string, delivery = 'stream') {
    await page.evaluate(({text, revision, status, attempt, delivery}) => {
      (window as unknown as {source: EventTarget}).source.dispatchEvent(new MessageEvent('progress.preview', {data: JSON.stringify({
        schema_version: 1, kind: 'progress.preview', instance_id: 'process', revision,
        preview: {instance_id: 'process', stream_id: 'conversation-test', session_id: 'trip', turn_id: 'turn', revision,
          entries: [{id: 'saved-tool', attempt_id: 'earlier-query', kind: 'tool', text: '查找地点资料', status: 'completed'},
            {id: attempt, attempt_id: attempt, kind: 'model', text, status, delivery}]},
      })}));
    }, {text, revision, status, attempt, delivery});
  }
  await progress('失败旧说明。', 1, 'generating', 'failed-query');
  await expect(page.locator('.query-progress')).toContainText('失败旧说明。');
  await page.clock.install(); await page.clock.pauseAt(new Date(Date.now() + 1000));
  await progress('失败旧说明。' + '还未完成的查询说明。'.repeat(100), 2, 'generating', 'failed-query');
  await progress('', 3, 'retrying', 'failed-query');
  await expect(page.getByText('查询进展正在重试，新尝试将重新生成。', {exact: true})).toBeVisible();
  await page.clock.runFor(500);
  await expect(page.locator('.query-progress')).not.toContainText('失败旧说明');
  await expect(page.locator('.query-progress')).toContainText('已取得资料');
  await progress('重新查找交通资料。', 4, 'generating', 'new-query');
  await expect(page.locator('.query-progress')).toContainText('重新查找交通资料。');
  await expect(page.getByText('查询进展正在重试，新尝试将重新生成。', {exact: true})).toHaveCount(0);
  await progress('', 5, 'generating', 'complete-query', 'complete');
  await expect(page.getByText('模型服务不支持流式，改为生成完整回答。', {exact: true})).toBeVisible();
  await expect(page.locator('.query-progress')).not.toContainText('重新查找交通资料');
  await page.screenshot({path: test.info().outputPath('query-complete-wait.png')});
});

test('查询进展次序阻止旧状态提前释放名额', async ({page}) => {
  const app = await conversation(page);
  app.sessions[0].status = 'running'; app.state.active_turn_id = 'turn'; app.state.active_session_id = 'trip';
  await app.notify();
  await page.getByRole('button', {name: /杭州行程.*正在执行/}).click();
  await page.getByRole('textbox').fill('保留下一条草稿');
  await page.evaluate(state => {
    const source = (window as unknown as {source: EventTarget}).source;
    source.dispatchEvent(new MessageEvent('progress.preview', {data: JSON.stringify({schema_version:1, kind:'progress.preview',
      instance_id:'process', revision:8, preview:{instance_id:'process', stream_id:'conversation-test', session_id:'trip',
        turn_id:'turn', revision:8, entries:[{id:'query',attempt_id:'request',kind:'model',status:'generating',text:'准备查找西湖资料'}]}})}));
    source.dispatchEvent(new MessageEvent('service.state', {data: JSON.stringify({schema_version:1,
      state:{...state, progress_revision:7, active_turn_id:null, active_session_id:null}})}));
  }, app.state);
  await expect(page.getByRole('region', {name:'查询进展',exact:true})).toContainText('准备查找西湖资料');
  await expect(page.getByRole('button', {name:'发送旅行需求',exact:true})).toBeDisabled();
  expect(app.writes).toEqual([]);
});

test('同一快照独立交接进展和正文，不丢弃另一通道的新内容', async ({page}) => {
  const app = await conversation(page);
  app.sessions[0].status = 'running';
  await page.locator('.history-select').filter({hasText:'杭州行程'}).click();
  await page.getByRole('textbox').fill('开始查询');
  let release!: () => void, entered!: () => void;
  const waiting = new Promise<void>(resolve => { release = resolve; });
  const requested = new Promise<void>(resolve => { entered = resolve; });
  await page.route('**/api/snapshot', async route => {
    entered(); await waiting;
    await route.fulfill({json:{schema_version:1, cursor:0, stream_id:'conversation-test', state:{...app.state,
      answer_revision:5, answer_preview:{instance_id:'process',stream_id:'conversation-test',session_id:'trip',turn_id:'turn',
        attempt_id:'answer',revision:5,text:'较旧正文',status:'generating'},
      progress_revision:10, progress_preview:{instance_id:'process',stream_id:'conversation-test',session_id:'trip',turn_id:'turn',revision:10,
        entries:[{id:'query',attempt_id:'query',kind:'model',status:'generated',text:'快照中已经完成的查询说明'}]}}}});
  });
  await page.getByRole('button', {name:'发送旅行需求',exact:true}).click();
  await requested;
  await app.notify();
  await page.evaluate(() => (window as unknown as {source:EventTarget}).source.dispatchEvent(new MessageEvent('answer.preview', {
    data:JSON.stringify({schema_version:1,kind:'answer.preview',instance_id:'process',revision:6,
      preview:{instance_id:'process',stream_id:'conversation-test',session_id:'trip',turn_id:'turn',attempt_id:'answer',revision:6,
        text:'实时连接中更新的正文',status:'generating'}})})));
  await expect(page.getByText('实时连接中更新的正文',{exact:true})).toBeVisible();
  release();
  const progress = page.getByRole('region',{name:'查询进展',exact:true});
  await expect(progress).toBeVisible();
  await progress.locator('summary').click();
  await expect(progress).toContainText('快照中已经完成的查询说明');
  await expect(page.getByText('实时连接中更新的正文',{exact:true})).toBeVisible();
  await expect(page.getByText('较旧正文',{exact:true})).toHaveCount(0);
  expect(app.writes).toEqual(['开始查询']);
});

test('首次停止快照不冻结上一轮的进展归属', async ({page}) => {
  const app = await conversation(page);
  app.sessions[0].status = 'running'; app.turn.turn_id = 'previous';
  app.state.active_turn_id = 'previous'; app.state.active_session_id = 'trip';
  Object.assign(app.state, {progress_revision:1, progress_preview:{instance_id:'process', stream_id:'conversation-test',
    session_id:'trip', turn_id:'previous', revision:1,
    entries:[{id:'old-query', attempt_id:'old', kind:'model', text:'上一轮的查询行动。', status:'generating'}]}});
  await app.notify(); await page.getByRole('button', {name:/杭州行程.*正在执行/}).click();
  await page.evaluate(() => (window as unknown as {source:EventTarget}).source.dispatchEvent(new MessageEvent('progress.preview', {
    data:JSON.stringify({schema_version:1,kind:'progress.preview',instance_id:'process',revision:1,
      preview:{instance_id:'process',stream_id:'conversation-test',session_id:'trip',turn_id:'previous',revision:1,
        entries:[{id:'old-query',attempt_id:'old',kind:'model',text:'上一轮的查询行动。',status:'generating'}]}}),
  })));
  await expect(page.getByRole('region', {name:'查询进展', exact:true})).toContainText('上一轮的查询行动。');
  app.turn.turn_id = 'turn'; app.turn.ordinal = 2; app.state.active_turn_id = 'turn';
  await app.notify();
  const current = page.getByRole('region', {name:'第 2 轮对话', exact:true});
  await expect(current).toBeVisible();
  await expect(current.getByRole('region', {name:'查询进展', exact:true})).toHaveCount(0);
  Object.assign(app.state, {stopping:true, progress_revision:2, progress_preview:{instance_id:'process', stream_id:'conversation-test',
    session_id:'trip', turn_id:'turn', revision:2,
    entries:[{id:'current-query', attempt_id:'current', kind:'model', text:'本轮停止前已经收到的资料说明。', status:'generated'}]}});
  await page.clock.install(); await page.clock.pauseAt(new Date(Date.now()+1000));
  await page.evaluate(() => (window as unknown as {source:{onerror:()=>void}}).source.onerror());
  await page.clock.runFor(2000);
  await expect(current.getByRole('region', {name:'查询进展', exact:true})).toContainText('本轮停止前已经收到的资料说明。');
  await expect(current.getByRole('region', {name:'查询进展', exact:true})).not.toContainText('上一轮的查询行动。');
});

for (const outcome of ['saved', 'unsaved', 'truncated', 'failed']) test(`跨端停止冻结两类可见队列并采用真实结果 ${outcome}`, async ({page, context}) => {
  await context.grantPermissions(['clipboard-read','clipboard-write']);
  const app = await conversation(page);
  app.sessions[0].status='running'; app.state.active_turn_id='turn'; app.state.active_session_id='trip';
  await app.notify(); await page.getByRole('button',{name:/杭州行程.*正在执行/}).click();
  async function progress(text: string, revision: number, status='generating') {
    Object.assign(app.state,{progress_revision:revision,progress_preview:{instance_id:'process',stream_id:'conversation-test',
      session_id:'trip',turn_id:'turn',revision,entries:[{id:'query',attempt_id:'query',kind:'model',text,status}]}});
    await page.evaluate(({text,revision,status}) => (window as unknown as {source:EventTarget}).source.dispatchEvent(new MessageEvent('progress.preview',{
      data:JSON.stringify({schema_version:1,kind:'progress.preview',instance_id:'process',revision,
        preview:{instance_id:'process',stream_id:'conversation-test',session_id:'trip',turn_id:'turn',revision,
          entries:[{id:'query',attempt_id:'query',kind:'model',text,status}]}}),
    })),{text,revision,status});
  }
  async function answer(text: string | null, revision: number, status='generating') {
    app.state.answer_revision=revision;
    app.state.answer_preview=text===null?null:{instance_id:'process',stream_id:'conversation-test',session_id:'trip',turn_id:'turn',
      attempt_id:'answer',revision,text,status};
    await page.evaluate(({text,revision,status}) => (window as unknown as {source:EventTarget}).source.dispatchEvent(new MessageEvent('answer.preview',{
      data:JSON.stringify({schema_version:1,kind:'answer.preview',instance_id:'process',revision,
        preview:text===null?null:{instance_id:'process',stream_id:'conversation-test',session_id:'trip',turn_id:'turn',attempt_id:'answer',revision,text,status}}),
    })),{text,revision,status});
  }
  await progress('准备查找西湖资料。',1);
  await answer('# 西湖旅行\n\n先看湖景。',1);
  await expect(page.locator('.answer-body').first()).toContainText('先看湖景。');
  const process = page.getByRole('region',{name:'查询进展',exact:true});
  await process.locator('summary').click();
  await page.clock.install(); await page.clock.pauseAt(new Date(Date.now()+1000));
  const fullProgress='准备查找西湖资料。'+'比较沿湖交通🙂'.repeat(20);
  const body='# 西湖旅行\n\n先看湖景。'+'正文原文👨‍👩‍👧‍👦'.repeat(160);
  await progress(fullProgress,2); await answer(body,2);
  await page.clock.runFor(16);
  await expect(process.locator('li span').first()).not.toHaveText('准备查找西湖资料。');
  const frozenBody=await page.locator('.answer-body').first().innerText();
  const frozenProgress=await process.locator('li span').first().innerText();
  expect(frozenBody.length).toBeLessThan(body.length);
  expect(frozenProgress.length).toBeLessThan(fullProgress.length);
  app.state.stopping=true; await app.notify();
  await expect(page.getByText('正在停止 · 正文已暂停追加',{exact:true})).toBeVisible();
  await answer(body+'\n\n真实收束尾部。',3,'generated');
  await progress(fullProgress+'已取得候选资料。',3,'generated');
  await page.clock.runFor(500);
  await expect(page.locator('.answer-body').first()).toHaveText(frozenBody);
  await expect(process.locator('li span').first()).toHaveText(frozenProgress);
  await expect(page.getByRole('button',{name:'发送旅行需求',exact:true})).toBeDisabled();
  await page.getByRole('button',{name:'复制当前正文',exact:true}).click();
  // Markdown 标记不会出现在排版文本中；复制取相同已显示前缀的原文。
  const frozenMarkdown=await page.evaluate(()=>navigator.clipboard.readText());
  expect(frozenMarkdown.startsWith('# 西湖旅行')).toBe(true);
  expect(frozenMarkdown.length).toBeLessThan(body.length);
  expect(frozenMarkdown).not.toContain('准备查找西湖');
  Object.assign(app.turn,{query_progress:[{id:'query',attempt_id:'query',kind:'model',text:fullProgress+'已取得候选资料。',status:'generated'}]});
  app.state.active_turn_id=null; app.state.active_session_id=null; app.state.stopping=false;
  const final=body+'\n\n真实收束尾部。';
  if(outcome==='unsaved') {
    Object.assign(app.state,{accepting:false,storage_error:'最终回答无法保存',unsaved_fact:{turn_id:'turn',kind:'turn.finished',message:'未保存'}});
  } else {
    app.turn.status=outcome==='saved'?'completed':outcome==='truncated'?'terminated':'failed';
    app.turn.reason=outcome==='truncated'?'output_limit':outcome==='failed'?'model_error':null;
    app.turn.answer=outcome==='failed'?'模型失败说明':final;
    app.turn.answer_source=outcome==='failed'?'application':'model';
    app.turn.model_error_code=outcome==='failed'?'AUTH':null;
    await answer(null,4);
  }
  await app.notify();
  if(outcome==='failed') {
    await expect(page.getByText('模型认证失败',{exact:true})).toBeVisible();
    await expect(page.locator('.answer-body')).toHaveCount(0);
  } else {
    const label=outcome==='unsaved'?'复制未保存正文':outcome==='truncated'?'复制不完整回答':'复制完整回答';
    await page.getByRole('button',{name:label,exact:true}).click();
    expect(await page.evaluate(()=>navigator.clipboard.readText())).toBe(final);
    if(outcome==='unsaved') {
      await expect(page.getByText('未保存 · 请复制保留，刷新后可能丢失',{exact:true})).toBeVisible();
      await expect(page.getByRole('button',{name:'发送旅行需求',exact:true})).toBeDisabled();
    }
  }
});

for (const width of [1440, 390]) test(`可靠完成到历史迟到期间正文连续且上翻保位 ${width}`, async ({page}) => {
  const app = await conversation(page);
  app.sessions[0].status = 'running'; app.state.active_turn_id = 'turn'; app.state.active_session_id = 'trip';
  const text = '## 西湖安排\n\n' + '沿湖慢行，按体力安排休息。\n\n'.repeat(45);
  Object.assign(app.state,{progress_revision:1,progress_preview:{instance_id:'process',stream_id:'conversation-test',session_id:'trip',turn_id:'turn',revision:1,
    entries:[{id:'query',attempt_id:'query',kind:'model',text:'正在查找西湖交通资料',status:'generated'}]}});
  app.state.answer_revision = 1;
  app.state.answer_preview = {instance_id:'process',stream_id:'conversation-test',session_id:'trip',turn_id:'turn',
    attempt_id:'answer',revision:1,text,status:'generated'};
  await page.reload();
  await page.getByRole('button', {name:/杭州行程.*正在执行/}).click();
  await page.setViewportSize({width,height:844});
  if (width < 760) await page.getByRole('button', {name:'收起会话导航'}).first().click();
  const messages = page.locator('.messages');
  await expect(page.getByRole('region',{name:'查询进展',exact:true})).toBeVisible();
  await expect(messages.locator('.answer-body')).toContainText('西湖安排');
  await messages.evaluate(element => {element.dispatchEvent(new WheelEvent('wheel',{deltaY:-450,bubbles:true})); element.scrollTop = 450; element.dispatchEvent(new Event('scroll'));});
  await expect(page.getByRole('button',{name:'回到最新',exact:true})).toBeVisible();
  const offset = () => messages.locator('[data-reading-anchor="answer:turn"]').evaluate(element => element.getBoundingClientRect().top);
  const before = await offset();
  let release!: () => void;
  const barrier = new Promise<void>(resolve => {release=resolve;});
  let requested!: () => void;
  const request = new Promise<void>(resolve => {requested=resolve;});
  await page.route('**/api/sessions/trip?*', async route => {requested(); await barrier; await route.fallback();});
  app.turn.status='completed'; app.turn.answer=text+'\n历史完整结尾。'; app.sessions[0].status='completed';
  app.state.active_turn_id=null; app.state.active_session_id=null; app.state.answer_revision=2; app.state.answer_preview=null;
  await page.evaluate(state => {
    const source=(window as unknown as {source:EventTarget}).source;
    source.dispatchEvent(new MessageEvent('trace',{data:JSON.stringify({schema_version:1,cursor:10,event_id:'finished',kind:'turn.finished',session_id:'trip',turn_id:'turn',data:{status:'completed',reason:null,answer_source:'model'}})}));
    source.dispatchEvent(new MessageEvent('service.state',{data:JSON.stringify({schema_version:1,state})}));
    source.dispatchEvent(new MessageEvent('answer.preview',{data:JSON.stringify({schema_version:1,kind:'answer.preview',instance_id:'process',revision:2,preview:null})}));
    source.dispatchEvent(new MessageEvent('progress.preview',{data:JSON.stringify({schema_version:1,kind:'progress.preview',instance_id:'process',revision:2,preview:null})}));
  },app.state);
  await request;
  await expect(page.getByRole('region',{name:'查询进展',exact:true})).toBeVisible();
  await expect(messages.locator('.answer-body')).toContainText('西湖安排');
  await expect(page.getByRole('button',{name:'复制当前正文',exact:true})).toBeVisible();
  await expect(page.getByRole('button',{name:'复制完整回答',exact:true})).toHaveCount(0);
  expect(await offset()).toBeCloseTo(before,0);
  release();
  await expect(messages.locator('.answer-body')).toContainText('历史完整结尾。');
  await expect(page.getByRole('button',{name:'复制完整回答',exact:true})).toBeVisible();
  expect(await offset()).toBeCloseTo(before,0);
});

for (const change of ['invalidated', 'retrying', 'attempt', 'turn', 'process', 'page', 'null-without-finish']) test(`完成交接显示不跨失效边界复活：${change}`, async ({page}) => {
  const app = await conversation(page);
  app.sessions[0].status='running'; app.state.active_turn_id='turn'; app.state.active_session_id='trip';
  app.state.answer_revision=1;
  app.state.answer_preview={instance_id:'process',stream_id:'conversation-test',session_id:'trip',turn_id:'turn',
    attempt_id:'answer',revision:1,text:'只属于本次作答的正文',status:'generated'};
  await page.reload();
  await page.getByRole('button',{name:/杭州行程.*正在执行/}).click();
  await expect(page.locator('.answer-body')).toContainText('只属于本次作答的正文');
  await page.evaluate(change => {
    const source=(window as unknown as {source:EventTarget}).source;
    if (change!=='null-without-finish') source.dispatchEvent(new MessageEvent('trace',{data:JSON.stringify({schema_version:1,cursor:10,event_id:'finished',kind:'turn.finished',session_id:'trip',turn_id:'turn',data:{status:'completed',answer_source:'model'}})}));
    source.dispatchEvent(new MessageEvent('answer.preview',{data:JSON.stringify({schema_version:1,kind:'answer.preview',instance_id:'process',revision:2,preview:null})}));
  },change);
  if (change!=='null-without-finish') await expect(page.locator('.answer-body')).toContainText('只属于本次作答的正文');
  if (change==='page') {
    await page.getByRole('button',{name:'轨迹',exact:true}).click();
    await page.getByRole('button',{name:'对话',exact:true}).click();
  } else if (change==='turn') {
    app.state.active_turn_id='next'; app.state.answer_revision=2; app.state.answer_preview=null;
    await app.notify();
  } else if (change!=='null-without-finish') {
    await page.evaluate(change => {
      (window as unknown as {source:EventTarget}).source.dispatchEvent(new MessageEvent('answer.preview',{data:JSON.stringify({schema_version:1,kind:'answer.preview',instance_id:change==='process'?'new-process':'process',revision:3,
        preview:change==='process'?null:{instance_id:'process',stream_id:'conversation-test',session_id:'trip',turn_id:'turn',attempt_id:change==='attempt'?'next':'answer',revision:3,text:'',status:change==='attempt'?'generating':change}})}));
    },change);
  }
  await expect(page.getByText('只属于本次作答的正文',{exact:true})).toHaveCount(0);
  await page.evaluate(() => (window as unknown as {source:EventTarget}).source.dispatchEvent(new MessageEvent('answer.preview',{data:JSON.stringify({schema_version:1,kind:'answer.preview',instance_id:'process',revision:1,
    preview:{instance_id:'process',stream_id:'conversation-test',session_id:'trip',turn_id:'turn',attempt_id:'answer',revision:1,text:'重复旧正文不得复活',status:'generated'}})})));
  await expect(page.getByText('重复旧正文不得复活',{exact:true})).toHaveCount(0);
});

for (const status of ['generating', 'generated']) test(`完成通知后的同尝试迟到累计正文与查询进展不回退 ${status}`, async ({page}) => {
  const app = await conversation(page);
  app.sessions[0].status='running'; app.state.active_turn_id='turn'; app.state.active_session_id='trip';
  app.state.answer_revision=1;
  app.state.answer_preview={instance_id:'process',stream_id:'conversation-test',session_id:'trip',turn_id:'turn',attempt_id:'answer',revision:1,text:'## 已经读到的回答。\n\n'+'沿湖慢行，按体力安排休息。\n\n'.repeat(35),status:'generating'};
  const progress={instance_id:'process',stream_id:'conversation-test',session_id:'trip',turn_id:'turn',revision:1,
    entries:[{id:'query',attempt_id:'query',kind:'tool',text:'核对西湖开放资料',status:'running'}]};
  Object.assign(app.state,{progress_revision:1,progress_preview:progress});
  await page.reload();
  await page.getByRole('button',{name:/杭州行程.*正在执行/}).click();
  await expect(page.locator('.answer-body')).toContainText('已经读到的回答。');
  await page.getByRole('region',{name:'查询进展',exact:true}).locator('summary').click();
  const messages=page.locator('.messages');
  await messages.evaluate(element=>{element.dispatchEvent(new WheelEvent('wheel',{deltaY:-450,bubbles:true}));element.scrollTop=450;element.dispatchEvent(new Event('scroll'));});
  await expect(page.getByRole('button',{name:'回到最新',exact:true})).toBeVisible();
  const offset=()=>messages.locator('[data-reading-anchor="answer:turn"]').evaluate(element=>element.getBoundingClientRect().top);
  const before=await offset();
  let release!: () => void;
  const barrier=new Promise<void>(resolve=>{release=resolve;});
  await page.route('**/api/sessions/trip?*',async route=>{await barrier; await route.fallback();});
  await page.evaluate(({state,progress,status})=>{
    const source=(window as unknown as {source:EventTarget}).source;
    source.dispatchEvent(new MessageEvent('trace',{data:JSON.stringify({schema_version:1,cursor:10,event_id:'finished',kind:'turn.finished',session_id:'trip',turn_id:'turn',data:{status:'completed',answer_source:'model'}})}));
    source.dispatchEvent(new MessageEvent('service.state',{data:JSON.stringify({schema_version:1,state:{...state,answer_revision:3,progress_revision:3,active_turn_id:null,active_session_id:null}})}));
    source.dispatchEvent(new MessageEvent('answer.preview',{data:JSON.stringify({schema_version:1,kind:'answer.preview',instance_id:'process',revision:2,
      preview:{...state.answer_preview,revision:2,text:state.answer_preview!.text+'迟到的真实累计结尾。',status}})}));
    source.dispatchEvent(new MessageEvent('progress.preview',{data:JSON.stringify({schema_version:1,kind:'progress.preview',instance_id:'process',revision:2,
      preview:{...progress,revision:2,entries:[{...progress.entries[0],status:'completed'}]}})}));
  },{state:app.state,progress,status});
  await expect(page.locator('.answer-body')).toContainText('迟到的真实累计结尾。');
  await expect(page.getByRole('region',{name:'查询进展',exact:true})).toContainText('已取得资料');
  await page.evaluate(()=>{
    const source=(window as unknown as {source:EventTarget}).source;
    for (const kind of ['answer.preview','progress.preview']) source.dispatchEvent(new MessageEvent(kind,{data:JSON.stringify({schema_version:1,kind,instance_id:'process',revision:3,preview:null})}));
  });
  await page.evaluate(()=>new Promise<void>(resolve=>requestAnimationFrame(()=>resolve())));
  await expect(page.locator('.answer-body')).toContainText('迟到的真实累计结尾。');
  await expect(page.getByRole('region',{name:'查询进展',exact:true})).toContainText('已取得资料');
  expect(await offset()).toBeCloseTo(before,0);
  release();
});

for (const channel of ['answer', 'progress', 'both']) test(`结束状态宣布的内容次序阻止旧状态重新开放停止入口 ${channel}`, async ({page})=>{
  const app=await conversation(page);
  app.sessions[0].status='running'; app.state.active_turn_id='turn'; app.state.active_session_id='trip';
  app.state.answer_revision=1;
  app.state.answer_preview={instance_id:'process',stream_id:'conversation-test',session_id:'trip',turn_id:'turn',attempt_id:'answer',revision:1,text:'当前已读回答',status:'generated'};
  await page.reload();
  await page.getByRole('button',{name:/杭州行程.*正在执行/}).click();
  await expect(page.getByRole('button',{name:'停止生成',exact:true})).toBeVisible();
  await page.evaluate(state=>{
    const source=(window as unknown as {source:EventTarget}).source;
    source.dispatchEvent(new MessageEvent('service.state',{data:JSON.stringify({schema_version:1,state:{...state,answer_revision:3,progress_revision:3,active_turn_id:null,active_session_id:null}})}));
  },app.state);
  await expect(page.getByRole('button',{name:'停止生成',exact:true})).toHaveCount(0);
  await page.evaluate(({state,channel})=>(window as unknown as {source:EventTarget}).source.dispatchEvent(new MessageEvent('service.state',{data:JSON.stringify({schema_version:1,state:{...state,answer_revision:channel==='progress'?3:2,progress_revision:channel==='answer'?3:2}})})),{state:app.state,channel});
  await expect(page.getByRole('button',{name:'停止生成',exact:true})).toHaveCount(0);
  await page.evaluate(()=>{
    const source=(window as unknown as {source:EventTarget}).source;
    for (const kind of ['answer.preview','progress.preview']) source.dispatchEvent(new MessageEvent(kind,{data:JSON.stringify({schema_version:1,kind,instance_id:'process',revision:3,preview:null})}));
  });
  await expect(page.locator('.answer-body')).toHaveCount(0);
});

for (const newer of ['answer','progress']) test(`较旧HTTP快照不能重开结束状态但可独立采用较新${newer}内容`,async ({page})=>{
  const app=await conversation(page);
  app.sessions[0].status='running'; app.state.active_turn_id='turn'; app.state.active_session_id='trip'; app.state.answer_revision=1;
  app.state.answer_preview={instance_id:'process',stream_id:'conversation-test',session_id:'trip',turn_id:'turn',attempt_id:'answer',revision:1,text:'已读正文',status:'generated'};
  await page.reload();
  await page.getByRole('button',{name:/杭州行程.*正在执行/}).click();
  let release!:()=>void, requested!:()=>void;
  const barrier=new Promise<void>(resolve=>{release=resolve;});
  const request=new Promise<void>(resolve=>{requested=resolve;});
  await page.route('**/api/snapshot',async route=>{
    requested(); await barrier;
    const answerRevision=newer==='answer'?4:2, progressRevision=newer==='progress'?4:2;
    await route.fulfill({json:{schema_version:1,cursor:0,stream_id:'conversation-test',state:{...app.state,
      answer_revision:answerRevision,answer_preview:{...app.state.answer_preview,revision:answerRevision,text:'HTTP迟到的真实累计正文'},
      progress_revision:progressRevision,progress_preview:{instance_id:'process',stream_id:'conversation-test',session_id:'trip',turn_id:'turn',revision:progressRevision,
        entries:[{id:'query',attempt_id:'query',kind:'tool',text:'HTTP迟到的真实查询进展',status:'completed'}]}}}});
  });
  await page.getByRole('button',{name:'停止生成',exact:true}).click();
  await request;
  await page.evaluate(state=>(window as unknown as {source:EventTarget}).source.dispatchEvent(new MessageEvent('service.state',{data:JSON.stringify({schema_version:1,state:{...state,answer_revision:3,progress_revision:3,active_turn_id:null,active_session_id:null,stopping:false}})})),app.state);
  await expect(page.getByRole('button',{name:'停止生成',exact:true})).toHaveCount(0);
  const response=page.waitForResponse('**/api/snapshot');
  release(); await response;
  await page.evaluate(()=>new Promise<void>(resolve=>requestAnimationFrame(()=>resolve())));
  await expect(page.getByRole('button',{name:'停止生成',exact:true})).toHaveCount(0);
  await expect(page.locator('.answer-body')).toContainText('HTTP迟到的真实累计正文');
  await page.getByRole('region',{name:'查询进展',exact:true}).locator('summary').click();
  await expect(page.getByRole('region',{name:'查询进展',exact:true})).toContainText('HTTP迟到的真实查询进展');
  await expect(page.getByRole('button',{name:'停止生成',exact:true})).toHaveCount(0);
});

for (const historical of ['invalidated','retrying']) for (const exposure of ['known','first-late']) test(`累计进展保留旧${historical}条目时可靠完成交接不撤销 ${exposure}`,async ({page})=>{
  const app=await conversation(page);
  app.sessions[0].status='running'; app.state.active_turn_id='turn'; app.state.active_session_id='trip'; app.state.answer_revision=1;
  app.state.answer_preview={instance_id:'process',stream_id:'conversation-test',session_id:'trip',turn_id:'turn',attempt_id:'answer',revision:1,text:'可靠完成后仍须连续可读的回答',status:'generated'};
  const progress={instance_id:'process',stream_id:'conversation-test',session_id:'trip',turn_id:'turn',revision:1,
    entries:[{id:'old',attempt_id:'old',kind:'model',text:'',status:historical},
      {id:'current',attempt_id:'current',kind:'model',text:'正在核对开放资料',status:'generated'},
      {id:'tool',attempt_id:'tool',kind:'tool',text:'核对西湖开放资料',status:'running'}]};
  Object.assign(app.state,{progress_revision:1,progress_preview:{...progress,entries:exposure==='first-late'?progress.entries.slice(1):progress.entries}});
  await page.reload();
  await page.getByRole('button',{name:/杭州行程.*正在执行/}).click();
  await expect(page.locator('.answer-body')).toContainText('可靠完成后仍须连续可读的回答');
  let release!:()=>void;
  const barrier=new Promise<void>(resolve=>{release=resolve;});
  await page.route('**/api/sessions/trip?*',async route=>{await barrier;await route.fallback();});
  await page.evaluate(({state,progress})=>{
    const source=(window as unknown as {source:EventTarget}).source;
    source.dispatchEvent(new MessageEvent('trace',{data:JSON.stringify({schema_version:1,cursor:10,event_id:'finished',kind:'turn.finished',session_id:'trip',turn_id:'turn',data:{status:'completed',answer_source:'model'}})}));
    source.dispatchEvent(new MessageEvent('service.state',{data:JSON.stringify({schema_version:1,state:{...state,answer_revision:2,progress_revision:3,active_turn_id:null,active_session_id:null}})}));
    source.dispatchEvent(new MessageEvent('answer.preview',{data:JSON.stringify({schema_version:1,kind:'answer.preview',instance_id:'process',revision:2,preview:null})}));
    source.dispatchEvent(new MessageEvent('progress.preview',{data:JSON.stringify({schema_version:1,kind:'progress.preview',instance_id:'process',revision:2,
      preview:{...progress,revision:2,entries:progress.entries.map(entry=>entry.id==='tool'?{...entry,status:'completed'}:entry)}})}));
    source.dispatchEvent(new MessageEvent('progress.preview',{data:JSON.stringify({schema_version:1,kind:'progress.preview',instance_id:'process',revision:3,preview:null})}));
  },{state:app.state,progress});
  await page.evaluate(()=>new Promise<void>(resolve=>requestAnimationFrame(()=>resolve())));
  await expect(page.locator('.answer-body')).toContainText('可靠完成后仍须连续可读的回答');
  await page.getByRole('region',{name:'查询进展',exact:true}).locator('summary').click();
  await expect(page.getByRole('region',{name:'查询进展',exact:true})).toContainText('已取得资料');
  await page.evaluate(({progress,historical})=>{
    (window as unknown as {source:EventTarget}).source.dispatchEvent(new MessageEvent('progress.preview',{data:JSON.stringify({schema_version:1,kind:'progress.preview',instance_id:'process',revision:4,
      preview:{...progress,revision:4,entries:progress.entries.map(entry=>entry.id==='current'?{...entry,text:'',status:historical}:entry.id==='tool'?{...entry,status:'completed'}:entry)}})}));
  },{progress,historical});
  await page.evaluate(()=>new Promise<void>(resolve=>requestAnimationFrame(()=>resolve())));
  await expect(page.locator('.answer-body')).toContainText('可靠完成后仍须连续可读的回答');
  await expect(page.getByRole('region',{name:'查询进展',exact:true})).not.toContainText('正在核对开放资料');
  await page.evaluate(()=>(window as unknown as {source:EventTarget}).source.dispatchEvent(new MessageEvent('progress.preview',{data:JSON.stringify({schema_version:1,kind:'progress.preview',instance_id:'process',revision:5,preview:null})})));
  await page.evaluate(()=>new Promise<void>(resolve=>requestAnimationFrame(()=>resolve())));
  await expect(page.locator('.answer-body')).toContainText('可靠完成后仍须连续可读的回答');
  await expect(page.getByRole('region',{name:'查询进展',exact:true})).not.toContainText('正在核对开放资料');
  await expect(page.getByRole('region',{name:'查询进展',exact:true})).toContainText('已取得资料');
  release();
});

for (const outcome of ['failed-replaced','replaced','matching','query','query-failed','summary']) test(`持久请求事实约束完成交接的作答尝试 ${outcome}`,async ({page})=>{
  const app=await conversation(page);
  app.sessions[0].status='running'; app.state.active_turn_id='turn'; app.state.active_session_id='trip';app.state.answer_revision=1;
  app.state.answer_preview={instance_id:'process',stream_id:'conversation-test',session_id:'trip',turn_id:'turn',attempt_id:'A',revision:1,text:'A尝试已经显示的部分正文',status:'generating'};
  await page.reload();
  await page.getByRole('button',{name:/杭州行程.*正在执行/}).click();
  await expect(page.locator('.answer-body')).toContainText('A尝试已经显示的部分正文');
  let release!:()=>void;
  const barrier=new Promise<void>(resolve=>{release=resolve;});
  await page.route('**/api/sessions/trip?*',async route=>{await barrier;await route.fallback();});
  await page.evaluate(({state,outcome})=>{
    const source=(window as unknown as {source:EventTarget}).source;
    let cursor=10;
    const fact=(kind:string,request_id:string,data:object={})=>source.dispatchEvent(new MessageEvent('trace',{data:JSON.stringify({schema_version:1,cursor:cursor++,event_id:`fact-${cursor}`,kind,request_id,session_id:'trip',turn_id:'turn',data})}));
    fact('request.started','A',{answer_phase:'answer'});
    if (outcome==='failed-replaced'||outcome==='matching') fact('request.failed','A');
    fact('request.started','B',outcome==='summary'?{purpose:'context_summary'}:{answer_phase:outcome.startsWith('query')?'query':'answer'});
    if (outcome==='matching') source.dispatchEvent(new MessageEvent('answer.preview',{data:JSON.stringify({schema_version:1,kind:'answer.preview',instance_id:'process',revision:2,
      preview:{...state.answer_preview,attempt_id:'B',revision:2,text:'B尝试可靠完成的正文',status:'generated'}})}));
    fact(outcome==='query-failed'?'request.failed':'request.completed','B');
    fact('turn.finished','',{status:'completed',answer_source:'model'});
    source.dispatchEvent(new MessageEvent('service.state',{data:JSON.stringify({schema_version:1,state:{...state,answer_revision:4,progress_revision:4,active_turn_id:null,active_session_id:null}})}));
    for(const kind of ['answer.preview','progress.preview']) source.dispatchEvent(new MessageEvent(kind,{data:JSON.stringify({schema_version:1,kind,instance_id:'process',revision:4,preview:null})}));
  },{state:app.state,outcome});
  await page.evaluate(()=>new Promise<void>(resolve=>requestAnimationFrame(()=>resolve())));
  if(outcome.startsWith('query')||outcome==='summary') await expect(page.locator('.answer-body')).toContainText('A尝试已经显示的部分正文');
  else await expect(page.getByText('A尝试已经显示的部分正文',{exact:true})).toHaveCount(0);
  if(outcome==='matching') {
    await expect(page.locator('.answer-body')).toContainText('B尝试可靠完成的正文');
    await page.evaluate(preview=>(window as unknown as {source:EventTarget}).source.dispatchEvent(new MessageEvent('answer.preview',{data:JSON.stringify({schema_version:1,kind:'answer.preview',instance_id:'process',revision:5,preview:{...preview,revision:5,text:'失败A迟到不得复活'}})})),app.state.answer_preview);
    await page.evaluate(()=>new Promise<void>(resolve=>requestAnimationFrame(()=>resolve())));
    await expect(page.locator('.answer-body')).toContainText('B尝试可靠完成的正文');
    await expect(page.getByText('失败A迟到不得复活',{exact:true})).toHaveCount(0);
  }
  app.turn.status='completed';app.turn.answer=(outcome.startsWith('query')||outcome==='summary')?'A真实保存的全文':'B真实保存的全文';app.sessions[0].status='completed';
  release();
  await expect(page.locator('.answer-body')).toContainText(app.turn.answer);
});

test('续接先采用B累计快照，重放快照之前的A请求事实不撤正文',async ({page})=>{
  const app=await conversation(page);
  app.sessions[0].status='running';app.state.active_turn_id='turn';app.state.active_session_id='trip';app.state.answer_revision=1;
  app.state.answer_preview={instance_id:'process',stream_id:'conversation-test',session_id:'trip',turn_id:'turn',attempt_id:'A',revision:1,text:'断线前A正文',status:'generating'};
  await page.reload();await page.getByRole('button',{name:/杭州行程.*正在执行/}).click();
  await page.evaluate(()=>(window as unknown as {source:EventTarget}).source.dispatchEvent(new MessageEvent('trace',{data:JSON.stringify({schema_version:1,cursor:1,event_id:'start-A',kind:'request.started',session_id:'trip',turn_id:'turn',request_id:'A',data:{answer_phase:'answer'}})})));
  app.state.answer_revision=3;app.state.answer_preview={...app.state.answer_preview,attempt_id:'B',revision:3,text:'续接时B已经可读的累计正文'};
  await page.route('**/api/snapshot',route=>route.fulfill({json:{schema_version:1,state:app.state,cursor:20,stream_id:'conversation-test'}}));
  await page.evaluate(()=>(window as unknown as {source:EventSource}).source.onerror?.(new Event('error')));
  await expect.poll(()=>page.evaluate(()=>(window as unknown as {sources:EventTarget[]}).sources.length)).toBe(2);
  await expect(page.locator('.answer-body')).toContainText('续接时B已经可读的累计正文');
  await page.evaluate(()=>(window as unknown as {source:EventTarget}).source.dispatchEvent(new MessageEvent('trace',{data:JSON.stringify({schema_version:1,cursor:10,event_id:'replay-A',kind:'request.started',session_id:'trip',turn_id:'turn',request_id:'A',data:{answer_phase:'answer'}})})));
  await page.evaluate(()=>new Promise<void>(resolve=>requestAnimationFrame(()=>resolve())));
  await expect(page.locator('.answer-body')).toContainText('续接时B已经可读的累计正文');
});
