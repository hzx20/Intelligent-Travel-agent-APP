/** An account-scoped, immutable creation attempt survives reload in this tab. */
const PREFIX = 'travel.trip-creation.v1.'
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
function key(userId) {
  if (!Number.isSafeInteger(userId) || userId <= 0) throw new Error('请先确认当前账号')
  return PREFIX+userId
}
function validated(value) {
  const p = value?.payload
  if (!uuid.test(value?.request_id || '') || !p || typeof p.title !== 'string' || !p.title.trim() || p.title.length > 100 ||
      typeof p.destination !== 'string' || p.destination.length > 100 || !Number.isInteger(p.days) || p.days < 1 || p.days > 30 ||
      !(p.start_date === null || (typeof p.start_date === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(p.start_date)))) {
    throw new Error('无法恢复上次创建信息，请先在“我的旅行”核对，避免重复创建。')
  }
  return Object.freeze({ request_id: value.request_id, payload: Object.freeze({
    title:p.title, destination:p.destination, days:p.days, start_date:p.start_date,
  }) })
}
export function newCreationAttempt(payload) {
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6]&15)|64; bytes[8] = (bytes[8]&63)|128
  const hex = [...bytes].map(b => b.toString(16).padStart(2,'0')).join('')
  return validated({request_id:`${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`,payload})
}
export function readCreationAttempt(userId) {
  const raw = sessionStorage.getItem(key(userId))
  if (!raw) return null
  let value
  try { value = JSON.parse(raw) } catch { throw new Error('无法恢复上次创建信息，请先在“我的旅行”核对，避免重复创建。') }
  if (value.schema !== 1 || value.user_id !== userId) throw new Error('上次创建信息与当前账号不一致，请先核对我的旅行。')
  return validated(value)
}
export function saveCreationAttempt(userId, attempt) {
  const value = validated(attempt)
  try { sessionStorage.setItem(key(userId), JSON.stringify({schema:1,user_id:userId,...value})) }
  catch { throw new Error('无法保留这次创建信息，请检查浏览器存储空间后重试。') }
}
export function clearCreationAttempt(userId, requestId) {
  const current = readCreationAttempt(userId)
  if (current?.request_id === requestId) sessionStorage.removeItem(key(userId))
}
