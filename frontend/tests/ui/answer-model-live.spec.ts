import { expect, test } from '@playwright/test';
import { writeFile } from 'node:fs/promises';

test.use({ignoreHTTPSErrors: true});

// 由 iOS 界面发起一轮，两端同时观察同一真实模型回答；普通回归不调用付费服务。
test('当前配置模型在 Web 与 iOS 联合验收中真实分批显示', async ({page}) => {
  test.skip(process.env.POTATO_LIVE_ANSWER !== '1', '仅在显式真实模型联合验收时运行');
  test.setTimeout(240_000);
  const url = 'https://localhost:18499';
  const environment = await (await page.request.get(url + '/__acceptance__/environment', {ignoreHTTPSErrors: true})).json();
  expect(environment.kind).toBe('configured-model-answer-acceptance');
  await page.goto(url);
  const session = page.locator('.history-select').first();
  await expect(session).toBeVisible({timeout: 150_000});
  await session.click();
  const answer = page.locator('.answer-body').first();
  const observations: object[] = [];
  let previous = '';
  await expect(async () => {
    const displayed = await answer.innerText();
    if (displayed.length <= previous.length + 20) throw new Error('等待可见正文继续增长');
    const snapshot = await (await page.request.get(url + '/api/snapshot', {ignoreHTTPSErrors: true})).json();
    const history = await (await page.request.get(url + '/api/sessions/' + snapshot.active_session_id, {ignoreHTTPSErrors: true})).json();
    expect(snapshot.answer_preview.status).toBe('generating');
    expect(snapshot.answer_preview.delivery).toBe('stream');
    expect(history.turns[0].status).toBe('running');
    expect(history.turns[0].answer).toBeNull();
    expect(history.requests.filter((request: {status: string}) => request.status === 'running')).toHaveLength(1);
    observations.push({at: new Date().toISOString(), session_id: snapshot.active_session_id,
      turn_id: snapshot.active_turn_id, revision: snapshot.answer_preview.revision,
      displayed_chars: displayed.length, preview_chars: snapshot.answer_preview.text.length,
      history_status: history.turns[0].status, history_answer: history.turns[0].answer});
    previous = displayed;
    await page.screenshot({path: test.info().outputPath(`live-batch-${observations.length}.png`)});
    expect(observations.length).toBeGreaterThanOrEqual(2);
  }).toPass({timeout: 120_000, intervals: [300]});
  const sessions = await (await page.request.get(url + '/api/sessions', {ignoreHTTPSErrors: true})).json();
  const id = sessions.sessions[0].session_id;
  await expect.poll(async () => (await (await page.request.get(url + '/api/sessions/' + id,
    {ignoreHTTPSErrors: true})).json()).turns[0].status, {timeout: 120_000}).toBe('completed');
  await expect(page.getByRole('button', {name: '复制完整回答', exact: true})).toBeVisible();
  const saved = await (await page.request.get(url + '/api/sessions/' + id, {ignoreHTTPSErrors: true})).json();
  await page.context().grantPermissions(['clipboard-read', 'clipboard-write']);
  await page.getByRole('button', {name: '复制完整回答', exact: true}).click();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(saved.turns[0].answer);
  const evidence = test.info().outputPath('live-evidence.json');
  const {turn_id, status, reason, answer: text, answer_source, created_at, finished_at} = saved.turns[0];
  await writeFile(evidence, JSON.stringify({environment, observations,
    outcome: {turn_id, status, reason, answer: text, answer_source, created_at, finished_at}, requests: saved.requests}, null, 2));
  await test.info().attach('真实模型与 Web 多批正文证据', {path: evidence, contentType: 'application/json'});
  await page.screenshot({path: test.info().outputPath('live-saved.png')});
});
