const {createRequire}=require('node:module');
const fs=require('node:fs/promises');
const path=require('node:path');
const requireHere=createRequire(path.join(process.cwd(),'frontend/package.json'));
const {chromium,expect}=requireHere('@playwright/test');
(async()=>{
 const out='/tmp/potato117-query-confirmed-87-readonly-web';await fs.mkdir(out,{recursive:true});
 const browser=await chromium.launch({channel:'chrome'});
 const context=await browser.newContext({ignoreHTTPSErrors:true,recordVideo:{dir:out},permissions:['clipboard-read','clipboard-write']});
 const page=await context.newPage();const base='https://localhost:18499';let evidence={mode:'same-saved-session-readonly-no-input'};
 try {
  const get=async p=>(await context.request.get(base+p)).json();
  const list=await get('/api/sessions');const id=list.sessions[0].session_id;
  const history=await get('/api/sessions/'+id+'?summary=false&include_messages=false');const turn=history.turns[1];
  evidence={...evidence,environment:await get('/__acceptance__/environment'),session_id:id,turn_id:turn.turn_id,status:turn.status,reason:turn.reason,answer_chars:turn.answer.length};
  expect(turn.status).toBe('terminated');expect(turn.reason).toBe('output_limit');
  await page.goto(base);await page.locator('.history-select').first().click();
  const region=page.locator('section[aria-label="第 2 轮对话"]');
  await expect(region.getByRole('button',{name:'复制不完整回答',exact:true})).toBeVisible();
  const query=region.locator('.query-progress');await expect(query).not.toHaveAttribute('open');await query.locator('summary').click();
  await expect(query).toContainText('已保存的查询进展');await page.screenshot({path:path.join(out,'saved-partial-expanded.png')});
  // 原生旧观察器先落盘全程AX，再交Web实际复制，不能伪造成功屏障。
  await expect.poll(async()=>Boolean((await get('/__acceptance__/coordination')).ios_saved),{timeout:600000,intervals:[500]}).toBe(true);
  await region.getByRole('button',{name:'复制不完整回答',exact:true}).click();
  expect(await page.evaluate(()=>navigator.clipboard.readText())).toBe(turn.answer);
  evidence.copy_markdown_equal=true;
  await context.request.post(base+'/__acceptance__/coordination/web_copied',{data:{turn_id:turn.turn_id,markdown_equal:true,outcome:'output_limit',observer:'saved-readonly-supplement'}});
  await page.reload();await page.locator('.history-select').first().click();
  const again=await get('/api/sessions/'+id+'?summary=false&include_messages=false');expect(again.turns[1]).toEqual(turn);
  const reopened=page.locator('section[aria-label="第 2 轮对话"] .query-progress');await expect(reopened).not.toHaveAttribute('open');await reopened.locator('summary').click();await expect(reopened).toContainText('已保存的查询进展');
  await page.screenshot({path:path.join(out,'reopened-partial.png')});
  evidence.reloaded_answer_and_progress_equal=true;evidence.requests_unchanged=again.requests.length===history.requests.length;evidence.tools_unchanged=again.tool_calls.length===history.tool_calls.length;
  expect(evidence.requests_unchanged).toBe(true);expect(evidence.tools_unchanged).toBe(true);
 } finally {await fs.writeFile(path.join(out,'evidence.json'),JSON.stringify(evidence,null,2));await context.close();await browser.close();}
 console.log('saved readonly copy/reload/progress verified; no new input');
})().catch(e=>{console.error(e);process.exitCode=1;});
