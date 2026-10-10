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

test('未结束调用没有伪造结束边界或区间', () => {
  const html = renderToStaticMarkup(createElement(TraceTimeline, {
    turns: [{turn_id: 'turn', ordinal: 1}], requests: [{request_id: 'request', turn_id: 'turn', ordinal: 1, status: 'running', started_at: '2026-10-09T02:00:00Z', finished_at: null}], tools: [], events: [], selected: null, onLocate() {},
  }));
  assert.match(html, /尚未结束/);
  assert.match(html, /width:0%/);
  assert.match(html, /请求开始/);
  assert.doesNotMatch(html, /请求结束/);
});
