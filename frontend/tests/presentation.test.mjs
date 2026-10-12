import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import { test } from 'node:test';
import ts from 'typescript';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const require = createRequire(import.meta.url);
async function component(file, name) {
  const source = await readFile(new URL(`../src/${file}.tsx`, import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.ReactJSX } });
  const code = outputText.replace(/from ["']([^"']+)["']/g, (_, specifier) => `from "${pathToFileURL(require.resolve(specifier))}"`);
  return (await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`))[name];
}
const AnswerBody = await component('AnswerBody', 'AnswerBody');
const TraceTimeline = await component('TraceTimeline', 'TraceTimeline');
const answer = text => renderToStaticMarkup(createElement(AnswerBody, { text }));

test('重试等待和恢复有独立时间边界，未开始的重试不生成模型请求条', () => {
  const html = renderToStaticMarkup(createElement(TraceTimeline, {
    turns: [{turn_id: 'turn', ordinal: 1}], selected: null, onLocate() {}, tools: [],
    requests: [{request_id: 'failed', turn_id: 'turn', ordinal: 1, status: 'failed',
      started_at: '2026-10-12T02:00:00Z', finished_at: '2026-10-12T02:00:01Z'}],
    events: [{event_id: 'retry', turn_id: 'turn', request_id: 'failed', sequence: 3,
      kind: 'request.retry_scheduled', timestamp: '2026-10-12T02:00:01Z',
      data: {retry_id: 'chain', retry: 1, delay_ms: 500}}],
  }));
  assert.match(html, /重试 1 · 等待开始/);
  assert.equal((html.match(/class="timeline-bar model /g) ?? []).length, 1);
  assert.match(html, /查看 3 个已采集时间边界/);
});

test('回答呈现标题、列表、表格和代码，保留长正文尾部', () => {
  const html = answer('## 旅行安排\n\n- 西湖\n- 餐饮\n\n| 日期 | 地点 |\n| --- | --- |\n| 首日 | 西湖 |\n\n```json\n{"city":"杭州"}\n```\n\n' + '完整正文'.repeat(3000) + '末尾核对标记');
  assert.match(html, /<h2>旅行安排<\/h2>/);
  assert.match(html, /<li>西湖<\/li>/);
  assert.match(html, /<table>/);
  assert.match(html, /<pre><code/);
  assert.match(html, /末尾核对标记/);
});

test('模型文本不能生成可执行 HTML 或危险链接', () => {
  const html = answer('<script>alert(1)</script>\n\n<img src=x onerror=alert(1)>\n\n[危险](javascript:alert%281%29)\n\n[正常](https://example.com)');
  assert.doesNotMatch(html, /<script|<img|href="javascript:/);
  assert.match(html, /href="https:\/\/example.com"/);
});

test('Markdown 图片显示为链接，不自动请求外部图片', () => {
  const html = answer('![地图参考](https://example.com/map.png)');
  assert.doesNotMatch(html, /<img/);
  assert.match(html, /<a href="https:\/\/example.com\/map.png">地图参考<\/a>/);
});

test('预订入口的裸网址不包含后续中文说明', () => {
  const text = '- **预订入口**：湖北省博物馆官方微信公众号及官方网站（https://hbww.org.cn/）。官方界面正文未读取，请在放票周期内（提前5天）通过官方渠道按页面提示自行办理实名预约；余票未确认，不代为预约。';
  const html = answer(text);
  assert.match(html, /<a href="https:\/\/hbww.org.cn\/"[^>]*>https:\/\/hbww.org.cn\/<\/a>）。官方界面正文未读取/);
});

test('裸网址在中文句末标点处结束', () => {
  assert.equal(answer('https://hbww.org.cn/。说明'), '<div class="answer-body"><p><a href="https://hbww.org.cn/">https://hbww.org.cn/</a>。说明</p></div>');
});

test('中文标点分隔裸网址，保留说明、多个链接和中文路径', () => {
  for (const punctuation of ['，', '。', '；', '：', '！', '？', '、', '（', '）', '【', '】', '《', '》', '“', '”', '‘', '’']) {
    const html = answer(`https://example.com/路径?q=武汉${punctuation}说明`);
    assert.match(html, /href="https:\/\/example.com\/%E8%B7%AF%E5%BE%84\?q=%E6%AD%A6%E6%B1%89"/);
    assert.match(html, new RegExp(`</a>${punctuation}说明`));
  }
  const html = answer('www.example.com。说明 https://example.org/path。');
  assert.match(html, /href="http:\/\/www.example.com">www.example.com<\/a>。说明/);
  assert.match(html, /href="https:\/\/example.org\/path">https:\/\/example.org\/path<\/a>。/);
  assert.match(answer('https://example.com/?a=1&b=2。说明'), /href="https:\/\/example.com\/\?a=1&amp;b=2"[^>]*>.*<\/a>。说明/);
});

test('显式链接、自动链接语法和代码中的中文标点保持原样', () => {
  const url = 'https://example.com/路径（说明）。';
  const encoded = 'https://example.com/%E8%B7%AF%E5%BE%84%EF%BC%88%E8%AF%B4%E6%98%8E%EF%BC%89%E3%80%82';
  for (const text of [`[官网](${url})`, `<${url}>`, `[来源][official]\n\n[official]: ${url}`]) {
    assert.match(answer(text), new RegExp(`href="${encoded}"`));
  }
  assert.match(answer('https://example.com/%E3%80%82'), /href="https:\/\/example.com\/%E3%80%82"/);
  const code = answer('`https://hbww.org.cn/。说明`\n\n```text\nhttps://hbww.org.cn/。说明\n```');
  assert.doesNotMatch(code, /<a /);
  assert.match(code, /<code>https:\/\/hbww.org.cn\/。说明<\/code>/);
});

test('未结束调用没有伪造结束边界或区间', () => {
  const html = renderToStaticMarkup(createElement(TraceTimeline, {
    turns: [{turn_id: 'turn', ordinal: 1}], requests: [{request_id: 'request', turn_id: 'turn', ordinal: 1, status: 'running', started_at: '2026-10-09T02:00:00Z', finished_at: null}], tools: [], events: [], selected: null, onLocate() {},
  }));
  assert.match(html, /尚未结束/);
  assert.match(html, /width:0%/);
  assert.match(html, /请求开始/);
  assert.doesNotMatch(html, /请求结束/);
});

const ContextMaintenance = await component('ContextMaintenance', 'ContextMaintenance');
test('摘要结果、校验、轮内采用和保存供续聊分别显示，状态不泄露摘要正文', () => {
  const events = [
    {event_id:'1',turn_id:'turn',kind:'context.summary_started',data:{}},
    {event_id:'2',turn_id:'turn',kind:'context.summary_result',data:{status:'completed'}},
    {event_id:'3',turn_id:'turn',kind:'context.validated',data:{valid:true}},
    {event_id:'4',turn_id:'turn',kind:'context.adopted',data:{before_tokens:25000,after_tokens:19000}},
  ];
  const render = events => renderToStaticMarkup(createElement(ContextMaintenance, {events,turnId:'turn'}));
  assert.match(render(events), /摘要请求已返回，等待校验/);
  assert.match(render(events), /摘要校验通过/);
  assert.match(render(events), /摘要已用于本轮/);
  assert.doesNotMatch(render(events), /已保存供续聊/);
  const saved = render([...events,{event_id:'5',turn_id:'turn',kind:'context.saved',data:{version:1}}]);
  assert.match(saved, /摘要已保存供续聊/);
  assert.equal(render([{event_id:'other',turn_id:'other',kind:'context.saved'}]), '');
});

test('工具结果整理展示前后估算、处理项数与剩余压力', () => {
  const html = renderToStaticMarkup(createElement(ContextMaintenance, {turnId:'turn',events:[
    {event_id:'organized',turn_id:'turn',kind:'context.organized',data:{before_tokens:25000,after_tokens:23000,changed_result_count:2,pressure_remaining:true}}
  ]}));
  assert.match(html, /25000 → 23000 tokens/);
  assert.match(html, /整理 2 项/);
  assert.match(html, /仍有上下文压力/);
});

test('超限恢复区分准备、实际请求和返回，不把准备状态显示为已经恢复', () => {
  const render = events => renderToStaticMarkup(createElement(ContextMaintenance, {events,turnId:'turn'}));
  const prepared = [
    {event_id:'exceeded',turn_id:'turn',kind:'context.exceeded'},
    {event_id:'prepared',turn_id:'turn',kind:'context.recovery_prepared',data:{before_tokens:20000,after_tokens:15000,reduced:true,allowed:true}},
  ];
  assert.match(render(prepared), /服务报告上下文超限/);
  assert.match(render(prepared), /已缩小且符合预算/);
  assert.doesNotMatch(render(prepared), /正在恢复原模型步骤|已返回有效响应/);
  assert.match(render([...prepared,{event_id:'started',turn_id:'turn',kind:'context.recovery_started'}]), /正在恢复原模型步骤/);
  assert.match(render([...prepared,{event_id:'result',turn_id:'turn',kind:'context.recovery_result',data:{status:'completed'}}]), /原模型步骤已返回有效响应/);
  assert.match(render([{event_id:'blocked',turn_id:'turn',kind:'context.recovery_prepared',data:{reduced:false,allowed:true}}]), /未获得预算内的有效缩减/);
});
