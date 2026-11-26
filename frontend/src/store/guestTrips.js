/** Local-only trips. Importing into an account is an explicit, non-destructive copy. */
import { hasRouteCoordinates, isRouteMode, routeLegKey, routePairs, routePoint } from '../utils/tripRouteSummary.js'

const PREFIX = 'travel.guest.trip.v1.'
const SCHEMA = 1
const STATUS = new Set(['planning', 'upcoming', 'completed'])
const TIME = /^$|^(?:[01]\d|2[0-3]):[0-5]\d$/

export class GuestTripError extends Error {
  constructor(message, status = 400) { super(message); this.name = 'GuestTripError'; this.status = status }
}

export function isGuestTripId(id) { return typeof id === 'string' && /^guest-[a-zA-Z0-9-]+$/.test(id) }
export function isGuestTripStorageChange(event, id) {
  return isGuestTripId(id) && (event.key === null || event.key === PREFIX + id)
}

function copy(value) { return JSON.parse(JSON.stringify(value)) }
function fail(message, status = 422) { throw new GuestTripError(message, status) }
const locatedPairs = day => routePairs(day.items).filter(({ start, end }) => hasRouteCoordinates(start) && hasRouteCoordinates(end))
const legIdentity = (start, end) => JSON.stringify([routePoint(start), routePoint(end)])
function retainRoutePreferences(trip, previous) {
  for (const day of trip.days) {
    if (!Object.keys(day.route_preferences || {}).length) continue
    const prior = previous.get(day.id)
    if (!prior) continue
    day.route_preferences = Object.fromEntries(locatedPairs(day).flatMap(({ start, end }) => {
      const key = routeLegKey(start, end), mode = day.route_preferences[key]
      return isRouteMode(mode) && prior.get(key) === legIdentity(start, end) ? [[key, mode]] : []
    }))
  }
}
function walkingPreferences(input) {
  if (input === null) return {}
  const flags = ['prefer_short_walks', 'check_climbs']
  if (!input || typeof input !== 'object' || Array.isArray(input) ||
      Object.keys(input).some(key => ![...flags, 'daily_limit_m'].includes(key))) fail('步行偏好格式不正确')
  const preferences = {}
  for (const flag of flags) {
    if (flag in input && typeof input[flag] !== 'boolean') fail('步行偏好格式不正确')
    if (input[flag]) preferences[flag] = true
  }
  if (input.daily_limit_m != null) {
    preferences.daily_limit_m = integer(input.daily_limit_m, '步行上限', 1, 100000)
  }
  return preferences
}
function text(value, label, max, { required = false } = {}) {
  if (typeof value !== 'string' || value.length > max || (required && !value.trim())) fail(`${label}不正确`)
  return value
}
function dateOrNull(value) {
  if (value == null || value === '') return null
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value) || value.startsWith('0000-')) fail('出发日期不正确')
  const parsed = new Date(`${value}T00:00:00Z`)
  if (Number.isNaN(parsed.valueOf()) || parsed.toISOString().slice(0, 10) !== value) fail('出发日期不正确')
  return value
}
function integer(value, label, min, max) {
  if (!Number.isInteger(value) || value < min || value > max) fail(`${label}不正确`)
  return value
}
function coordinates(data) {
  const lng = data.lng ?? null, lat = data.lat ?? null
  if ((lng === null) !== (lat === null) || (lng !== null && (!Number.isFinite(lng) || !Number.isFinite(lat) || lng < -180 || lng > 180 || lat < -90 || lat > 90))) fail('经纬度不正确')
  return { lng, lat }
}
function itemFields(input) {
  const time = text(input.start_time ?? '', '开始时间', 5)
  if (!TIME.test(time)) fail('开始时间不正确')
  const duration = input.duration_min ?? null
  if (duration !== null) integer(duration, '停留时间', 0, 1440)
  return {
    name: text(input.name, '名称', 160, { required: true }),
    kind: text(input.kind ?? '地点', '类型', 30), start_time: time, duration_min: duration,
    address: text(input.address ?? '', '地址', 300), ...coordinates(input),
    amap_id: text(input.amap_id ?? '', '地点标识', 100), note: text(input.note ?? '', '备注', 4000),
    activity: text(input.activity ?? '', '活动说明', 4000), source: text(input.source ?? '', '来源', 200),
  }
}
function placeFields(input) {
  return { name: text(input.name, '名称', 160, { required: true }), address: text(input.address ?? '', '地址', 300),
    ...coordinates(input), amap_id: text(input.amap_id ?? '', '地点标识', 100),
    note: text(input.note ?? '', '备注', 4000), source: text(input.source ?? '', '来源', 200) }
}
function reservationFields(input) {
  if (!['住宿', '交通', '门票', '其他'].includes(input.kind)) fail('预订类型不正确')
  if (!['待预订', '已预订', '已取消'].includes(input.status)) fail('预订状态不正确')
  const start_date = dateOrNull(input.start_date), end_date = dateOrNull(input.end_date)
  if (start_date && end_date && end_date < start_date) fail('结束日期不能早于开始日期')
  const item_id = input.item_id == null || input.item_id === '' ? null : integer(input.item_id, '行程项目标识', 1, Number.MAX_SAFE_INTEGER)
  return { kind: input.kind, title: text(input.title, '名称', 160, {required: true}).trim(), status: input.status,
    start_date, end_date, item_id, reference: text(input.reference ?? '', '订单号', 150).trim(), note: text(input.note ?? '', '备注', 2000).trim() }
}
function combineGuideText(old, addition, max) {
  old = text(old ?? '', '已有内容', max).trim(); addition = addition.trim()
  if (!addition || addition === old || old.split('\n\n').includes(addition)) return old
  const result = old ? `${old}\n\n${addition}` : addition
  if (result.length > max) fail('合并后的备注或来源过长，请缩短后再导入')
  return result
}
function aiKeys(value, allowed, required = []) {
  if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).some(key => !allowed.includes(key)) || required.some(key => !Object.hasOwn(value, key))) fail('AI 建议格式不正确')
}

function newId() {
  if (globalThis.crypto?.randomUUID) return `guest-${globalThis.crypto.randomUUID()}`
  if (!globalThis.crypto?.getRandomValues) fail('浏览器无法生成安全的本地旅行标识', 503)
  const bytes = new Uint8Array(16); globalThis.crypto.getRandomValues(bytes)
  return `guest-${Array.from(bytes, x => x.toString(16).padStart(2, '0')).join('')}`
}

/**
 * Async methods mirror trip API semantics and return detached data copies.
 * `options.storage`, `options.locks`, `options.makeId` and `options.now` permit isolated tests.
 * Writes are serialized across tabs through Web Locks or an IndexedDB transaction.
 * If neither mechanism is available, editing remains single-tab only.
 */
export function createGuestTrips(options = {}) {
  const storage = () => {
    try {
      const value = options.storage ?? globalThis.localStorage
      if (!value) throw new Error('unavailable')
      return value
    } catch { throw new GuestTripError('无法访问浏览器本地存储，请检查浏览器设置', 503) }
  }
  const locks = () => Object.prototype.hasOwnProperty.call(options, 'locks') ? options.locks : globalThis.navigator?.locks
  const indexedDB = () => Object.prototype.hasOwnProperty.call(options, 'indexedDB') ? options.indexedDB : globalThis.indexedDB
  const clock = options.now ?? (() => new Date().toISOString())
  const makeId = options.makeId ?? newId
  let databasePromise = null
  const openLockDatabase = () => {
    if (!databasePromise) databasePromise = new Promise((resolve, reject) => {
      let request
      try { request = indexedDB().open('travel-guest-write-locks-v1', 1) }
      catch { reject(new GuestTripError('无法初始化本地多标签页保护', 503)); return }
      request.onupgradeneeded = () => {
        if (!request.result.objectStoreNames.contains('locks')) request.result.createObjectStore('locks')
      }
      request.onerror = () => reject(new GuestTripError('无法初始化本地多标签页保护', 503))
      request.onblocked = () => reject(new GuestTripError('本地多标签页保护被其他页面阻止', 503))
      request.onsuccess = () => {
        request.result.onversionchange = () => { request.result.close(); databasePromise = null }
        resolve(request.result)
      }
    }).catch(error => { databasePromise = null; throw error })
    return databasePromise
  }
  const indexedLock = async (id, fn) => {
    const db = await openLockDatabase()
    return new Promise((resolve, reject) => {
      let result, failed = null, transaction
      try {
        transaction = db.transaction('locks', 'readwrite')
        const request = transaction.objectStore('locks').get(id)
        request.onsuccess = () => {
          try { result = fn() } catch (error) { failed = error; transaction.abort() }
        }
        transaction.oncomplete = () => resolve(result)
        transaction.onabort = transaction.onerror = () => reject(failed || new GuestTripError('本地多标签页保护失败，请重试', 503))
      } catch { reject(new GuestTripError('本地多标签页保护失败，请重试', 503)) }
    })
  }
  const key = id => {
    if (!isGuestTripId(id)) fail('本地旅行标识不正确', 404)
    return PREFIX + id
  }
  const read = id => {
    let raw
    try { raw = storage().getItem(key(id)) } catch (error) {
      if (error instanceof GuestTripError) throw error
      throw new GuestTripError('读取本地旅行失败', 503)
    }
    if (raw === null) fail('本地旅行不存在', 404)
    let record
    try { record = JSON.parse(raw) } catch { fail('本地旅行数据无法读取，请勿清除浏览器数据', 500) }
    if (!record || record.schema !== SCHEMA || !record.trip || record.trip.id !== id || !Array.isArray(record.trip.days) || !Array.isArray(record.trip.places) || !Number.isInteger(record.trip.version) || !Number.isInteger(record.nextId) || !Array.isArray(record.migrations)) {
      fail('本地旅行数据格式异常，请勿清除浏览器数据', 500)
    }
    if (record.trip.reservations === undefined) record.trip.reservations = []
    if (!Array.isArray(record.trip.reservations)) fail('本地预订记录格式异常，请勿清除浏览器数据', 500)
    return record
  }
  const write = (id, record) => {
    try { storage().setItem(key(id), JSON.stringify(record)) } catch (error) {
      if (error instanceof GuestTripError) throw error
      throw new GuestTripError('本地保存失败，可能是存储空间不足；原有数据未覆盖', 503)
    }
  }
  const locked = async (id, fn) => {
    const manager = locks()
    if (manager?.request) return manager.request(`${PREFIX}${id}`, { mode: 'exclusive' }, fn)
    if (indexedDB()?.open) return indexedLock(id, fn)
    return fn()
  }
  const change = async (id, version, fn) => locked(id, () => {
    const record = read(id)
    if (record.trip.version !== version) fail('行程已在其他页面更新，请刷新后重试', 409)
    const previous = new Map(record.trip.days.filter(day => Object.keys(day.route_preferences || {}).length)
      .map(day => [day.id, new Map(locatedPairs(day).map(({ start, end }) => [routeLegKey(start, end), legIdentity(start, end)]))]))
    fn(record)
    retainRoutePreferences(record.trip, previous)
    record.trip.version += 1
    record.trip.updated_at = clock()
    write(id, record)
    return copy(record.trip)
  })
  const dayOf = (trip, id) => {
    const day = trip.days.find(d => d.id === id)
    if (!day) fail('日期不存在', 404)
    return day
  }
  const itemOf = (trip, id) => {
    for (const day of trip.days) {
      const index = day.items.findIndex(item => item.id === id)
      if (index !== -1) return { day, index, item: day.items[index] }
    }
    fail('行程项目不存在', 404)
  }
  const placeOf = (trip, id) => {
    const index = trip.places.findIndex(place => place.id === id)
    if (index < 0) fail('待安排地点不存在', 404)
    return { index, place: trip.places[index] }
  }
  const reorder = day => day.items.forEach((item, index) => { item.sort_order = index })

  return {
    supportsCrossTabLock: () => Boolean(locks()?.request || indexedDB()?.open),
    async setRoutePreference(id, dayId, input) {
      if (!(input.mode === null || isRouteMode(input.mode))) fail('交通方式不正确')
      return change(id, input.version, ({ trip }) => {
        const day = dayOf(trip, dayId)
        const pair = routePairs(day.items).find(({ start, end }) => start.id === input.origin_id && end.id === input.destination_id)
        if (!pair) fail('相邻行程地点不存在，请刷新行程', 404)
        if (input.mode && !(hasRouteCoordinates(pair.start) && hasRouteCoordinates(pair.end))) fail('请先确认这段路线的两个地点')
        const key = routeLegKey(pair.start, pair.end)
        const choices = { ...day.route_preferences }
        if (input.mode === null) delete choices[key]
        else choices[key] = input.mode
        day.route_preferences = choices
      })
    },
    async list() {
      const s = storage(), ids = []
      try {
        for (let i = 0; i < s.length; i++) {
          const candidate = s.key(i)
          if (candidate?.startsWith(PREFIX)) ids.push(candidate.slice(PREFIX.length))
        }
      } catch { throw new GuestTripError('读取本地旅行列表失败', 503) }
      const items = ids.map(id => copy(read(id).trip))
      items.sort((a, b) => b.updated_at.localeCompare(a.updated_at) || a.id.localeCompare(b.id))
      return { items }
    },
    async get(id) { return copy(read(id).trip) },
    async create(data) {
      const title = text(data.title, '旅行名称', 100, { required: true }).trim()
      const destination = text(data.destination ?? '', '目的地', 100).trim()
      const startDate = dateOrNull(data.start_date)
      const dayCount = integer(data.days ?? 1, '旅行天数', 1, 30)
      const id = makeId()
      return locked(id, () => {
        let existing
        try { existing = storage().getItem(key(id)) } catch (error) {
          if (error instanceof GuestTripError) throw error
          throw new GuestTripError('读取本地旅行失败', 503)
        }
        if (existing !== null) fail('本地旅行标识冲突，请重试', 409)
        const now = clock(), record = { schema: SCHEMA, nextId: dayCount + 1, migrations: [], trip: {
          id, title, destination, start_date: startDate, status: 'planning', source_plan_id: null,
          version: 1, created_at: now, updated_at: now,
          days: Array.from({ length: dayCount }, (_, index) => ({ id: index + 1, day_number: index + 1, theme: '', items: [] })), places: [], reservations: [],
        } }
        write(id, record)
        return copy(record.trip)
      })
    },
    async patch(id, input) {
      const updates = {}
      if ('title' in input) updates.title = text(input.title, '旅行名称', 100, { required: true }).trim()
      if ('destination' in input) updates.destination = text(input.destination, '目的地', 100)
      if ('start_date' in input) updates.start_date = dateOrNull(input.start_date)
      if ('walking_preferences' in input) updates.walking_preferences = walkingPreferences(input.walking_preferences)
      if ('status' in input) {
        if (!STATUS.has(input.status)) fail('旅行状态不正确')
        updates.status = input.status
      }
      return change(id, input.version, record => Object.assign(record.trip, updates))
    },
    async addDay(id, input) {
      return change(id, input.version, record => {
        if (record.trip.days.length >= 30) fail('一段旅行最多 30 天')
        record.trip.days.push({ id: record.nextId++, day_number: record.trip.days.length + 1, theme: '', items: [] })
      })
    },
    async updateDay(id, dayId, input) {
      const theme = text(input.theme, '每日主题', 100).trim()
      return change(id, input.version, record => { dayOf(record.trip, dayId).theme = theme })
    },
    async removeDay(id, dayId, input) {
      return change(id, input.version, record => {
        const days = record.trip.days, day = dayOf(record.trip, dayId)
        if (days.length <= 1 || days[days.length - 1].id !== dayId) fail('只能删除最后一天，且旅行至少保留一天')
        if (day.items.length || day.theme.trim()) fail('这一天仍有安排或主题，请先处理', 409)
        days.pop()
      })
    },
    async addItem(id, dayId, input) {
      const data = itemFields(input)
      return change(id, input.version, record => {
        const day = dayOf(record.trip, dayId)
        day.items.push({ id: record.nextId++, sort_order: day.items.length, ...data })
      })
    },
    async updateItem(id, itemId, input) {
      const data = itemFields(input)
      return change(id, input.version, record => Object.assign(itemOf(record.trip, itemId).item, data))
    },
    async moveItem(id, itemId, input) {
      integer(input.index, '位置', 0, 1000000)
      return change(id, input.version, record => {
        const { day, index, item } = itemOf(record.trip, itemId), target = dayOf(record.trip, input.day_id)
        day.items.splice(index, 1)
        target.items.splice(Math.min(input.index, target.items.length), 0, item)
        reorder(day); if (target !== day) reorder(target)
      })
    },
    async deleteItem(id, itemId, input) {
      return change(id, input.version, record => {
        const { day, index } = itemOf(record.trip, itemId)
        for (const reservation of record.trip.reservations) if (reservation.item_id === itemId) reservation.item_id = null
        day.items.splice(index, 1); reorder(day)
      })
    },
    async applyAi(id, input) {
      if (!Array.isArray(input.operations) || !input.operations.length || input.operations.length > 40) fail('没有可应用的 AI 建议')
      return change(id, input.version, record => {
        const trip = record.trip
        const originalIds = new Set(trip.days.flatMap(day => day.items.map(item => item.id)))
        const originalItem = itemId => {
          integer(itemId, '行程项目标识', 1, Number.MAX_SAFE_INTEGER)
          if (!originalIds.has(itemId)) fail('建议引用了不存在的安排')
          return itemOf(trip, itemId)
        }
        for (const op of input.operations) {
          if (!op || typeof op !== 'object') fail('AI 建议格式不正确')
          if (op.action === 'add') {
            aiKeys(op, ['action','day_id','index','item'], ['action','day_id','index','item'])
            aiKeys(op.item, ['name','kind','start_time','duration_min','note','activity'], ['name'])
            if (Object.entries(op.item).some(([k,v]) => k !== 'duration_min' && typeof v !== 'string')) fail('建议的新增内容不正确')
            const day = dayOf(trip, integer(op.day_id, '日期标识', 1, Number.MAX_SAFE_INTEGER))
            integer(op.index, '位置', 0, 300)
            if (op.index > day.items.length) fail('建议的新增位置无效')
            const item = itemFields({...op.item, source:'AI 建议（地点待核实）'})
            day.items.splice(op.index, 0, {id:record.nextId++, sort_order:0, ...item})
          } else if (op.action === 'edit') {
            aiKeys(op, ['action','item_id','changes'], ['action','item_id','changes'])
            aiKeys(op.changes, ['start_time','duration_min','note','activity'])
            if (!Object.keys(op.changes).length || Object.entries(op.changes).some(([k,v]) => k !== 'duration_min' && typeof v !== 'string')) fail('建议的修改内容不正确')
            const {item} = originalItem(op.item_id)
            const validated = itemFields({...item, ...op.changes})
            for (const key of Object.keys(op.changes)) item[key] = validated[key]
          } else if (op.action === 'move') {
            aiKeys(op, ['action','item_id','day_id','index'], ['action','item_id','day_id','index'])
            const {day, index, item} = originalItem(op.item_id)
            const target = dayOf(trip, integer(op.day_id, '日期标识', 1, Number.MAX_SAFE_INTEGER))
            integer(op.index, '位置', 0, 300); day.items.splice(index, 1)
            if (op.index > target.items.length) fail('建议的移动位置无效')
            target.items.splice(op.index, 0, item)
          } else if (op.action === 'remove') {
            aiKeys(op, ['action','item_id'], ['action','item_id'])
            const {day, index} = originalItem(op.item_id)
            for (const reservation of trip.reservations) if (reservation.item_id === op.item_id) reservation.item_id = null
            day.items.splice(index, 1)
          } else fail('AI 建议操作不正确')
        }
        if (trip.days.some(day => day.items.length > 200) || trip.days.reduce((n, day) => n + day.items.length, 0) > 500) fail('行程项目过多')
        trip.days.forEach(reorder)
      })
    },
    async addReservation(id, input) {
      const data = reservationFields(input)
      return change(id, input.version, record => {
        if (record.trip.reservations.length >= 500) fail('预订记录最多 500 条')
        const item_name = data.item_id === null ? '' : itemOf(record.trip, data.item_id).item.name
        record.trip.reservations.unshift({ id: record.nextId++, ...data, item_name })
      })
    },
    async updateReservation(id, reservationId, input) {
      const data = reservationFields(input)
      return change(id, input.version, record => {
        const row = record.trip.reservations.find(x => x.id === reservationId)
        if (!row) fail('预订记录不存在', 404)
        const item_name = data.item_id !== null ? itemOf(record.trip, data.item_id).item.name : row.item_id === null ? row.item_name : ''
        Object.assign(row, data, {item_name})
      })
    },
    async deleteReservation(id, reservationId, input) {
      return change(id, input.version, record => {
        const index = record.trip.reservations.findIndex(x => x.id === reservationId)
        if (index < 0) fail('预订记录不存在', 404)
        record.trip.reservations.splice(index, 1)
      })
    },
    async addPlace(id, input) {
      const data = placeFields(input)
      return change(id, input.version, record => {
        if (data.amap_id && record.trip.places.some(place => place.amap_id === data.amap_id)) fail('该地点已在待安排列表中', 409)
        record.trip.places.push({ id: record.nextId++, ...data })
      })
    },
    async importGuide(id, input) {
      const source = text(input.source ?? '', '来源', 150).trim() || '用户粘贴攻略'
      if (!Array.isArray(input.places) || input.places.length < 1 || input.places.length > 50) fail('请选择 1 至 50 个地点')
      const selected = input.places.map(place => {
        const name = text(place.name, '地点名称', 160, {required:true}).trim()
        const note = text(place.note ?? '', '备注', 2000).trim()
        if (!place.match) return {name, note, address:'', lng:null, lat:null, amap_id:'', source}
        if (place.match.name !== name) fail('地点名称与所选景点不一致')
        return placeFields({...place.match, name, note, source})
      })
      return locked(id, () => {
        const record = read(id)
        if (record.trip.version !== input.version) fail('行程已在其他页面更新，请刷新后重试', 409)
        const byPoi = new Map(record.trip.places.filter(p => p.amap_id).map(p => [p.amap_id, p]))
        let added = 0, merged = 0, unchanged = 0
        for (const place of selected) {
          const existing = place.amap_id && byPoi.get(place.amap_id)
          if (existing) {
            const note = combineGuideText(existing.note, place.note, 4000)
            const combinedSource = combineGuideText(existing.source, source, 200)
            if (note === existing.note && combinedSource === existing.source) unchanged++
            else { existing.note = note; existing.source = combinedSource; merged++ }
          } else {
            if (record.trip.places.length >= 500) fail('待安排地点最多 500 个')
            const entry = {id:record.nextId++, ...place}
            record.trip.places.push(entry); if (entry.amap_id) byPoi.set(entry.amap_id, entry); added++
          }
        }
        if (added || merged) { record.trip.version++; record.trip.updated_at = clock(); write(id, record) }
        return {trip:copy(record.trip), added, merged, unchanged}
      })
    },
    async schedulePlace(id, placeId, input) {
      return change(id, input.version, record => {
        const { index, place } = placeOf(record.trip, placeId), day = dayOf(record.trip, input.day_id)
        day.items.push({ id: record.nextId++, sort_order: day.items.length,
          ...itemFields({ ...place, kind: '地点', start_time: '', duration_min: null, activity: '' }) })
        record.trip.places.splice(index, 1)
      })
    },
    async deletePlace(id, placeId, input) {
      return change(id, input.version, record => record.trip.places.splice(placeOf(record.trip, placeId).index, 1))
    },
    async prepareMigration(id) {
      const record = read(id)
      // Validate again before allowing a complete snapshot to leave this device.
      text(record.trip.title, '旅行名称', 100, { required: true })
      text(record.trip.destination, '目的地', 100); dateOrNull(record.trip.start_date)
      integer(record.trip.version, '行程版本', 1, Number.MAX_SAFE_INTEGER)
      if (!STATUS.has(record.trip.status) || record.trip.days.length < 1 || record.trip.days.length > 30) fail('本地旅行数据格式异常', 500)
      const ids = new Set()
      const validId = id => {
        integer(id, '本地项目标识', 1, Number.MAX_SAFE_INTEGER)
        if (ids.has(id)) fail('本地行程包含重复标识', 500)
        ids.add(id)
      }
      record.trip.days.forEach((day, i) => {
        if (!day || !Array.isArray(day.items) || day.day_number !== i + 1) fail('本地行程格式异常', 500)
        validId(day.id); text(day.theme, '每日主题', 100)
        day.items.forEach((item, index) => {
          validId(item.id)
          if (item.sort_order !== index) fail('本地行程顺序异常', 500)
          itemFields(item)
        })
      })
      record.trip.places.forEach(place => { validId(place.id); placeFields(place) })
      if (record.trip.reservations.length > 500) fail('本地预订记录过多', 500)
      const reservations = record.trip.reservations.map(row => {
        validId(row.id)
        const fields = reservationFields(row)
        const item_name = text(row.item_name, '关联安排名称', 160)
        let location = null
        if (fields.item_id !== null) {
          const found = itemOf(record.trip, fields.item_id)
          location = { day_number: found.day.day_number, item_index: found.index }
        }
        const {item_id, ...data} = fields
        return {...data, item_name, location}
      })
      if (record.nextId <= Math.max(...ids)) fail('本地行程序号异常', 500)
      const trip = copy(record.trip)
      trip.reservations = reservations
      if ('walking_preferences' in trip) trip.walking_preferences = walkingPreferences(trip.walking_preferences)
      for (const day of trip.days) {
        const choices = day.route_preferences || {}
        const indices = new Map(day.items.map((item, index) => [item.id, index]))
        const mapped = locatedPairs(day).flatMap(({ start, end }) => {
          const mode = choices[routeLegKey(start, end)]
          return isRouteMode(mode) ? [[`${indices.get(start.id)}:${indices.get(end.id)}`, mode]] : []
        })
        if (mapped.length !== Object.keys(choices).length) fail('本地交通方式关联异常，请核对路线后再复制', 422)
        if (mapped.length) day.route_preferences = Object.fromEntries(mapped)
        else delete day.route_preferences
      }
      return { guest_id: id, guest_version: record.trip.version, trip }
    },
    async migrationHistory(id) { return copy(read(id).migrations) },
    async markMigrated(id, guestVersion, userId, serverTripId) {
      integer(guestVersion, '迁移版本', 1, Number.MAX_SAFE_INTEGER)
      integer(userId, '用户标识', 1, Number.MAX_SAFE_INTEGER)
      integer(serverTripId, '服务器旅行标识', 1, Number.MAX_SAFE_INTEGER)
      return locked(id, () => {
        const record = read(id)
        if (guestVersion > record.trip.version) fail('迁移版本不正确', 409)
        const existing = record.migrations.find(m => m.user_id === userId && m.guest_version === guestVersion)
        if (existing && existing.server_trip_id !== serverTripId) fail('该版本已有不同的迁移记录', 409)
        if (!existing) record.migrations.push({ user_id: userId, guest_version: guestVersion, server_trip_id: serverTripId, confirmed_at: clock() })
        write(id, record)
        return copy(record.migrations)
      })
    },
  }
}

export const guestTrips = createGuestTrips()
