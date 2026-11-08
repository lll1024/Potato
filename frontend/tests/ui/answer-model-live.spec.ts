import { expect, test } from '@playwright/test';
import { writeFile } from 'node:fs/promises';

test.use({ignoreHTTPSErrors: true, video: 'on'});

// 由 iOS 原界面发起，两端在真实准备轮之后进入同一会话，再观测完整目标轮。
test('当前配置模型在 Web 与 iOS 联合验收中真实分批显示', async ({page}) => {
  test.skip(process.env.POTATO_LIVE_ANSWER !== '1', '仅在显式真实模型联合验收时运行');
  test.setTimeout(900_000);
  const url = 'https://localhost:18499';
  const get = async (path: string) => (await page.request.get(url + path)).json();
  const mark = async (key: string, data: object) => page.request.post(url + '/__acceptance__/coordination/' + key, {data});
  const environment = await get('/__acceptance__/environment');
  expect(environment.kind).toBe('configured-model-answer-acceptance');
  await page.addInitScript(() => {
    const evidence = {receipts: [] as object[], rendered: [] as object[]};
    (window as any).__liveEvidence = evidence;
    const Native = window.EventSource;
    window.EventSource = class extends Native {
      constructor(address: string | URL, options?: EventSourceInit) {
        super(address, options);
        for (const kind of ['answer.preview', 'progress.preview', 'service.state']) {
          this.addEventListener(kind, event => {
            const notice = JSON.parse((event as MessageEvent).data);
            const value = kind === 'service.state' ? notice.state : notice.preview;
            evidence.receipts.push({epoch_ms: Date.now(), monotonic_ms: performance.now(), kind,
              content: value == null ? null : {
                instance_id: value.instance_id, stream_id: value.stream_id, session_id: value.session_id,
                turn_id: value.turn_id, revision: value.revision, attempt_id: value.attempt_id,
                status: value.status, delivery: value.delivery, text_chars: value.text?.length,
                entries: value.entries?.map((entry: any) => ({id: entry.id, attempt_id: entry.attempt_id,
                  kind: entry.kind, status: entry.status, text_chars: entry.text.length})),
                active_turn_id: value.active_turn_id,
              }});
          });
        }
      }
    };
    let previous = '';
    let heartbeat = 0;
    const sample = () => {
      const turn = document.querySelector('section[aria-label="第 2 轮对话"]');
      const body = turn?.querySelector<HTMLElement>('.message.answer .answer-body');
      const progress = turn?.querySelector<HTMLDetailsElement>('.query-progress');
      const text = body?.innerText ?? '';
      const query = progress?.open ? Array.from(progress.querySelectorAll<HTMLElement>('li > span')).map(item => item.innerText).join('\n') : '';
      const status = progress?.open ? Array.from(progress.querySelectorAll<HTMLElement>('li > small')).map(item => item.innerText) : [];
      const scroll = document.querySelector('.messages');
      const state = {rendered_document_chars: text.length, rendered_progress_chars: query.length,
        progress_open: progress?.open ?? null, progress_statuses: status,
        copy_label: turn?.querySelector('.message.answer button')?.textContent ?? null,
        scroll_top: scroll?.scrollTop ?? null, scroll_height: scroll?.scrollHeight ?? null,
        viewport_height: scroll?.clientHeight ?? null};
      const serial = JSON.stringify(state);
      const now = performance.now();
      if (serial !== previous || now - heartbeat >= 100) {
        evidence.rendered.push({epoch_ms: Date.now(), monotonic_ms: now, time_origin: performance.timeOrigin, ...state});
        previous = serial; heartbeat = now;
      }
      requestAnimationFrame(sample);
    };
    requestAnimationFrame(sample);
  });
  await page.goto(url);
  let coordination: any;
  await expect.poll(async () => {
    coordination = await get('/__acceptance__/coordination');
    return coordination.ios_session?.session_id;
  }, {timeout: 240_000}).toBeTruthy();
  const id = coordination.ios_session.session_id;
  const session = page.locator('.history-select').first();
  await expect(session).toBeVisible({timeout: 30_000});
  await session.click();
  await expect(page.locator('section[aria-label="第 1 轮对话"] .answer-body')).toBeVisible();
  await mark('web_ready', {session_id: id, at: new Date().toISOString()});
  let saved: any;
  await expect.poll(async () => {
    saved = await get('/api/sessions/' + id + '?summary=false&include_messages=false');
    return saved.turns[1]?.status;
  }, {timeout: 600_000, intervals: [500]}).not.toBe(undefined);
  const target = saved.turns[1].turn_id;
  await expect.poll(async () => {
    saved = await get('/api/sessions/' + id + '?summary=false&include_messages=false');
    return saved.turns[1].status;
  }, {timeout: 600_000, intervals: [500]}).not.toBe('running');
  const turn = saved.turns[1];
  expect(turn.status).toBe('completed');
  await expect(page.locator('section[aria-label="第 2 轮对话"]').getByRole('button', {name: '复制完整回答', exact: true})).toBeVisible();
  await page.waitForTimeout(300);
  const timeline = await page.evaluate(() => (window as any).__liveEvidence);
  const samples = timeline.rendered.filter((sample: any) => sample.rendered_document_chars > 0);
  expect(samples.length).toBeGreaterThan(2);
  expect(timeline.receipts.some((item: any) => item.kind === 'answer.preview' && item.content?.turn_id === target && item.content.text_chars > 0)).toBeTruthy();
  const query = page.locator('section[aria-label="第 2 轮对话"] .query-progress');
  if (environment.scenario === 'query') {
    expect(turn.query_progress.length).toBeGreaterThan(0);
    expect(saved.tool_calls.filter((item: any) => item.turn_id === target).length).toBeGreaterThan(0);
    await expect(query).not.toHaveAttribute('open');
    expect(timeline.rendered.some((item: any) => item.rendered_progress_chars > 0)).toBeTruthy();
    await query.locator('summary').click();
    await expect(query).toHaveAttribute('open');
  } else {
    expect(turn.query_progress).toEqual([]);
    expect(saved.tool_calls.filter((item: any) => item.turn_id === target)).toEqual([]);
  }
  await page.screenshot({path: test.info().outputPath('live-saved-expanded.png')});
  // 系统剪贴板由两端共享：等待手机完成采样，再先由Web复制，最后交手机。
  await expect.poll(async () => (await get('/__acceptance__/coordination')).ios_saved, {timeout: 120_000}).toBeTruthy();
  await page.context().grantPermissions(['clipboard-read', 'clipboard-write']);
  await page.locator('section[aria-label="第 2 轮对话"]').getByRole('button', {name: '复制完整回答', exact: true}).click();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(turn.answer);
  await mark('web_copied', {turn_id: target, markdown_equal: true});
  await page.reload();
  await page.locator('.history-select').first().click();
  const history = await get('/api/sessions/' + id + '?summary=false&include_messages=false');
  expect(history.turns[1].query_progress).toEqual(turn.query_progress);
  expect(history.turns[1].answer).toBe(turn.answer);
  if (environment.scenario === 'query') {
    const reopened = page.locator('section[aria-label="第 2 轮对话"] .query-progress');
    await expect(reopened).not.toHaveAttribute('open');
    await reopened.locator('summary').click();
    await expect(reopened).toContainText('已保存的查询进展');
  }
  await page.screenshot({path: test.info().outputPath('live-reopened.png')});
  const evidence = test.info().outputPath('live-evidence.json');
  await writeFile(evidence, JSON.stringify({environment, session_id: id, turn_id: target, timeline,
    outcome: turn, requests: history.requests, tools: history.tool_calls.map((item: any) => ({
      tool_call_id: item.tool_call_id, turn_id: item.turn_id, name: item.name, status: item.status})),
    copy_markdown_equal: true, reloaded_progress_equal: true}, null, 2));
  await test.info().attach('真实模型与 Web 全程证据', {path: evidence, contentType: 'application/json'});
  await mark('web_finished', {turn_id: target, at: new Date().toISOString()});
});
