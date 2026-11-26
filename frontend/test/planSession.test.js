import test from 'node:test'
import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {ref,nextTick} from 'vue'
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no});return {promise,resolve,reject}}
const settle=async()=>{await nextTick();await new Promise(r=>setImmediate(r))}
async function harness({user=ref({id:1}),api={},job={}}={}){
  const data=new Map(),guards=[]
  globalThis.sessionStorage={getItem:k=>data.get(k)||null,setItem:(k,v)=>data.set(k,String(v)),removeItem:k=>data.delete(k)}
  globalThis.window={addEventListener(){},confirm:()=>true}
  const ctx={user,guards,api:{get:async()=>({items:[]}),post:async()=>({matches:{},nearby:[]}),patch:async()=>{},...api},job:{capabilities:async()=>({reliable_creation:true,scoped_guests:true}),active:async()=>({job:null}),cancel:async()=>{},poll:async()=>new Promise(()=>{}),...job}}
  const rawCreate=ctx.job.create
  ctx.job.create=async body=>{const owner_id=ctx.user.value?.id??0,r=await rawCreate(body);return {status:'running',request_id:body.request_id,owner_id,...r}}
  ctx.job.cancelCreation=job.cancelCreation|| (async body=>{if(job.cancel)await job.cancel(body);return {job_id:null,status:'cancelled',request_id:body.request_id,owner_id:ctx.user.value?.id??0}})
  const rawGet=ctx.api.get
  ctx.api.get=async(...args)=>{const r=await rawGet(...args);return r?.id&&r.itinerary?{version:1,revision:'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',status:'completed',...r}:r}
  globalThis.__planHarness=ctx
  const source=await readFile(new URL('../src/store/planSession.js',import.meta.url),'utf8')
  const code=source.replace("from 'vue'",`from ${JSON.stringify(import.meta.resolve('vue'))}`)
    .replace("import { api, planJob } from '../api'",'const {api,job:planJob}=globalThis.__planHarness')
    .replace("import { useAuth } from './auth'",'const useAuth=()=>({user:globalThis.__planHarness.user})')
    .replace("import { fmtMin } from '../utils/format'",'const fmtMin=n=>String(n)')
    .replace("from '../utils/planLocation'",`from ${JSON.stringify(new URL('../src/utils/planLocation.js',import.meta.url).href)}`)
    .replace("import { registerAuthChangeGuard } from './authChangeGuard'",'const registerAuthChangeGuard=f=>globalThis.__planHarness.guards.push(f)')
    .replace("from './planCreationAttempt'",`from ${JSON.stringify(new URL('../src/store/planCreationAttempt.js',import.meta.url).href)}`)
    .replace("from './planHistory'",`from ${JSON.stringify(new URL('../src/store/planHistory.js',import.meta.url).href)}`)
    + '\nexport const __test={pollOnce,applySnapshot,persistNow,readJobId,historyCtl,getId:()=>jobId,stop(){clearTimeout(pollTimer);clearTimeout(persistTimer);clearTimeout(saveTimer);stopTyping();historyCtl.reset()}}'
  const mod=await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}#${Math.random()}`)
  return {...ctx,data,state:mod.usePlanSession(),internal:mod.__test}
}

test('原规划任务恢复与账号视图隔离',async t=>{
  const oldWindow=globalThis.window,oldStorage=globalThis.sessionStorage
  try{
    await t.test('换号时保留原账号任务，新账号不取消它，旧创建失败不清新忙碌状态',async()=>{
      const a=deferred(),b=deferred(),cancels=[];let calls=0
      const h=await harness({job:{create:()=>++calls===1?a.promise:b.promise,cancel:async id=>cancels.push(id)}})
      try{
        const first=h.state.send('旧账号需求');await settle();h.user.value={id:2};await settle()
        assert.equal(h.state.busy.value,false);assert.equal(h.state.messages.value.length,0)
        const second=h.state.send('新账号需求');a.reject(Error('旧请求失败'));await first
        assert.equal(h.state.busy.value,true);assert.equal(h.state.messages.value[0].text,'新账号需求')
        b.resolve({job_id:'11111111111111111111111111111111'});await second;assert.equal(h.internal.getId(),'11111111111111111111111111111111');assert.deepEqual(cancels,[])
        assert.match(h.data.get('tp_plan_session_v2.1'),/旧账号需求/)
      }finally{h.internal.stop()}
    })
    await t.test('已知任务按账号保留，迟到轮询不能覆盖新账号，切回可恢复',async()=>{
      const old=deferred();let first=true
      const id='22222222222222222222222222222222',h=await harness({job:{create:async()=>({job_id:id}),poll:()=>first?(first=false,old.promise):new Promise(()=>{})}})
      try{
        await h.state.send('原始需求');const polling=h.internal.pollOnce();h.user.value={id:2};await settle()
        old.resolve({job_id:id,status:'done',seq:1,events:[],text:'他人的文字',result:{done:false,question:'他人的追问'}});await polling
        assert.equal(h.state.messages.value.length,0);assert.equal(h.state.busy.value,false)
        h.user.value={id:1};await settle();assert.equal(h.internal.getId(),id);assert.equal(h.state.busy.value,true);assert.equal(h.state.messages.value[0].text,'原始需求')
      }finally{h.internal.stop()}
    })
    await t.test('恢复reset清旧片段，完整快照不重复追加已缓存追问',async()=>{
      const h=await harness({job:{create:async()=>({job_id:'33333333333333333333333333333333'})}})
      try{
        await h.state.send('需求')
        h.internal.applySnapshot({events:[{seq:1,type:'token',data:{text:'旧片段'}}],text:'旧片段'})
        h.internal.applySnapshot({events:[{seq:2,type:'reset',data:{message:'正在恢复'}},{seq:3,type:'token',data:{text:'新追问'}}],text:'新追问'})
        h.internal.applySnapshot({events:[{seq:1,type:'token',data:{text:'旧片段'}},{seq:3,type:'token',data:{text:'新追问'}}],text:'新追问'})
        assert.equal(h.state.messages.value.at(-1).text,'新追问');assert.deepEqual([...h.state.logs.value],['正在恢复'])
        h.internal.applySnapshot({events:[],result:{done:false,question:'完整追问'}});assert.equal(h.state.messages.value.at(-1).text,'完整追问')
      }finally{h.internal.stop()}
    })
    await t.test('刷新复用正在等待的同一气泡，不额外插入重复追问',async()=>{
      const h=await harness()
      try{
        h.data.set('tp_plan_session_v2.1',JSON.stringify({v:1,ownerId:1,messages:[{role:'user',text:'需求'},{role:'ai',text:'旧追问片段'}]}))
        h.data.set('tp_plan_job_v2.1',JSON.stringify({ownerId:1,id:'saved-job'}))
        h.state.hydrate();assert.equal(h.state.messages.value.length,2)
        h.internal.applySnapshot({events:[{seq:1,type:'token',data:{text:'完整追问'}}],text:'完整追问'})
        assert.equal(h.state.messages.value.length,2);assert.equal(h.state.messages.value[1].text,'完整追问')
      }finally{h.internal.stop()}
    })
    await t.test('取消响应未知保留同次标识与取消意图，不改回创建',async()=>{
      const h=await harness({job:{create:async()=>({job_id:'44444444444444444444444444444444'}),cancel:async()=>{throw Error('网络断开')}}})
      try{
        await h.state.send('需求');await h.state.stopGeneration()
        assert.equal(h.state.busy.value,true);assert.equal(h.internal.getId(),'44444444444444444444444444444444');assert.equal(h.state.stopping.value,false);assert.equal(h.state.pendingCreation.value.mode,'cancel');assert.match(h.state.stopError.value,/停止结果待确认/)
      }finally{h.internal.stop()}
    })
    await t.test('迟到历史及住宿不能回填新账号，账号确认可取消',async()=>{
      const history=deferred(),hotels=deferred()
      let reads=0
      const h=await harness({api:{get:()=>++reads===1?history.promise:Promise.resolve({items:[]}),post:()=>hotels.promise},job:{create:async()=>({job_id:'55555555555555555555555555555555'})}})
      try{
        await h.state.send('原账号');h.internal.applySnapshot({events:[],result:{done:true,itinerary:{title:'旧方案',days:[],stays:[{name:'旧住宿'}]}}})
        h.state.input.value='保留草稿';window.confirm=()=>false;assert.equal(h.guards[0](),false)
        h.user.value={id:2};await settle();history.resolve({items:[{id:99,title:'旧历史'}]});hotels.resolve({matches:{old:{}},nearby:[{}]});await settle()
        assert.equal(h.state.history.value.length,0)
        assert.deepEqual({...h.state.stayMatches.value},{});assert.equal(h.state.nearbyHotels.value.length,0)
        assert.equal(h.state.messages.value.length,0)
      }finally{h.internal.stop()}
    })
    await t.test('创建丢响应后固定同次内容重试，新需求不能覆盖尝试',async()=>{
      const calls=[],id='66666666666666666666666666666666'
      const h=await harness({job:{create:async body=>{calls.push(body);if(calls.length===1)throw Error('丢响应');return {job_id:id}},poll:async()=>({job_id:id,status:'done',seq:1,events:[],text:'',result:{done:false,question:'已找回'}})}})
      try{
        await h.state.send('原提交');assert.equal(h.state.creationUncertain.value,true);assert.equal(h.state.busy.value,false)
        await h.state.send('不同需求');assert.equal(h.state.startFresh({text:'覆盖'}),false);assert.equal(calls.length,1)
        await h.state.resumeCreation();assert.deepEqual(calls[1],calls[0]);assert.equal(h.internal.getId(),id)
        await h.internal.pollOnce();assert.equal(h.state.pendingCreation.value,null);assert.equal(h.data.get('travel.plan-creation.v1.1'),undefined)
      }finally{h.internal.stop()}
    })
    await t.test('停止先于创建回执，晚到创建不能接回已取消任务',async()=>{
      const creating=deferred(),creates=[],cancels=[]
      const h=await harness({job:{create:body=>{creates.push(body);return creating.promise},cancelCreation:async body=>{cancels.push(body);return {job_id:null,status:'cancelled',request_id:body.request_id,owner_id:1}}}})
      try{
        const sending=h.state.send('待取消需求');await settle();await h.state.stopGeneration();assert.equal(h.state.pendingCreation.value,null);assert.equal(h.state.busy.value,false)
        creating.resolve({job_id:'77777777777777777777777777777777'});await sending
        assert.equal(h.internal.getId(),null);assert.equal(h.state.busy.value,false);assert.deepEqual(cancels[0],creates[0]);assert.match(h.state.messages.value.at(-1).text,/已取消/)
      }finally{h.internal.stop()}
    })
    await t.test('未知创建后停止也丢响应，继续仅重复取消不再创建',async()=>{
      let creates=0,cancels=0
      const h=await harness({job:{create:async()=>{creates++;throw Error('create lost')},cancelCreation:async body=>{if(++cancels===1)throw Error('cancel lost');return {job_id:null,status:'cancelled',request_id:body.request_id,owner_id:1}}}})
      try{
        await h.state.send('需求');await h.state.stopGeneration();assert.equal(h.state.pendingCreation.value.mode,'cancel')
        await h.state.resumeCreation();assert.equal(creates,1);assert.equal(cancels,2);assert.equal(h.state.pendingCreation.value,null)
      }finally{h.internal.stop()}
    })
    await t.test('无法写提交快照时不POST并保留输入',async()=>{
      let writes=0;const h=await harness({job:{create:async()=>{writes++;return {}}}})
      try{
        sessionStorage.setItem=()=>{throw Error('quota')};h.state.input.value='保留输入';await h.state.send()
        assert.equal(writes,0);assert.equal(h.state.input.value,'保留输入');assert.equal(h.state.pendingCreation.value,null);assert.match(h.state.creationStatus.value,/无法保留/)
      }finally{h.internal.stop()}
    })
    await t.test('旧服务不创建，坏成功回执仍保留原标识',async()=>{
      let writes=0;const h=await harness({job:{capabilities:async()=>({}),create:async()=>{writes++;return {job_id:'bad'}}}})
      try{
        h.state.input.value='需求';await h.state.send();assert.equal(writes,0);assert.equal(h.state.pendingCreation.value,null)
        h.job.capabilities=async()=>({reliable_creation:true,scoped_guests:true});await h.state.send()
        assert.equal(writes,1);assert(h.state.pendingCreation.value);assert.equal(h.internal.getId(),null);assert.equal(h.state.busy.value,false);assert.match(h.state.creationStatus.value,/回执无法确认/)
      }finally{h.internal.stop()}
    })
    await t.test('回看历史期间继续提交，错误不改历史气泡，成功回到原对话',async()=>{
      const calls=[];const h=await harness({api:{get:async()=>({id:41,intent:{},chat:[{role:'ai',text:'历史气泡原文'}],itinerary:{days:[]}})},job:{create:async body=>{calls.push(body);if(calls.length<3)throw Error('丢响应');return {job_id:'99999999999999999999999999999999'}}}})
      try{
        await h.state.send('原请求');await h.state.loadPlan(41);await h.state.resumeCreation()
        assert.equal(h.state.historyView.value,true);assert.equal(h.state.messages.value[0].text,'历史气泡原文')
        await h.state.resumeCreation();assert.equal(h.state.historyView.value,false);assert.equal(h.state.messages.value[0].text,'原请求');assert.deepEqual(calls[2],calls[0])
      }finally{h.internal.stop()}
    })
    await t.test('已知任务404后仍显示原次提交，不能隐形锁定',async()=>{
      const error=Object.assign(Error('expired'),{status:404})
      const h=await harness({job:{create:async()=>({job_id:'88888888888888888888888888888888'}),poll:async()=>{throw error}}})
      try{
        await h.state.send('原需求');await h.internal.pollOnce()
        assert.equal(h.state.busy.value,false);assert.equal(h.internal.getId(),null);assert.equal(h.state.creationUncertain.value,true);assert(h.state.pendingCreation.value);assert.match(h.state.creationStatus.value,/不存在或已过期/)
      }finally{h.internal.stop()}
    })
    await t.test('刷新恢复取消模式，不自动改成创建',async()=>{
      const h=await harness({job:{create:async()=>{throw Error('lost')},cancelCreation:async()=>{throw Error('lost cancel')}}})
      try{
        await h.state.send('原需求');await h.state.stopGeneration();const old=h.state.pendingCreation.value
        h.state.hydrate();assert.equal(h.state.pendingCreation.value.mode,'cancel');assert.equal(h.state.pendingCreation.value.request_id,old.request_id);assert.equal(h.state.messages.value[0].text,'原需求')
      }finally{h.internal.stop()}
    })
    await t.test('原方案删除回执迟到不清除新一轮生成和输入',async()=>{
      const deleting=deferred(),revision='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';let creates=0
      const h=await harness({api:{del:()=>deleting.promise},job:{create:async()=>({job_id:++creates===1?'11111111111111111111111111111111':'22222222222222222222222222222222'}),poll:async()=>({job_id:'11111111111111111111111111111111',status:'done',seq:0,events:[],result:{done:true,plan_id:41,saved:false,itinerary:{title:'原方案',days:[]}}})}})
      try{
        await h.state.send('原需求');await h.internal.pollOnce()
        const row={id:41,version:1,revision,status:'completed',title:'原方案'};h.internal.historyCtl.register(row)
        const removing=h.state.deletePlan(row);await settle();await h.state.send('新一轮需求')
        deleting.resolve({...row,version:2,status:'deleted',can_restore:true,restore_seconds_remaining:8});await removing
        assert.equal(h.state.busy.value,true);assert.equal(h.internal.getId(),'22222222222222222222222222222222');assert.equal(h.state.messages.value.at(-2).text,'新一轮需求');assert.equal(h.state.result.value,null)
      }finally{h.internal.stop()}
    })
    await t.test('旧账号历史读取后返回不污染新账号，旧存档迁移不串号',async()=>{
      const old=deferred();let reads=0
      const h=await harness({api:{get:()=>++reads===1?old.promise:Promise.resolve({items:[]})}})
      try{
        h.data.set('tp_plan_session_v1',JSON.stringify({v:1,ownerId:3,messages:[{role:'user',text:'旧存档'}]}));h.data.set('tp_plan_job_v1','legacy-job')
        h.state.hydrate();const reading=h.state.refreshHistory();h.user.value={id:2};await settle();old.resolve({items:[{id:10}]});await reading;assert.equal(h.state.history.value.length,0);assert.equal(h.internal.getId(),null)
        h.user.value={id:3};await settle();assert.equal(h.internal.getId(),'legacy-job');assert.equal(h.state.messages.value[0].text,'旧存档')
      }finally{h.internal.stop()}
    })
  }finally{globalThis.window=oldWindow;globalThis.sessionStorage=oldStorage;delete globalThis.__planHarness}
})
