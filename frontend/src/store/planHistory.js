import {ref,reactive,computed} from 'vue'
const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const clone=x=>JSON.parse(JSON.stringify(x))
const freeze=x=>{if(x&&typeof x==='object'){Object.values(x).forEach(freeze);Object.freeze(x)}return x}
const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b)
export function validPlanMeta(p,id=p?.id){return Number.isSafeInteger(id)&&id>0&&p?.id===id&&Number.isSafeInteger(p.version)&&p.version>0&&uuid.test(p.revision||'')}
const validState=(p,id)=>validPlanMeta(p,id)&&['completed','deleted'].includes(p.status)&&typeof p.can_restore==='boolean'&&Number.isSafeInteger(p.restore_seconds_remaining)&&p.restore_seconds_remaining>=0&&p.restore_seconds_remaining<=8

/** Each write keeps its original base; reads never silently rebase a draft. */
export function createPlanHistory({api,owner}){
  const items=ref([]),listLoading=ref(false),listError=ref(''),detailLoading=ref(null),detailError=ref('')
  const records=reactive(new Map()),operation=ref(null)
  let scope=0,listSeq=0,detailSeq=0
  const stamp=()=>({scope,owner:owner()})
  const active=s=>s.scope===scope&&s.owner===owner()
  const hasPending=computed(()=>Boolean(operation.value||[...records.values()].some(e=>e.pending||e.queued||e.saving)))
  function invalidateReads(){++listSeq;++detailSeq;detailLoading.value=null;listLoading.value=false}
  function reset(){invalidateReads();++scope;for(const e of records.values())clearTimeout(e.timer);records.clear();items.value=[];operation.value=null;listError.value='';detailError.value=''}
  function register(p,{replace=false}={}){
    if(!validPlanMeta(p))return null
    let e=records.get(p.id)
    if(!e||replace){
      if(e)clearTimeout(e.timer)
      records.set(p.id,{id:p.id,version:p.version,revision:p.revision,status:p.status||'completed',
        detail:Array.isArray(p.chat)&&p.itinerary?clone(p):null,chat:Array.isArray(p.chat)?clone(p.chat):null,map:p.map_state?clone(p.map_state):null,
        pending:null,queued:null,saving:false,error:'',checked:null,checking:false,timer:null})
      e=records.get(p.id)
    }else if(!e.detail&&Array.isArray(p.chat)&&p.itinerary&&p.version===e.version&&p.revision===e.revision){
      e.detail=clone(p);e.chat=clone(p.chat);e.map=clone(p.map_state||{})
    }
    return e
  }
  async function refresh(){
    const s=stamp(),seq=++listSeq
    if(!s.owner){items.value=[];return}
    listLoading.value=true;listError.value=''
    try{
      const r=await api.get('/api/plan/history')
      if(!active(s)||seq!==listSeq)return
      if(!Array.isArray(r?.items)||r.items.some(p=>!Number.isSafeInteger(p?.id)))throw Error('无法读取历史列表，请重试。')
      items.value=r.items
      for(const p of r.items)if(!records.has(p.id))register(p)
    }catch(e){if(active(s)&&seq===listSeq)listError.value=e.message}
    finally{if(active(s)&&seq===listSeq)listLoading.value=false}
  }
  async function read(id,{discard=false}={}){
    const s=stamp(),seq=++detailSeq,e=records.get(id)
    detailError.value='';detailLoading.value=id
    if(e&&(e.pending||e.queued||e.saving)&&!discard&&e.detail){
      detailLoading.value=null
      const local={...clone(e.detail)},desired={...(e.pending||{}),...(e.queued||{})}
      if(desired.messages)local.chat=clone(desired.messages)
      if(desired.map_state)local.map_state=clone(desired.map_state)
      return local
    }
    if(e?.saving){detailLoading.value=null;detailError.value='快照正在保存，请稍后再重新载入。';return null}
    try{
      const p=await api.get(`/api/plan/${id}`)
      if(!active(s)||seq!==detailSeq)return null
      if(!validPlanMeta(p,id)||!Array.isArray(p.chat)||!Array.isArray(p.itinerary?.days)||p.status!=='completed')throw Error('无法确认历史方案及版本，请更新服务或重试。')
      if(e&&!discard&&(p.revision!==e.revision||p.version<e.version))throw Error('历史方案已变化，请重新核对后载入。')
      register(p,{replace:true});return p
    }catch(error){if(active(s)&&seq===detailSeq)detailError.value=error.message;return null}
    finally{if(active(s)&&seq===detailSeq)detailLoading.value=null}
  }
  function queue(id,body,{delay=0,expected=null}={}){
    const e=records.get(id)
    if(!e||!e.detail||e.status!=='completed'||!validPlanMeta(e)){detailError.value='无法确认快照版本，请先重新打开历史方案。';return}
    if(expected&&(expected.version!==e.version||expected.revision!==e.revision)){e.error='生成时的快照版本已变化，本次未自动覆盖，请重新读取。';return}
    if(operation.value?.id===id){e.error='请先核对本次删除或恢复结果。';return}
    const clean=clone(body)
    if(!e.pending&&!e.queued&&Object.entries(clean).every(([k,v])=>same(v,k==='messages'?e.chat:e.map)))return
    e.queued=freeze({...e.queued,version:e.queued?.version??e.version,revision:e.revision,...clean})
    clearTimeout(e.timer)
    if(!e.pending&&!e.saving&&!e.error)e.timer=setTimeout(()=>run(e),delay)
  }
  async function run(e){
    if(e.saving||e.error||records.get(e.id)!==e)return
    const s=stamp()
    e.saving=true;clearTimeout(e.timer)
    try{
      while(active(s)&&records.get(e.id)===e&&(e.pending||e.queued)){
        if(!e.pending){e.pending=e.queued;e.queued=null}
        const body=e.pending
        const r=await api.patch(`/api/plan/${e.id}/snapshot/edit`,body)
        if(!active(s)||records.get(e.id)!==e)return
        if(!validPlanMeta(r,e.id)||r.revision!==body.revision||r.version!==body.version+1||r.status!=='completed')throw Error('快照保存结果无法确认，请核对后继续。')
        e.version=r.version;e.status=r.status
        if(body.messages)e.chat=clone(body.messages)
        if(body.map_state)e.map=clone(body.map_state)
        if(e.detail){e.detail.version=e.version;e.detail.chat=clone(e.chat||[]);e.detail.map_state=clone(e.map||{})}
        e.pending=null;e.checked=null
        if(e.queued){
          const unchanged=Object.entries(e.queued).filter(([k])=>['messages','map_state'].includes(k)).every(([k,v])=>same(v,k==='messages'?e.chat:e.map))
          e.queued=unchanged?null:freeze({...e.queued,version:e.version})
        }
      }
    }catch(error){if(active(s)&&records.get(e.id)===e)e.error=error.message||'快照保存待确认。'}
    finally{if(active(s)&&records.get(e.id)===e)e.saving=false}
  }
  async function retry(id){const e=records.get(id);if(e&&!e.saving){e.error='';await run(e)}}
  function discardSnapshot(id){const e=records.get(id);if(!e||e.saving)return;clearTimeout(e.timer);e.pending=null;e.queued=null;e.status='unverified';e.error='本页快照尝试已结束，请重新载入后再保存。'}
  async function checkSnapshot(id){
    const e=records.get(id),s=stamp();if(!e||e.checking||e.saving)return
    e.checking=true
    try{const p=await api.get(`/api/plan/${id}/state`);if(!active(s)||records.get(id)!==e)return
      if(!validState(p,id)||p.revision!==e.revision||p.version<e.version)throw Error('无法核对原快照记录，请重新载入。')
      e.checked=p
    }catch(error){if(active(s)&&records.get(id)===e)e.error=error.message}
    finally{if(active(s)&&records.get(id)===e)e.checking=false}
  }
  function applyMutation(op,r,elapsed=0){
    const remaining=Math.max(0,r.restore_seconds_remaining-Math.floor(elapsed/1000))
    r={...r,restore_seconds_remaining:remaining,can_restore:r.can_restore&&remaining>0}
    const e=records.get(op.id)
    if(e&&e.revision===op.revision){e.version=r.version;e.status=r.status;e.checked=r;e.error='';if(e.detail){e.detail.version=r.version;e.detail.status=r.status}}
    ++listSeq;listLoading.value=false
    if(r.status==='deleted')items.value=items.value.filter(p=>p.id!==op.id)
    else items.value=items.value.map(p=>p.id===op.id?{...p,version:r.version,status:r.status}:p)
    operation.value=null
    return {...r,title:op.title,kind:op.kind}
  }
  async function runMutation(op){
    const s=stamp(),started=performance.now();op.busy=true;op.error=''
    try{
      const body={version:op.version,revision:op.revision}
      const r=op.kind==='delete'?await api.del(`/api/plan/${op.id}/content?version=${body.version}&revision=${encodeURIComponent(body.revision)}`):await api.post(`/api/plan/${op.id}/content/restore`,body)
      if(!active(s)||operation.value!==op)return null
      if(!validState(r,op.id)||r.revision!==op.revision||r.version!==op.version+1||r.status!==(op.kind==='delete'?'deleted':'completed'))throw Error('操作回执无法确认，请先核对服务器状态。')
      return applyMutation(op,r,performance.now()-started)
    }catch(error){if(active(s)&&operation.value===op)op.error=error.message;return null}
    finally{if(active(s)&&operation.value===op)op.busy=false}
  }
  async function mutate(row,kind){
    if(operation.value)return null
    const e=records.get(row.id),meta=kind==='restore'?row:e||row
    if(!validPlanMeta(meta,row.id)){detailError.value='缺少历史版本，请重新读取后再操作。';return null}
    if(e&&(e.pending||e.queued||e.saving)){detailError.value='请先处理该快照的保存结果，再删除或恢复。';return null}
    invalidateReads()
    operation.value={id:row.id,title:row.title||'历史方案',kind,version:meta.version,revision:meta.revision,busy:false,error:'',checked:null}
    return runMutation(operation.value)
  }
  async function retryMutation(){const op=operation.value;if(op&&!op.busy)return runMutation(op);return null}
  async function checkMutation(){
    const op=operation.value,s=stamp(),started=performance.now();if(!op||op.busy)return null
    op.busy=true;op.error=''
    try{
      const p=await api.get(`/api/plan/${op.id}/state`)
      if(!active(s)||operation.value!==op)return null
      if(!validState(p,op.id)||p.revision!==op.revision)throw Error('记录标识已变化或状态不完整，原操作不会改写新记录。')
      op.checked=p
      if(p.version===op.version+1&&p.status===(op.kind==='delete'?'deleted':'completed'))return applyMutation(op,p,performance.now()-started)
      op.error='已读取服务器状态，本次原版本和输入保持不变；可继续原操作或结束核对。'
    }catch(error){if(active(s)&&operation.value===op)op.error=error.message}
    finally{if(active(s)&&operation.value===op)op.busy=false}
    return null
  }
  function endMutation(){if(operation.value&&!operation.value.busy)operation.value=null}
  return {items,listLoading,listError,detailLoading,detailError,records,operation,hasPending,register,refresh,read,queue,retry,checkSnapshot,discardSnapshot,mutate,retryMutation,checkMutation,endMutation,reset,invalidateReads}
}
