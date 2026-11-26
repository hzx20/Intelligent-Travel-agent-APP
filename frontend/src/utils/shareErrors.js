export function shareError(error, accepting = false) {
  if ([404, 422].includes(error?.status)) return { message: '链接无效、已撤销或已过期，请向分享者重新获取。', retryable: false }
  if (error?.name === 'AbortError') return { message: accepting ? '请求超时，可能已经加入；可重试确认。' : '打开分享超时，请重试。', retryable: true }
  return { message: accepting ? '暂时无法确认是否已加入，请检查网络后重试。' : '暂时无法打开分享，请检查网络后重试。', retryable: true }
}
