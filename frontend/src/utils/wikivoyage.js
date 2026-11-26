export const WIKIVOYAGE_HOST = 'zh.wikivoyage.org'
export const WIKIVOYAGE_LICENSE = 'https://creativecommons.org/licenses/by-sa/4.0/'
export const wikivoyageRevisionUrl = revision => 'https://' + WIKIVOYAGE_HOST + '/w/index.php?oldid=' + revision
const invalidLink = '请粘贴中文维基导游的 HTTPS 目的地页面链接'
const invalidResult = '维基导游返回的攻略无法识别，请稍后重试'

function titleOf(raw) {
  const title = raw.replaceAll('_', ' ').trim().normalize('NFC')
  if (!title || title.length > 160 || /[:%?#\\\p{C}]/u.test(title.normalize('NFKC'))
    || title.split('/').some(part => !part || part === '.' || part === '..')) throw Error(invalidLink)
  return title
}

export function wikivoyageTitle(value) {
  try {
    const url = new URL(value)
    const prefix = url.pathname.match(/^\/(?:wiki|zh(?:-(?:hans|hant|cn|hk|tw|mo|sg|my))?)\//)
    if (url.protocol === 'https:' && url.host.toLowerCase() === WIKIVOYAGE_HOST
      && !url.username && !url.password && !url.search && prefix) {
      return titleOf(decodeURIComponent(url.pathname.slice(prefix[0].length)))
    }
  } catch {
    // Invalid URLs and encoded titles use the same UI message.
  }
  throw Error(invalidLink)
}

async function boundedJson(response) {
  if (!response.ok || !response.body) throw Error('当前浏览器无法读取维基导游')
  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8', { fatal: true })
  let received = 0
  let text = ''
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) return JSON.parse(text + decoder.decode())
      received += value.byteLength
      if (received > 512 * 1024) {
        await reader.cancel()
        throw Error(invalidResult)
      }
      text += decoder.decode(value, { stream: true })
    }
  } finally {
    reader.releaseLock()
  }
}

export async function readWikivoyage(title) {
  const url = new URL('https://' + WIKIVOYAGE_HOST + '/w/api.php')
  url.search = new URLSearchParams({
    action: 'parse', page: title, prop: 'text|revid', format: 'json', formatversion: '2',
    variant: 'zh-hans', disableeditsection: '1', origin: '*',
  })
  const response = await fetch(url, {
    credentials: 'omit', redirect: 'error', referrerPolicy: 'no-referrer',
    signal: AbortSignal.timeout(10000),
    headers: { 'Api-User-Agent': 'IntelligentTravelAgent/1.0 (https://134.175.137.18/travel/)' },
  })
  const page = (await boundedJson(response))?.parse
  if (!page || typeof page.title !== 'string' || !Number.isSafeInteger(page.revid)
    || page.revid < 1 || typeof page.text !== 'string') throw Error(invalidResult)
  const returnedTitle = titleOf(page.title)
  if (returnedTitle.toLowerCase() !== title.toLowerCase()) throw Error(invalidResult)

  // Template content stays inert and is never attached to the page.
  const template = document.createElement('template')
  template.innerHTML = page.text
  const names = new Map()
  for (const node of template.content.querySelectorAll('span.listing-name')) {
    for (const hidden of node.querySelectorAll('script, style, template')) hidden.remove()
    const name = node.textContent.trim().replace(/\s+/gu, ' ')
    if (name && name.length <= 160 && !/\p{C}/u.test(name)) {
      const key = name.normalize('NFKC').toLowerCase()
      if (!names.has(key)) names.set(key, name)
      if (names.size === 50) break
    }
  }
  return { title: returnedTitle, revision: page.revid, names: [...names.values()] }
}
