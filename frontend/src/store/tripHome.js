import {ref,reactive,computed} from 'vue'
import {validPlanMeta} from './planHistory.js'
import {homeTransferAttempt,readHomeTransfer,saveHomeTransfer,clearHomeTransfer} from './homeTransferAttempt.js'

const validTrip=t=>Number.isSafeInteger(t?.id)&&t.id>0&&Number.isSafeInteger(t.version)&&t.version>0&&typeof t.title==='string'&&Array.isArray(t.days)&&t.days.every(d=>Array.isArray(d.items))
const source=()=>({items:[],loading:false,error:'',loaded:false,seq:0})
export function createTripHome({api,guestTrips,owner,storage}){
  const trips=reactive(source()),history=reactive(source()),local=reactive(source()),migrations=ref({})
  const operation=ref(null),recoveryError=ref(''),notice=ref('')
  const preparing=ref(false)
  let epoch=0,alive=true
  const stamp=()=>({epoch,owner:owner()})
  const active=s=>alive&&s.epoch===epoch&&s.owner===owner()
  const hasPending=computed(()=>Boolean(recoveryError.value||operation.value&&!operation.value.resolved))
  const locked=computed(()=>preparing.value||hasPending.value)
  async function loadOne(name){
    const state={trips,history,local}[name],s=stamp(),seq=++state.seq
    if(!s.owner&&name!=='local')return
    state.loading=true;state.error=''
    try{
      const r=name==='local'?await guestTrips.list():await api.get(name==='trips'?'/api/trips':'/api/plan/history',{timeout:10000})
      if(!active(s)||state.seq!==seq)return
      if(!Array.isArray(r?.items)||r.items.some(x=>name==='trips'?!validTrip(x):name==='history'?!validPlanMeta(x):!/^guest-/.test(x?.id)||!Array.isArray(x.days)||x.days.some(d=>!Array.isArray(d.items))))throw Error('返回的数据不完整，请重试。')
      state.items=r.items;state.loaded=true
      if(name==='local'&&s.owner){
        const records=await Promise.allSettled(r.items.map(async t=>[t.id,(await guestTrips.migrationHistory(t.id)).find(m=>m.user_id===s.owner&&m.guest_version===t.version)||null]))
        if(!active(s)||state.seq!==seq)return
        migrations.value=Object.fromEntries(records.filter(r=>r.status==='fulfilled').map(r=>r.value))
        if(records.some(r=>r.status==='rejected'))state.error='部分本机复制记录暂不可读；本机原件仍保留，可重试。'
      }
    }catch(e){if(active(s)&&state.seq===seq)state.error=e.message||'加载失败，请重试。'}
    finally{if(active(s)&&state.seq===seq)state.loading=false}
  }
  async function load(name){if(name)return loadOne(name);await Promise.allSettled(['trips','history','local'].map(loadOne))}
  function reset(){
    ++epoch;preparing.value=false;operation.value=null;recoveryError.value='';notice.value='';migrations.value={}
    for(const state of [trips,history,local])Object.assign(state,source())
    if(owner())try{const p=readHomeTransfer(storage(),owner());if(p)operation.value={...p,busy:false,error:'本标签保留了一次待确认操作，请核对或继续原次。',trip:null,resolved:false}}
    catch(e){recoveryError.value=e.message}
    return load()
  }
  function dispose(){alive=false;++epoch}
  function current(s,op){return active(s)&&operation.value===op}
  async function complete(op,r,s){
    const bound=r?.owner_id===s.owner&&(op.kind==='plan'?r.plan_id===op.source_id&&r.plan_revision===op.body.revision:r.guest_id===op.source_id&&r.guest_version===op.body.guest_version)
    if(!bound||!validTrip(r?.trip)||op.kind==='plan'&&r.trip.source_plan_id!==op.source_id)throw Error('操作回执无法确认，请继续核对原次，避免另建旅行。')
    op.trip=r.trip
    if(op.kind==='local')try{await guestTrips.markMigrated(op.source_id,op.body.guest_version,s.owner,r.trip.id)}catch{if(current(s,op))notice.value='账号复制已确认，但本机复制标记未保存；原件仍在，可打开账号旅行。'}
    if(!current(s,op))return
    try{clearHomeTransfer(storage(),s.owner);op.resolved=true;op.error=''}
    catch{op.error='已确认账号旅行，但本标签恢复标记未清除，请检查存储后再继续确认。';return}
    load('trips');load('local')
  }
  async function run(check=false){
    const op=operation.value,s=stamp();if(!op||op.busy||op.resolved||!s.owner)return
    op.busy=true;op.error=''
    try{
      const cap=await api.get(op.kind==='plan'?'/api/trips/plan-import-capabilities':'/api/trips/guest-import-capabilities')
      if(!current(s,op))return
      if(op.kind==='plan'?cap.recoverable_conversion!==true:cap.idempotent_import!==true||cap.status_lookup!==true||cap.reservation_records!==true)throw Error('当前服务不支持安全恢复，请更新服务后继续；不会使用旧转换接口。')
      if(op.kind==='local'&&Object.keys(op.body.trip?.walking_preferences||{}).length&&cap.walking_preferences!==true)throw Error('当前服务无法复制步行偏好，请升级后再继续。')
      if(op.kind==='local'&&op.body.trip?.days?.some(day=>Object.keys(day.route_preferences||{}).length)&&cap.route_preferences!==true)throw Error('当前服务无法复制分段交通方式，请升级后再继续。')
      const r=op.kind==='plan'?(check?await api.get(`/api/trips/from-plan/${op.source_id}/conversion?revision=${op.body.revision}`):await api.post(`/api/trips/from-plan/${op.source_id}/convert`,op.body)):
        await api.post('/api/trips/import-guest'+(check?'/check':''),op.body)
      if(!current(s,op))return
      if(check&&r?.state==='not_created'){
        if(r.owner_id!==s.owner||op.kind==='plan'&&(r.plan_id!==op.source_id||r.plan_revision!==op.body.revision)||op.kind==='local'&&(r.guest_id!==op.source_id||r.guest_version!==op.body.guest_version))throw Error('核对结果与原次不符，请重试。')
        op.error='服务器尚未找到本次结果；原快照保持不变，可继续原次操作。'
      }else await complete(op,r,s)
    }catch(e){if(current(s,op))op.error=e.message||'结果无法确认，请核对后继续。'}
    finally{if(current(s,op))op.busy=false}
  }
  async function begin(kind,row){
    if(!owner()||locked.value)return
    const s=stamp();preparing.value=true;notice.value=''
    try{
      const body=kind==='plan'?{version:row.version,revision:row.revision}:await guestTrips.prepareMigration(row.id)
      if(!active(s))return
      const attempt=homeTransferAttempt({kind,source_id:row.id,label:row.title||'旅行',body})
      saveHomeTransfer(storage(),s.owner,attempt)
      operation.value={...attempt,busy:false,error:'',trip:null,resolved:false}
      await run()
    }catch(e){if(active(s))notice.value=e.message}
    finally{if(active(s))preparing.value=false}
  }
  function discard(){
    if(operation.value?.busy||!owner())return
    if(!window.confirm('放弃只清除本标签恢复信息，不会删除已转换或复制的账号旅行。请先核对账号旅行，确定放弃吗？'))return
    try{clearHomeTransfer(storage(),owner());operation.value=null;recoveryError.value='';notice.value='本标签恢复已结束，已发请求仍可能完成，请先核对账号旅行。'}catch(e){notice.value='无法清除恢复信息，请检查浏览器存储。'}
  }
  return {trips,history,local,migrations,operation,recoveryError,notice,hasPending,locked,load,reset,dispose,begin,retry:()=>run(false),check:()=>run(true),discard}
}
