/** Only immutable submitted attempts, scoped to this tab's accepted account. */
const PREFIX='travel.plan-creation.v1.'
const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const invalid=()=>Error('无法恢复上次规划提交，请先核对历史和后台任务，避免重复生成。')
function key(uid){if(!Number.isSafeInteger(uid)||uid<0)throw invalid();return PREFIX+uid}
function freeze(value){if(value&&typeof value==='object'){Object.values(value).forEach(freeze);Object.freeze(value)}return value}
function validated(uid,value){
  const p=value?.payload
  if(!uuid.test(value?.request_id||'')||!['create','cancel'].includes(value?.mode)||
    !(uid===0?uuid.test(value?.guest_key||''):value?.guest_key===null)||!p||
    typeof p.user_input!=='string'||!p.user_input.trim()||p.user_input.length>500||
    typeof p.session_id!=='string'||p.session_id.length>50||!Array.isArray(p.history)||p.history.length>200||
    !p.prev_collected||typeof p.prev_collected!=='object'||Array.isArray(p.prev_collected)||
    !Array.isArray(p.messages)||p.messages.length>200||p.messages.some(m=>!m||typeof m.role!=='string'||typeof m.text!=='string'))throw invalid()
  let payload
  try{const raw=JSON.stringify({history:p.history,user_input:p.user_input,prev_collected:p.prev_collected,session_id:p.session_id,messages:p.messages});if(new TextEncoder().encode(raw).length>250*1024)throw invalid();payload=JSON.parse(raw)}catch{throw invalid()}
  return Object.freeze({request_id:value.request_id,guest_key:value.guest_key,mode:value.mode,payload:freeze(payload)})
}
export function newPlanCreation(uid,payload){return validated(uid,{request_id:crypto.randomUUID(),guest_key:uid===0?crypto.randomUUID():null,mode:'create',payload})}
export function cancelPlanCreation(uid,attempt){return validated(uid,{...attempt,mode:'cancel'})}
export function readPlanCreation(uid){
  const raw=sessionStorage.getItem(key(uid));if(!raw)return null
  let value;try{value=JSON.parse(raw)}catch{throw invalid()}
  if(value?.schema!==1||value.owner_id!==uid)throw invalid()
  return validated(uid,value)
}
export function savePlanCreation(uid,attempt){
  const value=validated(uid,attempt)
  try{sessionStorage.setItem(key(uid),JSON.stringify({schema:1,owner_id:uid,...value}))}catch{throw Error('无法保留本次规划提交，请检查浏览器存储后重试。')}
}
export function clearPlanCreation(uid,requestId){if(!requestId||readPlanCreation(uid)?.request_id===requestId)sessionStorage.removeItem(key(uid))}
