import { expect, test } from '@playwright/test';
import { writeFile } from 'node:fs/promises';

test.use({ignoreHTTPSErrors:true});
test('真实HTTPS页面断线恢复进展和正文，保存后刷新及第二客户端读取一致', async ({page, request, browser}) => {
  test.skip(process.env.POTATO_RECOVERY_UI !== '1', '需显式启动本次 HTTPS 外部连接验收服务');
  test.setTimeout(90000);
  const base = 'https://localhost:18497';
  const control = async (action: string) => expect((await request.post(`${base}/__test__/${action}`)).ok()).toBe(true);
  await control('arm?mode=query-progress');
  await page.setViewportSize({width:1440,height:900});
  const connected = page.waitForRequest(value => new URL(value.url()).pathname === "/api/events");
  await page.goto(base);
  await connected;
  const input = `双端断线恢复查询进展 ${Date.now()}`;
  const accepted = await (await request.post(`${base}/api/turns`, {data:{input}})).json();
  expect((await request.get(`${base}/__test__/entered`)).ok()).toBe(true);
  await page.locator('.history-select').filter({hasText:input}).click();
  const progress = page.getByRole('region',{name:'查询进展',exact:true});
  const samples: object[] = [];
  async function observe(stage: string) {
    const snapshot = await (await request.get(`${base}/api/snapshot`)).json();
    samples.push({stage,time:new Date().toISOString(),session_id:accepted.session_id,turn_id:accepted.turn_id,
      instance_id:snapshot.instance_id,stream_id:snapshot.stream_id,answer_revision:snapshot.answer_revision,
      progress_revision:snapshot.progress_revision,answer_chars:snapshot.answer_preview?.text.length ?? 0,
      progress:snapshot.progress_preview?.entries ?? null,rendered_answer:await page.locator('.message.answer .answer-body').allTextContents()});
  }
  await control('step');
  await expect(progress).toContainText('准备查找西湖');
  await observe('query-before-disconnect');
  await page.context().setOffline(true);
  await control('step');
  await page.context().setOffline(false);
  await expect(progress).toContainText('准备查找西湖的地点资料。');
  await observe('query-restored');
  await page.screenshot({path:test.info().outputPath('query-restored.png')});
  await control('step');
  await control('step');
  await expect(page.locator('.message.answer .answer-body')).toContainText('实时杭州旅行');
  await expect(progress).not.toHaveAttribute('open','');
  await observe('answer-before-disconnect');
  await page.context().setOffline(true);
  for (let index=0; index<3; index++) await control('step');
  const historyURL = `${base}/api/sessions/${accepted.session_id}?summary=false&include_messages=false`;
  await expect.poll(async () => (await (await request.get(historyURL)).json()).turns[0].status).toBe('completed');
  await page.context().setOffline(false);
  await expect(page.getByRole('button',{name:'复制完整回答',exact:true})).toBeVisible();
  await observe('saved-while-offline');
  const history = await (await request.get(historyURL)).json();
  const turn = history.turns.find((item: {turn_id:string}) => item.turn_id === accepted.turn_id);
  expect(history.tool_calls).toHaveLength(1);
  expect(history.requests).toHaveLength(3);
  await page.reload();
  await page.locator('.history-select').filter({hasText:input}).click();
  await progress.locator('summary').click();
  await expect(progress.locator('li > span')).toHaveText(turn.query_progress.map((entry:{text:string}) => entry.text));
  await page.screenshot({path:test.info().outputPath('saved-after-refresh.png')});
  const second = await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:390,height:844}});
  try {
    const other = await second.newPage();
    await other.goto(base);
    await other.getByRole('button',{name:'打开会话导航',exact:true}).click();
    await other.locator('.history-select').filter({hasText:input}).click();
    const restored = other.getByRole('region',{name:'查询进展',exact:true});
    await restored.locator('summary').click();
    await expect(restored.locator('li > span')).toHaveText(turn.query_progress.map((entry:{text:string}) => entry.text));
    await other.screenshot({path:test.info().outputPath('second-client-history.png')});
  } finally { await second.close(); }
  expect((await (await request.get(historyURL)).json()).requests).toHaveLength(3);
  await writeFile(test.info().outputPath('recovery-observations.json'),JSON.stringify({samples,query_progress:turn.query_progress,
    answer:turn.answer,tool_count:history.tool_calls.length,request_count:history.requests.length},null,2));
});
