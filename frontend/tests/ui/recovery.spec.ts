import { expect, test, type Page } from '@playwright/test';

async function recovery(page: Page) {
  const state = {accepting: true, active_turn_id: null as string | null, active_session_id: null as string | null,
    stopping: false, map_paused: false, service_status: 'available', storage_error: undefined as string | undefined,
    unsaved_fact: null as {turn_id: string; kind: string; message: string} | null};
  const turn = {turn_id: 'failed', ordinal: 1, input: '春节去三亚', status: 'failed', reason: 'model_error' as string | null,
    answer_source: 'application', answer: '检查 MODEL_ID maps_weather {"data":{"city":"三亚"}}' as string | null, tool_error_count: 0,
    query_materials: [{title: '天气资料', entries: [['城市：三亚', '预报日期：2026-10-09', '白天天气：晴']],
      notes: ['旅行年份或日期尚未确认，尚无法确认适用性。'], tool_call_id: 'weather', payload_id: 'weather-result'}]};
  const writes: object[] = [];
  let cursor = 0;
  await page.addInitScript(() => {
    Object.defineProperty(window, 'EventSource', {value: class extends EventTarget {constructor() {super(); (window as unknown as {source: EventTarget}).source = this;} close() {}}});
  });
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/turns') {
      writes.push(route.request().postDataJSON());
      state.active_turn_id = 'retry'; state.active_session_id = 'trip';
      await route.fulfill({json: {schema_version: 1, submission_id: 'new-submission', session_id: 'trip', turn_id: 'retry'}});
    } else if (url.pathname === '/api/snapshot') await route.fulfill({json: {schema_version: 1, state, cursor: 0, stream_id: 'recovery'}});
    else if (url.pathname === '/api/sessions') await route.fulfill({json: {schema_version: 1,
      sessions: [{session_id: 'trip', title: '三亚旅行', status: turn.status, reason: turn.reason, updated_at: '2026-10-09T00:00:00Z', tool_error_count: 0}], next_cursor: null}});
    else await route.fulfill({json: {schema_version: 1, session: {title: '三亚旅行'}, turns: [turn],
      requests: [], tool_calls: [], events: [], next_before: null, total_turns: 1}});
  });
  await page.goto('/');
  await page.getByRole('button', {name: /^三亚旅行/}).click();
  return {state, turn, writes, notify: () => page.evaluate(({state, cursor}) => {
    const source = (window as unknown as {source: EventTarget}).source;
    source.dispatchEvent(new MessageEvent('service.state', {data: JSON.stringify({schema_version: 1, state})}));
    source.dispatchEvent(new MessageEvent('trace', {data: JSON.stringify({schema_version: 1, cursor, event_id: String(cursor), kind: 'turn.finished'})}));
  }, {state, cursor: ++cursor})};
}

test('旧异常会话只显示紧凑提示，图标重试保留草稿并只提交一次', async ({page}) => {
  const {writes} = await recovery(page);
  await expect(page.getByRole('region', {name: '第 1 轮异常提示'}).getByRole('status')).toHaveText('请求失败');
  await expect(page.getByText(/MODEL_ID/)).toHaveCount(0);
  await expect(page.getByText(/maps_weather/)).toHaveCount(0);
  await expect(page.getByText('城市：三亚', {exact: true})).not.toBeVisible();
  await expect(page.getByText('查看已有资料', {exact: true})).toHaveCount(0);
  expect(writes).toHaveLength(0);
  const input = page.getByRole('textbox', {name: '旅行需求'});
  await input.fill('正在编辑的需求');
  const retry = page.getByRole('button', {name: '重新尝试', exact: true});
  await retry.evaluate(element => { (element as HTMLButtonElement).click(); (element as HTMLButtonElement).click(); });
  await expect.poll(() => writes.length).toBe(1);
  expect(writes[0]).toMatchObject({input: '春节去三亚', session_id: 'trip'});
  await expect(input).toHaveValue('正在编辑的需求');
  await expect(retry).toBeDisabled();
  await expect(page.getByText(/本机可接收输入/)).not.toBeVisible();
});

test('预算终止仍展示收尾回答与未完成限制，刷新不会自动重放', async ({page}) => {
  const app = await recovery(page);
  app.turn.reason = 'budget'; app.turn.status = 'terminated'; app.turn.answer_source = 'model';
  app.turn.answer = '旅行行程尚未完成。\n\n已取得三亚天气资料，交通路线尚未查询。';
  await app.notify();
  await expect(page.getByRole('region', {name: '第 1 轮异常提示'}).getByRole('status')).toHaveText('查询达到上限');
  await expect(page.getByText('回答不完整', {exact: true})).toBeVisible();
  await expect(page.getByText(/已取得三亚天气资料，交通路线尚未查询/)).toBeVisible();
  await page.reload();
  await page.getByRole('button', {name: /^三亚旅行/}).click();
  await expect(page.getByText(/已取得三亚天气资料，交通路线尚未查询/)).toBeVisible();
  expect(app.writes).toHaveLength(0);
});

for (const [reason, meaning] of [
  ['execution_error', '服务执行失败'], ['budget', '查询达到上限'], ['user_stop', '已停止'],
  ['map_paused', '地图查询已暂停'], ['service_shutdown', '本机服务已退出'],
  ['service_interrupted', '服务中断'], ['output_limit', '回答达到输出上限'], ['storage_failure', '结果未完整保存'],
  ['context_budget', '上下文超过安全预算'], ['context_configuration', '上下文容量配置无效'],
  ['context_window_exceeded', '上下文超过模型上限'], ['context_recovery_unavailable', '上下文无法恢复'],
]) {
  test(`异常终局 ${reason} 按真实原因说明`, async ({page}) => {
    const app = await recovery(page);
    app.turn.reason = reason;
    app.turn.query_materials = [];
    if (reason === 'output_limit') {app.turn.answer_source = 'model'; app.turn.answer = '实际收到的未完整回答';}
    if (reason === 'storage_failure') app.state.accepting = false;
    if (reason === 'map_paused') app.state.map_paused = true;
    await app.notify();
    await expect(page.getByRole('region', {name: '第 1 轮异常提示'}).getByRole('status')).toHaveText(meaning);
    await expect(page.getByText('查看已有资料', {exact: true})).toHaveCount(0);
    if (reason === 'output_limit') {
      await expect(page.getByText('实际收到的未完整回答', {exact: true})).toBeVisible();
      await expect(page.getByText('回答不完整', {exact: true})).toBeVisible();
      app.turn.answer = ''; await app.notify();
      await expect(page.getByRole('heading', {name: '旅行助手', exact: true})).toHaveCount(0);
    } else await expect(page.getByRole('heading', {name: '旅行助手', exact: true})).toHaveCount(0);
    if (reason === 'storage_failure') await expect(page.getByText('你的旅行需求已保留。', {exact: true})).toHaveCount(0);
    if (reason === 'map_paused' || reason === 'storage_failure') await expect(page.getByRole('button', {name: '重新尝试', exact: true})).toBeDisabled();
    expect(app.writes).toHaveLength(0);
  });
}

test('提交响应丢失后核对沿用身份，忙和停止期间禁止新重试', async ({page}) => {
  const app = await recovery(page);
  const retry = page.getByRole('button', {name: '重新尝试', exact: true});
  app.state.active_turn_id = 'another'; app.state.active_session_id = 'elsewhere';
  await app.notify(); await expect(retry).toBeDisabled();
  app.state.stopping = true;
  await app.notify(); await expect(retry).toBeDisabled();
  app.state.active_turn_id = null; app.state.stopping = false;
  await app.notify(); await expect(retry).toBeEnabled();
  let submitted: {submission_id: string; input: string; session_id: string} | undefined;
  await page.route('**/api/turns', async route => {
    submitted = route.request().postDataJSON(); app.writes.push(submitted!);
    await route.abort('failed');
  });
  await retry.click();
  await expect(page.getByRole('button', {name: '核对提交', exact: true})).toBeVisible();
  await expect(retry).toBeDisabled();
  const checks: string[] = [];
  await page.route('**/api/submissions/**', async route => {
    checks.push(new URL(route.request().url()).pathname.split('/').at(-1)!);
    app.state.active_turn_id = 'new';
    await route.fulfill({json: {schema_version: 1, ...submitted, turn_id: 'new'}});
  });
  await page.getByRole('button', {name: '核对提交', exact: true}).click();
  await expect.poll(() => checks).toEqual([submitted!.submission_id]);
  expect(app.writes).toHaveLength(1);
});

test('真实存储故障保持上次保存的运行状态，不冒充已保存终局或继续执行', async ({page}) => {
  const app = await recovery(page);
  app.turn.status = 'running'; app.turn.reason = null; app.turn.answer = null;
  app.state.accepting = false;
  app.state.storage_error = '存储故障：轮次结果未保存。';
  app.state.unsaved_fact = {turn_id: 'failed', kind: 'turn.finished', message: '轮次结果未保存。'};
  await app.notify();
  const round = page.getByRole('region', {name: '第 1 轮对话', exact: true});
  // section 的可访问名称属于浏览器的公开交互界面。
  await expect(round.getByRole('region', {name: '第 1 轮异常提示'}).getByRole('status')).toHaveText('结果未完整保存');
  await expect(round.getByRole('region', {name: '第 1 轮处理进展'})).toHaveCount(0);
  await expect(round.getByText('你的旅行需求已保留。', {exact: true})).toHaveCount(0);
  await expect(round.getByRole('button', {name: '重新尝试', exact: true})).toBeDisabled();
  expect(app.writes).toHaveLength(0);
});
