/** A failed response does not prove that a submitted write was rolled back. */
export function transportFailure(error, method) {
  const timeout = error?.name === 'AbortError'
  const invalid = error?.name === 'SyntaxError'
  if (!timeout && !invalid && error?.name !== 'TypeError') return error
  const write = !['GET', 'HEAD'].includes(method)
  const reason = timeout ? '请求超时' : invalid ? '无法读取服务器响应' : '网络连接中断'
  const result = new Error(write
    ? `${reason}，结果暂时无法确认。请先核对已保存内容，再决定是否重试。`
    : `${reason}，请检查网络后重试。`, { cause: error })
  result.name = timeout ? 'AbortError' : invalid ? 'InvalidResponseError' : 'NetworkError'
  result.code = timeout ? 'REQUEST_TIMEOUT' : invalid ? 'INVALID_RESPONSE' : 'NETWORK_ERROR'
  result.outcomeUnknown = write
  return result
}
