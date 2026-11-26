import test from 'node:test'
import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {parse,compileScript} from '@vue/compiler-sfc'
import {createRenderer,createVNode,nextTick,reactive,ref} from 'vue'

const settle=async()=>{await new Promise(resolve=>setImmediate(resolve));await nextTick()}
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no});return {promise,resolve,reject}}
const revision='44444444-4444-4444-8444-444444444444'
const guide=(id,title='已保存攻略')=>({id,title,content:'已保存正文',city:'杭州',is_draft:true,is_owner:true,version:1,revision,images:[],spots:[]})
async function mountEditor(api,id){
  const route=reactive({params:{id}}),user=ref({id:1}),navigations=[],listeners=new Map(),guards={}
  globalThis.window={addEventListener:(k,f)=>listeners.set(k,f),removeEventListener:k=>listeners.delete(k),confirm:()=>false}
  const data=new Map();globalThis.sessionStorage={getItem:k=>data.get(k)||null,setItem:(k,v)=>data.set(k,v),removeItem:k=>data.delete(k)}
  const wrapped={...api,get:path=>path==='/api/guides/write-capabilities'?Promise.resolve({idempotent_creation:true,versioned_writes:true}):api.get(path)}
  globalThis.__editorTest={api:wrapped,route,user,router:{resolve:path=>({href:'/travel'+path}),push:async path=>{navigations.push(path)}},guards}
  const url=new URL('../src/views/GuideEditView.vue',import.meta.url)
  const {descriptor}=parse(await readFile(url,'utf8'))
  const code=compileScript(descriptor,{id:'editor-regression'}).content
    .replace(/from ['"]vue['"]/g,`from ${JSON.stringify(import.meta.resolve('vue'))}`)
    .replace(/import \{[^\n]+\} from ['"]vue-router['"]/,`const {route,router,guards}=globalThis.__editorTest; const useRoute=()=>route,useRouter=()=>router,onBeforeRouteLeave=f=>guards.leave=f,onBeforeRouteUpdate=f=>guards.update=f`)
    .replace(/import \{ api \} from ['"]\.\.\/api['"]/, 'const api=globalThis.__editorTest.api')
    .replace(/import \{ useAuth \} from ['"]\.\.\/store\/auth['"]/, 'const useAuth=()=>({user:globalThis.__editorTest.user,openLogin(){}})')
    .replace(/import \{ registerAuthChangeGuard \} from ['"]\.\.\/store\/authChangeGuard['"]/, 'const registerAuthChangeGuard=f=>{guards.auth=f;return ()=>delete guards.auth}')
    .replace(/from ['"](\.\.[^'"]+)['"]/g,(_,path)=>`from ${JSON.stringify(new URL(path+'.js',url).href)}`)
  const {default:component}=await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}#${Math.random()}`)
  component.render=()=>null
  const renderer=createRenderer({createComment:()=>({}),insert(){},remove(){},parentNode:()=>null,nextSibling:()=>null,createText:()=>({}),setText(){},createElement:()=>({}),setElementText(){},patchProp(){}})
  const root={},vnode=createVNode(component);renderer.render(vnode,root);await settle()
  return {state:vnode.component.setupState,route,user,navigations,listeners,guards,unmount(){renderer.render(null,root)}}
}

test('攻略编辑草稿与上下文保护',async t=>{
  const previousWindow=globalThis.window,previousStorage=globalThis.sessionStorage
  try{
    await t.test('原内容无草稿，修改后保护离开/换号/刷新，恢复原值解除保护',async()=>{
      const m=await mountEditor({get:async()=>guide(1)},'1')
      try{
        const s=m.state;assert.equal(s.hasDraft,false);assert.equal(m.guards.leave(),true)
        s.form.title='未保存';assert.equal(s.hasDraft,true);assert.equal(m.guards.update(),false);assert.equal(m.guards.auth(),false)
        let prevented=false;const e={preventDefault(){prevented=true}};m.listeners.get('beforeunload')(e);assert(prevented)
        s.form.title='已保存攻略';assert.equal(s.hasDraft,false)
        s.imgUrl='https://example.invalid/a.png';assert.equal(s.hasDraft,true)
        s.imgUrl='';s.images.push('a');assert.equal(s.hasDraft,true)
      }finally{m.unmount()}
      assert.equal(m.listeners.size,0);assert.equal(m.guards.auth,undefined)
    })
    await t.test('加载失败和无编辑权限均不能提交空表单',async()=>{
      let writes=0
      const api={get:async()=>{throw new Error('读取失败')},patch:async()=>{writes++}}
      const m=await mountEditor(api,'1')
      try{
        assert.equal(m.state.ready,false);assert.equal(m.state.loadError,'读取失败')
        m.state.form.title='不能发送';m.state.form.content='正文';await m.state.submit(false);assert.equal(writes,0)
        api.get=async()=>({...guide(1),is_owner:false});await m.state.loadGuide();assert.equal(m.state.ready,false)
        api.get=async()=>guide(1);await m.state.loadGuide();assert.equal(m.state.ready,true);assert.equal(m.state.hasDraft,false)
      }finally{m.unmount()}
    })
    await t.test('复用编辑页时旧读取不能覆盖新攻略',async()=>{
      const old=deferred(),m=await mountEditor({get:path=>path.endsWith('/1')?old.promise:Promise.resolve(guide(2,'新攻略'))},'1')
      try{
        m.route.params.id='2';await settle();assert.equal(m.state.form.title,'新攻略')
        old.resolve(guide(1,'旧迟到结果'));await settle();assert.equal(m.state.form.title,'新攻略');assert.equal(m.state.hasDraft,false)
      }finally{m.unmount()}
    })
    await t.test('景点搜索乱序和失败均保留当前输入并拒绝旧候选',async()=>{
      const a=deferred(),b=deferred();let calls=0
      const api={get:()=>++calls===1?a.promise:b.promise},m=await mountEditor(api)
      try{
        const s=m.state;s.spotKeyword='旧';const first=s.searchSpots();s.spotKeyword='新';const second=s.searchSpots()
        b.resolve({items:[{id:2,name:'新景点'}]});await second;a.resolve({items:[{id:1,name:'旧景点'}]});await first
        assert.deepEqual(s.spotResults.map(x=>x.id),[2]);assert.equal(s.spotKeyword,'新')
        api.get=async()=>{throw new Error('搜索故障')};s.form.content='手动正文';s.spotKeyword='失败';await s.searchSpots()
        assert.equal(s.msg,'搜索故障');assert.equal(s.form.content,'手动正文');assert.equal(s.spotKeyword,'失败')
      }finally{m.unmount()}
    })
    await t.test('保存锁住重复提交，成功后更新基线再导航',async()=>{
      const pending=deferred();let writes=0
      const m=await mountEditor({post:()=>{writes++;return pending.promise}})
      try{
        const s=m.state;s.form.title='新攻略';s.form.content='正文';const saving=s.submit(true)
        await s.submit(false);assert.equal(writes,1);assert.equal(s.busy,true);assert.equal(m.guards.leave(),false)
        pending.resolve({id:3,is_draft:true,version:1,revision});await saving;assert.equal(s.hasDraft,false);assert.equal(s.busy,false);assert.deepEqual(m.navigations,['/me'])
      }finally{m.unmount()}
    })
    await t.test('未知创建沿用原标识继续，取消放弃保留快照',async()=>{
      let writes=0;const keys=[]
      const m=await mountEditor({post:async(path,body)=>{writes++;keys.push(body.request_id);const e=new Error('结果未知');e.outcomeUnknown=true;throw e}})
      try{
        const s=m.state;s.form.title='新攻略';s.form.content='正文';await s.submit(false);await s.submit(false)
        assert.equal(writes,2);assert.equal(keys[0],keys[1]);assert.equal(s.uncertain,true);assert.equal(s.form.content,'正文');s.abandonCreation();assert.equal(s.uncertain,true)
        window.confirm=()=>true;s.abandonCreation();assert.equal(s.uncertain,false);assert.equal(s.form.content,'正文')
      }finally{m.unmount()}
    })
    await t.test('旧账号保存迟到不能跳转或清掉新账号草稿',async()=>{
      let writes=0;const pending=deferred(),m=await mountEditor({post:()=>{writes++;return pending.promise}})
      try{
        const s=m.state;s.form.title='旧账号攻略';s.form.content='正文';const saving=s.submit(true)
        await settle();assert.equal(writes,1)
        m.user.value={id:2};await settle();s.form.title='新账号草稿'
        pending.resolve({id:4,is_draft:true,version:1,revision});await saving
        assert.equal(s.form.title,'新账号草稿');assert.equal(s.hasDraft,true);assert.deepEqual(m.navigations,[])
      }finally{m.unmount()}
    })
    await t.test('卸载后保存迟到不导航',async()=>{
      let writes=0;const pending=deferred(),m=await mountEditor({post:()=>{writes++;return pending.promise}})
      m.state.form.title='攻略';m.state.form.content='正文';const saving=m.state.submit(false);await settle();assert.equal(writes,1);m.unmount()
      pending.resolve({id:5,is_draft:false,version:1,revision});await saving;assert.deepEqual(m.navigations,[])
    })
  }finally{globalThis.window=previousWindow;globalThis.sessionStorage=previousStorage;delete globalThis.__editorTest}
})
