export async function requestJson<T>(path: string, failureMessage: string, options?: RequestInit,
  versionMessage = '数据版本不兼容，请更新页面和服务。'): Promise<T> {
  const response = await fetch(path, options);
  const body = await response.json();
  if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : body.detail?.message ?? failureMessage);
  if (body.schema_version !== 1) throw new Error(versionMessage);
  return body;
}
