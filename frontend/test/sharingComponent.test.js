import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { parse, compileScript } from '@vue/compiler-sfc'
import { createRenderer, createVNode, nextTick } from 'vue'

// Exercise the actual script-setup component with Vue lifecycle/watchers and
// isolated API/browser ports. No browser, account or external service is used.
async function mountSharing(api, events) {
  const source = await readFile(new URL('../src/components/TripSharingPanel.vue', import.meta.url), 'utf8')
  const { descriptor } = parse(source)
  const script = compileScript(descriptor, { id: 'sharing-regression' }).content
    .replace(/from ['"]vue['"]/g, `from ${JSON.stringify(import.meta.resolve('vue'))}`)
    .replace(/import \{ api \} from ['"]\.\.\/api['"]/, 'const api = globalThis.__sharingTestApi')
    .replaceAll('import.meta.env.BASE_URL', JSON.stringify('/travel/'))
  globalThis.__sharingTestApi = api
  const { default: component } = await import(`data:text/javascript;base64,${Buffer.from(script).toString('base64')}#${Math.random()}`)
  const renderer = createRenderer({
    createComment: () => ({}), insert() {}, remove() {}, parentNode: () => null,
    nextSibling: () => null, createText: () => ({}), setText() {},
    createElement: () => ({}), setElementText() {}, patchProp() {},
  })
  component.render = () => null
  const root = {}, props = { tripId: 1, onDirty: value => events.push(['dirty', value]), onTripChanged: () => events.push(['changed']) }
  let vnode = createVNode(component, props)
  renderer.render(vnode, root)
  await settle()
  const state = vnode.component.setupState
  return { state, update(id) { vnode = createVNode(component, { ...props, tripId: id }); renderer.render(vnode, root) }, unmount() { renderer.render(null, root) } }
}
async function settle() { await new Promise(resolve => setImmediate(resolve)); await nextTick() }
function deferred() { let resolve; const promise = new Promise(done => { resolve = done }); return { promise, resolve } }

test('分享链接、移除成员与组件生命周期的回归', async t => {
  const oldWindow = globalThis.window, oldDocument = globalThis.document
  globalThis.window = { location: { origin: 'https://test.invalid' }, confirm: () => true, setInterval: () => 1, clearInterval() {} }
  globalThis.document = { addEventListener() {}, removeEventListener() {}, hidden: false }
  try {
    await t.test('创建失败保留已有地址，收起后才清除草稿信号', async () => {
      const events = []
      const mounted = await mountSharing({get: async () => ({links:[],members:[]}), post: async (path,body) => {
        if (path.endsWith('/prepare')) {
          if (body.mode==='edit') throw new Error('offline')
          return {preparation:'prepared-view',mode:body.mode,token:'A'.repeat(43)}
        }
        return {id:1, mode:'view', token:'A'.repeat(43),expires_at:'2099-01-01T00:00:00Z'}
      }}, events)
      try {
        await mounted.state.create('view')
        assert.equal(mounted.state.secrets.length, 1)
        assert.equal(events.at(-1)[1], true)
        await mounted.state.create('edit')
        assert.equal(mounted.state.secrets.length, 1)
        assert.match(mounted.state.secrets[0].url, /\/travel\/share#/)
        mounted.state.hideSecret(1)
        assert.deepEqual(events.at(-1), ['dirty', false])
      } finally { mounted.unmount() }
    })
    await t.test('移除成员完成后才通知父页面，列表刷新失败也通知版本改变', async () => {
      const events = []
      let reads = 0
      const mounted = await mountSharing({get: async () => {
        if (++reads > 1) throw new Error('refresh offline')
        return {links:[],members:[{user_id:2}]}
      }, del: async () => null}, events)
      try {
        await mounted.state.remove({user_id:2})
        assert.deepEqual(events.slice(-2), [['dirty', false], ['changed']])
        assert.equal(mounted.state.working, false)
      } finally { mounted.unmount() }
    })
    await t.test('切换旅行后，迟到的创建结果不能写入新上下文', async () => {
      const events = [], pending = deferred(), reads = []
      const mounted = await mountSharing({get: async path => {reads.push(path);return {links:[],members:[]}}, post: () => pending.promise}, events)
      const creating = mounted.state.create('view')
      mounted.update(2); await settle()
      pending.resolve({preparation:'prepared-view',mode:'view',token:'A'.repeat(43)})
      await creating
      assert.equal(mounted.state.secrets.length, 0)
      assert.deepEqual(reads, ['/api/trips/1/sharing','/api/trips/2/sharing'])
      mounted.unmount()
    })
    await t.test('卸载后不再处理创建结果或刷新列表', async () => {
      const events = [], pending = deferred(), reads = []
      const mounted = await mountSharing({get: async path => {reads.push(path);return {links:[],members:[]}}, post: () => pending.promise}, events)
      const creating = mounted.state.create('view')
      mounted.unmount()
      const eventCount = events.length
      pending.resolve({preparation:'prepared-view',mode:'view',token:'A'.repeat(43)})
      await creating
      assert.equal(mounted.state.secrets.length, 0)
      assert.equal(reads.length, 1)
      assert.equal(events.length, eventCount)
    })
    await t.test('新旅行加载失败时不能显示上一份旅行的成员和链接', async () => {
      const events = []
      const mounted = await mountSharing({get: async path => {
        if (path.includes('/2/')) throw new Error('offline')
        return {links:[{id:9,mode:'edit'}],members:[{user_id:3}]}
      }}, events)
      try {
        mounted.update(2); await settle()
        assert.deepEqual(mounted.state.links, [])
        assert.deepEqual(mounted.state.members, [])
        assert.equal(mounted.state.error, 'offline')
      } finally { mounted.unmount() }
    })
    await t.test('创建结果未知时保留同次准备，继续不再准备新链接', async () => {
      const events=[],calls=[]
      let fail=true
      const mounted=await mountSharing({get:async()=>({links:[],members:[]}),post:async(path,body)=>{
        calls.push([path,body])
        if(path.endsWith('/prepare'))return {preparation:'same-preparation',mode:'view',token:'A'.repeat(43)}
        if(fail){fail=false;throw Error('response lost')}
        return {id:9,mode:'view',token:'A'.repeat(43),expires_at:'2099-01-01T00:00:00Z'}
      }},events)
      try {
        await mounted.state.create('view')
        assert.equal(mounted.state.pending.preparation,'same-preparation')
        assert.equal(events.at(-1)[1],true)
        await mounted.state.create('view')
        assert.equal(calls.filter(([path])=>path.endsWith('/prepare')).length,1)
        assert.deepEqual(calls.filter(([path])=>path.endsWith('/activate')).map(([,body])=>body),[
          {preparation:'same-preparation'},{preparation:'same-preparation'}])
        assert.equal(mounted.state.pending,null)
        assert.equal(mounted.state.secrets.length,1)
      }finally{mounted.unmount()}
    })
  } finally {
    globalThis.window = oldWindow; globalThis.document = oldDocument
    delete globalThis.__sharingTestApi
  }
})
