import test from 'node:test'
import assert from 'node:assert/strict'
import { createGuestTrips, isGuestTripId, isGuestTripStorageChange } from '../src/store/guestTrips.js'

function memoryStorage() {
  const data = new Map()
  return { get length() { return data.size }, key: index => Array.from(data.keys())[index] ?? null,
    getItem: key => data.has(key) ? data.get(key) : null, setItem: (key, value) => data.set(key, String(value)),
    removeItem: key => data.delete(key) }
}

function fixture() {
  let id = 0, time = 0
  const storage = memoryStorage()
  const store = createGuestTrips({ storage, locks: null, makeId: () => `guest-test-${++id}`,
    now: () => new Date(1700000000000 + time++ * 1000).toISOString() })
  return { store, storage }
}

test('本机步行偏好随版本保存并进入账号复制的快照', async () => {
  const { store } = fixture()
  const trip = await store.create({ title: '少走路', days: 1 })
  const walking_preferences = { daily_limit_m: 2500, check_climbs: true }
  const saved = await store.patch(trip.id, { version: trip.version, walking_preferences })
  assert.deepEqual(saved.walking_preferences, walking_preferences)
  await assert.rejects(store.patch(trip.id, { version: trip.version, walking_preferences: null }), { status: 409 })
  assert.deepEqual((await store.prepareMigration(trip.id)).trip.walking_preferences, walking_preferences)
})

test('只响应当前本机旅行或存储被清空的跨页面事件', () => {
  const id = 'guest-test-1'
  assert.equal(isGuestTripStorageChange({key:'travel.guest.trip.v1.guest-test-1'}, id), true)
  assert.equal(isGuestTripStorageChange({key:'travel.guest.trip.v1.guest-test-2'}, id), false)
  assert.equal(isGuestTripStorageChange({key:null}, id), true)
  assert.equal(isGuestTripStorageChange({key:null}, '12'), false)
})

test('本机分段交通保留在当前坐标和顺序上，复制时转换为安排位置', async () => {
  const { store } = fixture()
  let trip = await store.create({ title: '分段交通', days: 1 })
  const id = trip.id, day = trip.days[0].id
  for (const item of [{ name: '甲馆', lng: 120.1, lat: 30 }, { name: '午休', kind: '休息' },
    { name: '乙馆', lng: 120.2, lat: 30 }, { name: '待查地点' }]) {
    trip = await store.addItem(id, day, { ...item, version: trip.version })
  }
  const [first, rest, end, missing] = trip.days[0].items
  const pick = { origin_id: first.id, destination_id: end.id, mode: 'driving', version: trip.version }
  await assert.rejects(store.setRoutePreference(id, day, { ...pick, destination_id: rest.id }), { status: 404 })
  await assert.rejects(store.setRoutePreference(id, day, { ...pick, origin_id: end.id, destination_id: missing.id }), { status: 422 })
  trip = await store.setRoutePreference(id, day, pick)
  await assert.rejects(store.setRoutePreference(id, day, pick), { status: 409 })
  assert.deepEqual((await store.get(id)).days[0].route_preferences, { [`${first.id}:${end.id}`]: 'driving' })
  assert.deepEqual((await store.prepareMigration(id)).trip.days[0].route_preferences, { '0:2': 'driving' })
  trip = await store.updateItem(id, first.id, { ...first, lng: 120.11, version: trip.version })
  trip = await store.updateItem(id, first.id, { ...first, version: trip.version })
  assert.deepEqual(trip.days[0].route_preferences, {})
  trip = await store.setRoutePreference(id, day, { ...pick, version: trip.version })
  trip = await store.moveItem(id, end.id, { version: trip.version, index: 0, day_id: day })
  assert.deepEqual(trip.days[0].route_preferences, {})
  assert.equal((await store.prepareMigration(id)).trip.days[0].route_preferences, undefined)
})

test('本地旅行完整生命周期和陈旧版本保护', async () => {
  const { store } = fixture()
  assert.equal(store.supportsCrossTabLock(), false)
  let trip = await store.create({ title: '杭州旅行', destination: '杭州', days: 2, start_date: '2026-11-20' })
  assert.ok(isGuestTripId(trip.id))
  assert.equal(trip.version, 1)
  const id = trip.id, first = trip.days[0].id, second = trip.days[1].id
  trip = await store.addItem(id, first, { version: trip.version, name: '西湖', lng: 120.14, lat: 30.25, note: '早去' })
  const itemId = trip.days[0].items[0].id
  await assert.rejects(store.addItem(id, first, { version: 1, name: '旧页面写入' }), { status: 409 })
  trip = await store.updateItem(id, itemId, { ...trip.days[0].items[0], version: trip.version, start_time: '09:00' })
  trip = await store.moveItem(id, itemId, { version: trip.version, day_id: second, index: 0 })
  assert.deepEqual(trip.days[0].items, [])
  assert.equal(trip.days[1].items[0].name, '西湖')
  assert.equal(trip.days[1].items[0].sort_order, 0)
  trip = await store.deleteItem(id, itemId, { version: trip.version })
  assert.deepEqual(trip.days[1].items, [])
  assert.deepEqual(await store.get(id), trip)
  assert.equal((await store.list()).items[0].id, id)
})

test('本地旅行可延长一天并保护旧版本', async () => {
  const { store } = fixture()
  let trip = await store.create({ title: '旅行', days: 1 })
  trip = await store.addDay(trip.id, { version: trip.version })
  assert.equal(trip.days.length, 2)
  assert.equal(trip.days[1].day_number, 2)
  await assert.rejects(store.addDay(trip.id, { version: 1 }), { status: 409 })
  assert.equal((await store.get(trip.id)).days.length, 2)
})

test('本机只能删除没有安排的最后一天', async () => {
  const { store } = fixture()
  let trip = await store.create({ title: '旅行', days: 2 })
  await assert.rejects(store.removeDay(trip.id, trip.days[0].id, { version: trip.version }), { status: 422 })
  trip = await store.addItem(trip.id, trip.days[1].id, { version: trip.version, name: '公园' })
  await assert.rejects(store.removeDay(trip.id, trip.days[1].id, { version: trip.version }), { status: 409 })
  trip = await store.deleteItem(trip.id, trip.days[1].items[0].id, { version: trip.version })
  trip = await store.removeDay(trip.id, trip.days[1].id, { version: trip.version })
  assert.equal(trip.days.length, 1)
  await assert.rejects(store.removeDay(trip.id, trip.days[0].id, { version: trip.version }), { status: 422 })
})

test('地点收集、去重、排入日期与来源保留', async () => {
  const { store } = fixture()
  let trip = await store.create({ title: '本地', days: 1 })
  trip = await store.addPlace(trip.id, { version: trip.version, name: '公园', amap_id: 'p1', lng: 120, lat: 30, note: '好风景', source: '景点库' })
  await assert.rejects(store.addPlace(trip.id, { version: trip.version, name: '同一地点', amap_id: 'p1' }), { status: 409 })
  const place = trip.places[0]
  trip = await store.schedulePlace(trip.id, place.id, { version: trip.version, day_id: trip.days[0].id })
  assert.equal(trip.places.length, 0)
  assert.equal(trip.days[0].items[0].amap_id, 'p1')
  assert.equal(trip.days[0].items[0].source, '景点库')
  assert.equal(trip.days[0].items[0].note, '好风景')
  trip = await store.addPlace(trip.id, { version: trip.version, name: '未核实' })
  trip = await store.deletePlace(trip.id, trip.places[0].id, { version: trip.version })
  assert.equal(trip.places.length, 0)
})

test('迁移快照独立且不可静默删除原行程，按账户和版本记录服务端确认', async () => {
  const { store } = fixture()
  const original = await store.create({ title: '周末', days: 1 })
  const migration = await store.prepareMigration(original.id)
  assert.equal(migration.guest_id, original.id)
  assert.equal(migration.guest_version, original.version)
  migration.trip.title = '外部修改'
  assert.equal((await store.get(original.id)).title, '周末')
  assert.deepEqual(await store.migrationHistory(original.id), [])
  await store.markMigrated(original.id, original.version, 4, 12)
  await store.markMigrated(original.id, original.version, 4, 12)
  assert.equal((await store.migrationHistory(original.id)).length, 1)
  await assert.rejects(store.markMigrated(original.id, original.version, 4, 13), { status: 409 })
  const later = await store.patch(original.id, { version: original.version, title: '新的名字' })
  assert.equal((await store.migrationHistory(original.id))[0].guest_version, original.version)
  assert.equal((await store.prepareMigration(original.id)).guest_version, later.version)
  assert.equal((await store.get(original.id)).title, '新的名字')
})

test('损坏数据和写入失败不会覆盖已有内容', async () => {
  const { store, storage } = fixture()
  const trip = await store.create({ title: '周末' })
  const key = storage.key(0), old = storage.getItem(key)
  storage.setItem = () => { throw new Error('quota') }
  await assert.rejects(store.patch(trip.id, { version: 1, title: '新标题' }), { status: 503 })
  assert.equal(storage.getItem(key), old)
  const broken = memoryStorage(), store2 = createGuestTrips({ storage: broken, locks: null })
  broken.setItem('travel.guest.trip.v1.guest-broken', '{not json')
  await assert.rejects(store2.get('guest-broken'), { status: 500 })
  await assert.rejects(store2.list(), { status: 500 })
  assert.equal(broken.getItem('travel.guest.trip.v1.guest-broken'), '{not json')
})

test('格式错误的日期和坐标不写入', async () => {
  const { store } = fixture()
  await assert.rejects(store.create({ title: '周末', start_date: '2026-02-30' }), { status: 422 })
  const trip = await store.create({ title: '周末' })
  await assert.rejects(store.addItem(trip.id, trip.days[0].id, { version: 1, name: '湖', lng: 120 }), { status: 422 })
  assert.equal((await store.get(trip.id)).version, 1)
})

test('跨页面使用 Web Locks 串行化并拒绝旧版本', async () => {
  const storage = memoryStorage(), queue = new Map()
  const locks = { async request(name, options, fn) {
    const previous = queue.get(name) || Promise.resolve()
    let release
    const current = new Promise(resolve => { release = resolve })
    queue.set(name, current)
    await previous
    try { return await fn() } finally { release() }
  } }
  const a = createGuestTrips({ storage, locks, makeId: () => 'guest-shared-1' })
  const b = createGuestTrips({ storage, locks })
  const trip = await a.create({ title: '共同的本地旅行' })
  assert.equal(a.supportsCrossTabLock(), true)
  const results = await Promise.allSettled([
    a.patch(trip.id, { version: 1, title: '页面一' }), b.patch(trip.id, { version: 1, title: '页面二' }),
  ])
  assert.equal(results.filter(r => r.status === 'fulfilled').length, 1)
  assert.equal(results.find(r => r.status === 'rejected').reason.status, 409)
  assert.equal((await a.get(trip.id)).version, 2)
})

test('无 Web Locks 时利用 IndexedDB 事务互斥', async () => {
  const storage = memoryStorage()
  let tail = Promise.resolve()
  const db = {
    objectStoreNames: { contains: () => true },
    transaction() {
      const previous = tail
      let release
      tail = new Promise(resolve => { release = resolve })
      const tx = {
        objectStore: () => ({ get: () => {
          const req = {}
          previous.then(() => {
            req.onsuccess?.()
            if (!tx.aborted) queueMicrotask(() => { tx.oncomplete?.(); release() })
          })
          return req
        } }),
        abort: () => { tx.aborted = true; queueMicrotask(() => { tx.onabort?.(); release() }) },
      }
      return tx
    },
  }
  const indexedDB = { open() {
    const req = { result: db }
    queueMicrotask(() => req.onsuccess?.())
    return req
  } }
  const a = createGuestTrips({ storage, locks: null, indexedDB, makeId: () => 'guest-indexed-1' })
  const b = createGuestTrips({ storage, locks: null, indexedDB })
  const trip = await a.create({ title: '本地旅行' })
  assert.equal(a.supportsCrossTabLock(), true)
  const outcomes = await Promise.allSettled([
    a.patch(trip.id, { version: 1, title: '页面一' }), b.patch(trip.id, { version: 1, title: '页面二' }),
  ])
  assert.equal(outcomes.filter(result => result.status === 'fulfilled').length, 1)
  assert.equal(outcomes.find(result => result.status === 'rejected').reason.status, 409)
})

test('迁移前拒绝损坏的项目标识或次序', async () => {
  const { store, storage } = fixture()
  let trip = await store.create({ title: '本地' })
  trip = await store.addItem(trip.id, trip.days[0].id, { version: 1, name: '地点' })
  const key = storage.key(0), record = JSON.parse(storage.getItem(key))
  record.trip.days[0].items[0].id = record.trip.days[0].id
  storage.setItem(key, JSON.stringify(record))
  await assert.rejects(store.prepareMigration(trip.id), { status: 500 })
  assert.equal(JSON.parse(storage.getItem(key)).trip.days[0].items[0].id, record.trip.days[0].id)
})

test('本机预订记录支持编辑、冲突保护和复制关联', async () => {
  const {store} = fixture()
  let trip = await store.create({title:'本机旅行'})
  trip = await store.addItem(trip.id, trip.days[0].id, {version:trip.version, name:'酒店位置'})
  const item = trip.days[0].items[0].id
  const entry = {kind:'住宿', title:'酒店', status:'已预订', start_date:'2026-12-01', end_date:'2026-12-02', item_id:item, reference:'TEST-123', note:''}
  trip = await store.addReservation(trip.id, {...entry, version:trip.version})
  const recordId = trip.reservations[0].id
  await assert.rejects(store.addReservation(trip.id, {...entry, version:1}), {status:409})
  await assert.rejects(store.addReservation(trip.id, {...entry, version:trip.version, end_date:'2026-11-01'}), {status:422})
  let snapshot = await store.prepareMigration(trip.id)
  assert.deepEqual(snapshot.trip.reservations[0].location, {day_number:1,item_index:0})
  assert.equal(snapshot.trip.reservations[0].reference, 'TEST-123')
  trip = await store.deleteItem(trip.id, item, {version:trip.version})
  assert.equal(trip.reservations[0].item_id, null)
  assert.equal(trip.reservations[0].item_name, '酒店位置')
  trip = await store.updateReservation(trip.id, recordId, {...entry, title:'修改', item_id:null, version:trip.version})
  snapshot = await store.prepareMigration(trip.id)
  assert.equal(snapshot.trip.reservations[0].location, null)
  assert.equal(snapshot.trip.reservations[0].item_name, '酒店位置')
  trip = await store.deleteReservation(trip.id, recordId, {version:trip.version})
  assert.deepEqual(trip.reservations, [])
})

test('本机每日主题可编辑清空并保护版本', async () => {
  const {store} = fixture()
  let trip = await store.create({title:'旅行', days:2})
  const day = trip.days[1].id
  trip = await store.updateDay(trip.id, day, {version:trip.version, theme:'  山海之旅  '})
  assert.equal(trip.days[1].theme, '山海之旅')
  await assert.rejects(store.updateDay(trip.id, day, {version:1, theme:'旧内容'}), {status:409})
  await assert.rejects(store.removeDay(trip.id, day, {version:trip.version}), {status:409})
  assert.equal((await store.prepareMigration(trip.id)).trip.days[1].theme, '山海之旅')
  trip = await store.updateDay(trip.id, day, {version:trip.version, theme:''})
  trip = await store.removeDay(trip.id, day, {version:trip.version})
  assert.equal(trip.days.length, 1)
})

test('表单打开后其他页面更新，旧版本提交保留对方变更', async () => {
  const {store} = fixture()
  let trip = await store.create({title:'原旅行'})
  const id = trip.id, day = trip.days[0].id, openedVersion = trip.version
  trip = await store.addItem(id, day, {version:trip.version, name:'同行安排'})
  await assert.rejects(store.patch(id, {version:openedVersion, title:'旧页面标题'}), {status:409})
  await assert.rejects(store.addItem(id, day, {version:openedVersion, name:'旧页面新安排'}), {status:409})
  const current = await store.get(id)
  assert.equal(current.title, '原旅行')
  assert.deepEqual(current.days[0].items.map(item => item.name), ['同行安排'])
})

test('游客攻略确认后原子导入、合并来源并保护旧版本', async () => {
  const {store} = fixture()
  let trip = await store.create({title:'攻略旅行'})
  const id = trip.id
  const match = {id:1, name:'西湖', address:'杭州', lng:120.14, lat:30.25, amap_id:'hz-1'}
  const payload = {version:trip.version, source:'朋友攻略', places:[{name:'西湖', note:'散步', match},{name:'未知地点', note:'预约', match:null}]}
  assert.equal((await store.get(id)).places.length, 0)
  let result = await store.importGuide(id, payload)
  assert.equal(result.added, 2); trip = result.trip
  assert.equal(trip.places[0].source, '朋友攻略'); assert.equal(trip.places[0].lng, 120.14)
  await assert.rejects(store.importGuide(id, payload), {status:409})
  result = await store.importGuide(id, {version:trip.version, source:'第二篇', places:[{name:'西湖',note:'日落',match}]})
  trip = result.trip; assert.equal(result.merged, 1)
  assert.equal(trip.places[0].note, '散步\n\n日落'); assert.equal(trip.places[0].source, '朋友攻略\n\n第二篇')
  await assert.rejects(store.importGuide(id, {version:trip.version, source:'来源', places:[{name:'不匹配',match}]}), {status:422})
  assert.deepEqual(await store.get(id), trip)
  const migration = await store.prepareMigration(id)
  assert.equal(migration.trip.places[0].source, '朋友攻略\n\n第二篇')
})

test('游客 AI 建议原子应用并保留关联快照', async () => {
  const {store} = fixture()
  let trip = await store.create({title:'AI旅行',days:2}); const id = trip.id, first = trip.days[0].id, second = trip.days[1].id
  trip = await store.addItem(id, first, {version:trip.version,name:'西湖',note:'原备注',lng:120,lat:30})
  const item = trip.days[0].items[0].id
  trip = await store.addReservation(id,{version:trip.version,kind:'门票',title:'门票',status:'已预订',item_id:item,reference:'demo'})
  const original = trip
  const ops = [{action:'move',item_id:item,day_id:second,index:0},{action:'edit',item_id:item,changes:{note:'新备注'}},
    {action:'add',day_id:first,index:0,item:{name:'建议地点',start_time:'09:00'}}]
  await assert.rejects(store.applyAi(id,{version:trip.version,operations:[...ops,{action:'remove',item_id:999}]}),{status:422})
  assert.deepEqual(await store.get(id), original)
  trip = await store.applyAi(id,{version:trip.version,operations:ops})
  assert.equal(trip.days[1].items[0].id,item); assert.equal(trip.days[1].items[0].lng,120)
  assert.equal(trip.days[1].items[0].note,'新备注'); assert.equal(trip.days[0].items[0].source,'AI 建议（地点待核实）')
  assert.equal(trip.reservations[0].item_id,item)
  await assert.rejects(store.applyAi(id,{version:original.version,operations:ops}),{status:409})
  trip = await store.applyAi(id,{version:trip.version,operations:[{action:'remove',item_id:item}]})
  assert.equal(trip.reservations[0].item_id,null); assert.equal(trip.reservations[0].item_name,'西湖')
})
