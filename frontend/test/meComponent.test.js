import test from 'node:test'
import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {parse,compileScript} from '@vue/compiler-sfc'
import {createRenderer,createVNode,nextTick,ref} from 'vue'
import {mergeUserInfo} from '../src/store/profileInfo.js'

const settle=async()=>{await new Promise(resolve=>setImmediate(resolve));await nextTick()}
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no});return {promise,resolve,reject}}
const profile=(id=1,version=1,nickname='原昵称')=>({id,version,nickname,avatar:'',username:'user'+id,is_admin:false,created_at:'2026-11-15',favorite_count:0,guide_count:0})
async function mountMe(api={}){
  const user=ref({id:1,nickname:'原昵称',avatar:'',profile_version:1}),accepted=[],listeners=new Map(),guards={}
  globalThis.window={addEventListener:(k,f)=>listeners.set(k,f),removeEventListener:k=>listeners.delete(k),confirm:()=>false}
  const acceptProfile=p=>{accepted.push(p);if(p.id===user.value?.id)Object.assign(user.value,mergeUserInfo(user.value,{...user.value,nickname:p.nickname,avatar:p.avatar,profile_version:p.version}))}
  globalThis.__meTest={api:{...api,get:path=>api.get?api.get(path):Promise.resolve(path.endsWith('/profile')?profile(user.value.id):[])},user,acceptProfile,guards}
  const url=new URL('../src/views/MeView.vue',import.meta.url),{descriptor}=parse(await readFile(url,'utf8'))
  const code=compileScript(descriptor,{id:'me-regression'}).content
    .replace(/from ['"]vue['"]/g,`from ${JSON.stringify(import.meta.resolve('vue'))}`)
    .replace(/import \{[^\n]+\} from ['"]vue-router['"]/,`const {guards}=globalThis.__meTest; const useRoute=()=>({query:{}}),useRouter=()=>({push:async()=>{}}),onBeforeRouteLeave=f=>guards.leave=f`)
    .replace(/import SpotCard from [^\n]+/,'const SpotCard={}').replace(/import \{ api \} from ['"]\.\.\/api['"]/, 'const api=globalThis.__meTest.api')
    .replace(/import \{ useAuth \} from ['"]\.\.\/store\/auth['"]/, 'const useAuth=()=>({user:globalThis.__meTest.user,acceptProfile:globalThis.__meTest.acceptProfile,openLogin(){}})')
    .replace(/import \{ registerAuthChangeGuard \} from ['"]\.\.\/store\/authChangeGuard['"]/, 'const registerAuthChangeGuard=f=>{guards.auth=f;return ()=>delete guards.auth}')
    .replace(/from ['"](\.\.[^'"]+)['"]/g,(_,path)=>`from ${JSON.stringify(new URL(path+'.js',url).href)}`)
  const {default:component}=await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}#${Math.random()}`);component.render=()=>null
  const renderer=createRenderer({createComment:()=>({}),insert(){},remove(){},parentNode:()=>null,nextSibling:()=>null,createText:()=>({}),setText(){},createElement:()=>({}),setElementText(){},patchProp(){}})
  const root={},vnode=createVNode(component);renderer.render(vnode,root);await settle()
  return {state:vnode.component.setupState,user,accepted,listeners,guards,unmount(){renderer.render(null,root)}}
}

test('个人中心独立恢复、草稿和账号保护',async t=>{
  const previousWindow=globalThis.window
  try{
    await t.test('收藏故障不挡住资料和攻略，列表重试保留昵称输入',async()=>{
      let failed=true
      const api={get:async path=>{if(path.endsWith('/profile'))return profile();if(path.endsWith('/favorites')&&failed)throw Error('收藏故障');return []}}
      const m=await mountMe(api)
      try{
        const s=m.state;assert.equal(s.profile.nickname,'原昵称');assert.equal(s.favoriteState.error,'收藏故障');assert.equal(s.guideState.error,'')
        s.nickname='未保存';failed=false;await s.loadList('favorites');assert.equal(s.nickname,'未保存');assert.equal(s.baseVersion,1);assert.equal(s.favoriteState.error,'')
      }finally{m.unmount()}
    })
    await t.test('昵称草稿保护离开、刷新和换号，卸载清理注册',async()=>{
      const m=await mountMe()
      const s=m.state;assert.equal(m.guards.leave(),true);s.nickname='未保存';assert.equal(m.guards.leave(),false);assert.equal(m.guards.auth(),false)
      let prevented=false;m.listeners.get('beforeunload')({preventDefault(){prevented=true}});assert(prevented)
      s.nickname='原昵称';assert.equal(s.hasDraft,false);assert.equal(m.guards.leave(),true)
      m.unmount();assert.equal(m.listeners.size,0);assert.equal(m.guards.auth,undefined)
    })
    await t.test('保存锁定重复请求，成功清草稿并同步导航账号资料',async()=>{
      const pending=deferred(),bodies=[];const m=await mountMe({patch:(path,body)=>{bodies.push(body);assert.equal(path,'/api/me/profile/edit');return pending.promise}})
      try{
        const s=m.state;s.nickname=' 新昵称 ';const saving=s.saveNickname();await s.saveNickname();assert.equal(bodies.length,1);assert.equal(s.saving,true)
        pending.resolve(profile(1,2,'新昵称'));await saving
        assert.deepEqual(bodies,[{nickname:'新昵称',version:1}]);assert.equal(s.hasDraft,false);assert.equal(s.nickname,'新昵称');assert.equal(m.user.value.nickname,'新昵称');assert.equal(m.user.value.profile_version,2)
      }finally{m.unmount()}
    })
    await t.test('结果未知核对不提升原版本，旧重试冲突后确认重载才替换草稿',async()=>{
      const bodies=[];let saved=false
      const m=await mountMe({get:async path=>path.endsWith('/profile')?profile(1,saved?2:1,saved?'新昵称':'原昵称'):[],patch:async(path,body)=>{
        bodies.push(body);const e=Error(saved?'版本冲突':'结果未知');if(saved)e.status=409;else{saved=true;e.outcomeUnknown=true}throw e
      }})
      try{
        const s=m.state;s.nickname='新昵称';await s.saveNickname();assert.equal(s.pendingWrite.version,1)
        await s.loadProfile();assert.equal(s.profile.version,2);assert.equal(s.baseVersion,1);assert.equal(s.nickname,'新昵称')
        await s.saveNickname();assert.equal(s.conflict,true);assert.deepEqual(bodies,[{nickname:'新昵称',version:1},{nickname:'新昵称',version:1}])
        await s.adoptCloudNickname();assert.equal(s.pendingWrite.version,1)
        window.confirm=()=>true;await s.adoptCloudNickname();assert.equal(s.baseVersion,2);assert.equal(s.hasDraft,false);assert.equal(s.pendingWrite,null)
      }finally{m.unmount()}
    })
    await t.test('资料核对乱序不回退资料或覆盖输入',async()=>{
      const api={get:async path=>path.endsWith('/profile')?profile():[]},m=await mountMe(api),old=deferred(),latest=deferred();let calls=0
      try{
        const s=m.state;s.nickname='本页输入';api.get=path=>path.endsWith('/profile')?(++calls===1?old.promise:latest.promise):Promise.resolve([])
        const a=s.loadProfile(),b=s.loadProfile();latest.resolve(profile(1,3,'最新云端'));await b;old.resolve(profile(1,2,'旧云端'));await a
        assert.equal(s.profile.nickname,'最新云端');assert.equal(s.nickname,'本页输入');assert.equal(s.baseVersion,1);assert.equal(s.profileState.loading,false)
      }finally{m.unmount()}
    })
    await t.test('不完整成功响应保持输入和原尝试，不冒充保存成功',async()=>{
      const m=await mountMe({patch:async()=>({})})
      try{const s=m.state;s.nickname='新昵称';await s.saveNickname();assert.equal(s.pendingWrite.version,1);assert.equal(s.baseVersion,1);assert.equal(s.nickname,'新昵称');assert.match(s.nickError,/无法确认/);assert.equal(s.saving,false)}finally{m.unmount()}
    })
    await t.test('旧账号已发送保存迟到，不能覆盖新账号草稿或同步旧资料',async()=>{
      const pending=deferred();let writes=0
      const api={patch:()=>{writes++;return pending.promise}},m=await mountMe(api)
      try{
        const s=m.state;s.nickname='旧账号输入';const saving=s.saveNickname();assert.equal(writes,1)
        api.get=async path=>path.endsWith('/profile')?profile(2,1,'新账号'):[];m.user.value={id:2,nickname:'新账号',profile_version:1};await settle();s.nickname='新账号草稿'
        pending.resolve(profile(1,2,'旧账号输入'));await saving
        assert.equal(s.nickname,'新账号草稿');assert.equal(s.profile.id,2);assert.equal(m.user.value.id,2);assert.equal(s.saving,false)
      }finally{m.unmount()}
    })
    await t.test('卸载后保存迟到不更新账号展示',async()=>{
      const pending=deferred(),m=await mountMe({patch:()=>pending.promise})
      m.state.nickname='待保存';const saving=m.state.saveNickname();const count=m.accepted.length;m.unmount();pending.resolve(profile(1,2,'待保存'));await saving
      assert.equal(m.accepted.length,count)
    })
    await t.test('老服务资料没有版本时不开放保存',async()=>{
      let writes=0;const m=await mountMe({get:async path=>path.endsWith('/profile')?{...profile(),version:undefined}:[],patch:async()=>{writes++}})
      try{assert.equal(m.state.profile,null);assert.match(m.state.profileState.error,/版本保护/);await m.state.saveNickname();assert.equal(writes,0)}finally{m.unmount()}
    })
  }finally{globalThis.window=previousWindow;delete globalThis.__meTest}
})
