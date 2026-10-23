import { expect, test } from '@playwright/test';

for (const width of [1440, 390]) {
  test(`数据目录更换后忽略迟到分页且重置草稿与历史缓存 ${width}`, async ({page}) => {
    await page.setViewportSize({width, height: 900});
    let dataset = 'original';
    let release!: () => void;
    const held = new Promise<void>(resolve => {release = resolve;});
    let requested = false;
    const state = {accepting: true, active_turn_id: null, active_session_id: null, stopping: false,
      map_paused: false, service_status: 'available'};
    await page.addInitScript(() => {
      Object.defineProperty(window, 'EventSource', {value: class extends EventTarget {
        onerror: (() => void) | null = null;
        constructor() {super(); (window as unknown as {source: EventSource}).source = this as unknown as EventSource;}
        close() {}
      }});
    });
    const turn = (id: string, ordinal: number, input: string) => ({turn_id: id, ordinal, input,
      status: 'completed', answer: '已保存回答', answer_source: 'model', tool_error_count: 0});
    await page.route('**/api/**', async route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/api/snapshot') await route.fulfill({json: {schema_version: 1, state, cursor: 0, stream_id: dataset}});
      else if (url.pathname === '/api/sessions') await route.fulfill({json: {schema_version: 1, sessions: [
        {session_id: 'same-id', title: '测试旅行', status: 'completed', updated_at: '2026-10-24T00:00:00Z', tool_error_count: 0},
      ], next_cursor: null}});
      else if (url.searchParams.has('before')) {
        requested = true;
        await held;
        await route.fulfill({json: {schema_version: 1, session: {title: '旧目录'}, turns: [turn('old', 1, '旧目录的私有输入')], requests: [], tool_calls: [], events: [], next_before: null, total_turns: 51}});
      } else await route.fulfill({json: {schema_version: 1, session: {title: '测试旅行'}, turns: [
        turn(dataset, dataset === 'original' ? 51 : 1, dataset === 'original' ? '原目录最新输入' : '新目录输入'),
      ], requests: [], tool_calls: [], events: [], next_before: dataset === 'original' ? 51 : null, total_turns: dataset === 'original' ? 51 : 1}});
    });
    await page.goto('/');
    if (width < 760) await page.getByRole('button', {name: '打开会话导航', exact: true}).click();
    await page.getByRole('button', {name: /^测试旅行.*已完成/}).click();
    await page.getByRole('textbox', {name: '旅行需求'}).fill('旧目录草稿');
    await page.getByRole('button', {name: '加载更早对话', exact: true}).click();
    await expect.poll(() => requested).toBe(true);
    dataset = 'replacement';
    await page.evaluate(() => (window as unknown as {source: EventSource}).source.onerror?.(new Event('error')));
    await expect(page.getByText(/已重新读取一致快照/)).toBeVisible();
    if (width < 760) await page.getByRole('button', {name: '打开会话导航', exact: true}).click();
    await page.getByRole('button', {name: /^测试旅行.*已完成/}).click();
    release();
    await expect(page.getByText('新目录输入', {exact: true})).toBeVisible();
    await expect(page.getByText('旧目录的私有输入', {exact: true})).toHaveCount(0);
    await expect(page.getByRole('textbox', {name: '旅行需求'})).toHaveValue('');
  });
}

for (const width of [1440, 390]) {
  test(`轨迹分页去重并定位未加载轮次的完整载荷 ${width}`, async ({page}) => {
    await page.setViewportSize({width, height: 900});
    const makeTurn = (ordinal: number) => ({turn_id: `turn-${ordinal}`, ordinal, input: `旅行需求${ordinal}`,
      input_payload_id: `input-${ordinal}`, answer_payload_id: null, status: 'completed', reason: null,
      answer_source: 'model', tool_error_count: 0});
    await page.addInitScript(() => {
      Object.defineProperty(window, 'EventSource', {value: class extends EventTarget {close() {}}});
    });
    await page.route('**/api/**', async route => {
      const url = new URL(route.request().url());
      let body: object;
      if (url.pathname === '/api/snapshot') body = {state: {accepting: true, active_turn_id: null, active_session_id: null,
        stopping: false, map_paused: false, service_status: 'available'}, cursor: 0, stream_id: 'trace-pages'};
      else if (url.pathname === '/api/sessions') body = {sessions: [{session_id: 'trip', title: '分页旅行', status: 'completed',
        updated_at: '2026-10-24T00:00:00Z', tool_error_count: 0}], next_cursor: null};
      else if (url.pathname.endsWith('/search')) body = {query: '西湖', matches: [{turn_id: 'turn-1', turn_ordinal: 1,
        object_id: 'turn-1', object_type: 'turn', payload_id: 'input-1', field: 'turn_input', excerpt: '西湖定位原文'}]};
      else if (url.pathname.startsWith('/api/payloads/')) body = {payload_id: 'input-1', offset: 0, total: 6,
        text: '西湖定位原文', next_offset: null};
      else body = {session: {title: '分页旅行'}, turns: url.searchParams.get('limit') === '1' ? [makeTurn(1)]
        : url.searchParams.has('before') ? [makeTurn(50), makeTurn(51)] : [makeTurn(51)],
        requests: [], tool_calls: [], events: [], usage_summary: {}, next_before: 50, total_turns: 51};
      await route.fulfill({json: {schema_version: 1, ...body}});
    });
    await page.goto('/');
    if (width < 760) await page.getByRole('button', {name: '打开会话导航', exact: true}).click();
    await page.getByRole('button', {name: /^分页旅行.*已完成/}).click();
    await page.getByRole('button', {name: '轨迹', exact: true}).click();
    await page.getByRole('button', {name: '加载更早轨迹', exact: true}).click();
    await expect(page.locator('.trace-turn')).toHaveCount(2);
    await expect(page.getByText('旅行需求51', {exact: true})).toHaveCount(1);
    await page.getByRole('textbox', {name: '搜索当前会话'}).fill('西湖');
    await page.getByRole('button', {name: '搜索', exact: true}).click();
    await page.getByRole('button', {name: /第 1 轮 · 对话轮次 · 用户输入/}).click();
    await expect(page.locator('.trace-turn')).toHaveCount(3);
    await expect(page.locator('pre').filter({hasText: '西湖定位原文'})).toBeVisible();
  });
}

test('切回已有分页缓存的会话后，仍可加载后台新增轮次之间的历史', async ({page}) => {
  let latest = 2;
  await page.addInitScript(() => {
    Object.defineProperty(window, 'EventSource', {value: class extends EventTarget {close() {}}});
  });
  const turn = (ordinal: number) => ({turn_id: `turn-${ordinal}`, ordinal, input: `历史需求${ordinal}`,
    status: 'completed', answer: '已保存回答', answer_source: 'model', tool_error_count: 0});
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    let body: object;
    if (url.pathname === '/api/snapshot') body = {state: {accepting: true, active_turn_id: null,
      active_session_id: null, stopping: false, map_paused: false, service_status: 'available'}, cursor: latest, stream_id: 'gap'};
    else if (url.pathname === '/api/sessions') body = {sessions: ['甲旅行', '乙旅行'].map((title, index) => ({session_id: `trip-${index}`,
      title, status: 'completed', updated_at: '2026-10-24T00:00:00Z', tool_error_count: 0})), next_cursor: null};
    else {
      const before = Number(url.searchParams.get('before'));
      body = {session: {title: '甲旅行'}, turns: before === 5 ? [turn(3), turn(4)] : before ? [turn(1)] : [turn(latest)],
        requests: [], tool_calls: [], events: [], cursor: latest, next_before: before ? (before === 5 ? 3 : null) : latest, total_turns: latest};
    }
    await route.fulfill({json: {schema_version: 1, ...body}});
  });
  await page.goto('/');
  await page.getByRole('button', {name: /^甲旅行.*已完成/}).click();
  await expect(page.getByText('历史需求2', {exact: true})).toBeVisible();
  await page.getByRole('button', {name: /^乙旅行.*已完成/}).click();
  latest = 5;
  await page.getByRole('button', {name: /^甲旅行.*已完成/}).click();
  await expect(page.getByText('历史需求5', {exact: true})).toBeVisible();
  await page.getByRole('button', {name: '加载更早对话', exact: true}).click();
  await expect(page.getByText('历史需求4', {exact: true})).toBeVisible();
});

test('另一页面删除其他会话时，当前会话的在途分页仍能完成', async ({page}) => {
  let release!: () => void;
  const held = new Promise<void>(resolve => {release = resolve;});
  let requested = false;
  await page.addInitScript(() => {
    Object.defineProperty(window, 'EventSource', {value: class extends EventTarget {
      constructor() {super(); (window as unknown as {source: EventTarget}).source = this;}
      close() {}
    }});
  });
  const turn = (ordinal: number) => ({turn_id: `turn-${ordinal}`, ordinal, input: `当前旅行需求${ordinal}`,
    status: 'completed', answer: '已保存回答', answer_source: 'model', tool_error_count: 0});
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    let body: object;
    if (url.pathname === '/api/snapshot') body = {state: {accepting: true, active_turn_id: null,
      active_session_id: null, stopping: false, map_paused: false, service_status: 'available'}, cursor: 0, stream_id: 'delete-other'};
    else if (url.pathname === '/api/sessions') body = {sessions: [{session_id: 'current', title: '当前旅行', status: 'completed',
      updated_at: '2026-10-24T00:00:00Z', tool_error_count: 0}], next_cursor: null};
    else {
      if (url.searchParams.has('before')) {requested = true; await held;}
      body = {session: {title: '当前旅行'}, turns: [turn(url.searchParams.has('before') ? 1 : 2)],
        requests: [], tool_calls: [], events: [], next_before: url.searchParams.has('before') ? null : 2, total_turns: 2};
    }
    await route.fulfill({json: {schema_version: 1, ...body}});
  });
  await page.goto('/');
  await page.getByRole('button', {name: /^当前旅行.*已完成/}).click();
  const more = page.getByRole('button', {name: '加载更早对话', exact: true});
  await more.click();
  await expect.poll(() => requested).toBe(true);
  await page.evaluate(() => (window as unknown as {source: EventTarget}).source.dispatchEvent(new MessageEvent('trace', {
    data: JSON.stringify({schema_version: 1, cursor: 1, event_id: 'deleted-other', kind: 'session.deleted', session_id: 'other'}),
  })));
  release();
  await expect(page.getByText('当前旅行需求1', {exact: true})).toBeVisible();
  await expect(more).toHaveCount(0);
});

test('迟到的搜索定位页不能把已完成的轨迹退回执行中', async ({page}) => {
  let completed = false, locating = false;
  let release!: () => void;
  const held = new Promise<void>(resolve => {release = resolve;});
  await page.addInitScript(() => {
    Object.defineProperty(window, 'EventSource', {value: class extends EventTarget {
      constructor() {super(); (window as unknown as {source: EventTarget}).source = this;}
      close() {}
    }});
  });
  const snapshot = (done: boolean) => ({schema_version: 1, session: {title: '真实状态'},
    turns: [{turn_id: 'turn', ordinal: 1, input: '西湖旅行', status: done ? 'completed' : 'running',
      reason: null, answer_source: 'model', tool_error_count: 0}], requests: [], tool_calls: [], events: [],
    cursor: done ? 12 : 10, next_before: null, total_turns: 1});
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/snapshot') await route.fulfill({json: {schema_version: 1, cursor: 10, stream_id: 'order',
      state: {accepting: true, active_turn_id: null, active_session_id: null, stopping: false, map_paused: false, service_status: 'available'}}});
    else if (url.pathname === '/api/sessions') await route.fulfill({json: {schema_version: 1, sessions: [
      {session_id: 'trip', title: '真实状态', status: 'completed', updated_at: '2026-10-24T00:00:00Z', tool_error_count: 0},
    ], next_cursor: null}});
    else if (url.pathname.endsWith('/search')) await route.fulfill({json: {schema_version: 1, query: '西湖', matches: [
      {turn_id: 'turn', turn_ordinal: 1, object_id: 'turn', object_type: 'turn', payload_id: null, field: 'turn_input', excerpt: '西湖旅行'},
    ]}});
    else if (url.searchParams.get('limit') === '1') {
      const oldPage = snapshot(false);
      locating = true; await held; await route.fulfill({json: oldPage});
    } else await route.fulfill({json: snapshot(completed)});
  });
  await page.goto('/');
  await page.getByRole('button', {name: /^真实状态.*已完成/}).click();
  await page.getByRole('button', {name: '轨迹', exact: true}).click();
  await page.getByRole('textbox', {name: '搜索当前会话'}).fill('西湖');
  await page.getByRole('button', {name: '搜索', exact: true}).click();
  await page.getByRole('button', {name: /第 1 轮 · 对话轮次 · 用户输入/}).click();
  await expect.poll(() => locating).toBe(true);
  completed = true;
  await page.evaluate(() => (window as unknown as {source: EventTarget}).source.dispatchEvent(new MessageEvent('trace', {
    data: JSON.stringify({schema_version: 1, cursor: 12, event_id: 'finished', kind: 'turn.finished'}),
  })));
  await expect(page.locator('.trace-turn > summary')).toContainText('已完成');
  const returned = page.waitForResponse(response => new URL(response.url()).searchParams.get('limit') === '1');
  release(); await (await returned).finished();
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  await expect(page.locator('.trace-turn > summary')).toContainText('已完成');
});

test('迟到的更早页不能关闭刷新后发现的历史缺口', async ({page}) => {
  let latest = 2, requested = false;
  let release!: () => void;
  const held = new Promise<void>(resolve => {release = resolve;});
  const turn = (ordinal: number) => ({turn_id: `turn-${ordinal}`, ordinal, input: `缺口需求${ordinal}`,
    status: 'completed', answer: '已保存回答', answer_source: 'model', tool_error_count: 0});
  await page.addInitScript(() => {
    Object.defineProperty(window, 'EventSource', {value: class extends EventTarget {
      constructor() {super(); (window as unknown as {source: EventTarget}).source = this;}
      close() {}
    }});
  });
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    let body: object;
    if (url.pathname === '/api/snapshot') body = {state: {accepting: true, active_turn_id: null,
      active_session_id: null, stopping: false, map_paused: false, service_status: 'available'}, cursor: 0, stream_id: 'frontier'};
    else if (url.pathname === '/api/sessions') body = {sessions: [{session_id: 'trip', title: '补齐旅行', status: 'completed',
      updated_at: '2026-10-24T00:00:00Z', tool_error_count: 0}], next_cursor: null};
    else {
      const before = Number(url.searchParams.get('before'));
      if (before === 2) {requested = true; await held;}
      body = {session: {title: '补齐旅行'}, turns: before === 5 ? [turn(3), turn(4)] : before ? [turn(1)] : [turn(latest)],
        requests: [], tool_calls: [], events: [], cursor: before === 2 ? 2 : latest,
        next_before: before ? (before === 5 ? 3 : null) : latest, total_turns: latest};
    }
    await route.fulfill({json: {schema_version: 1, ...body}});
  });
  await page.goto('/');
  await page.getByRole('button', {name: /^补齐旅行.*已完成/}).click();
  const more = page.getByRole('button', {name: '加载更早对话', exact: true});
  await more.click(); await expect.poll(() => requested).toBe(true);
  latest = 5;
  await page.evaluate(() => (window as unknown as {source: EventTarget}).source.dispatchEvent(new MessageEvent('trace', {
    data: JSON.stringify({schema_version: 1, cursor: 5, event_id: 'new-turns', kind: 'turn.finished'}),
  })));
  await expect(page.getByText('缺口需求5', {exact: true})).toBeVisible();
  release();
  await expect(page.getByText('缺口需求1', {exact: true})).toBeVisible();
  await expect(more).toBeEnabled();
  await more.click();
  await expect(page.getByText('缺口需求4', {exact: true})).toBeVisible();
});
