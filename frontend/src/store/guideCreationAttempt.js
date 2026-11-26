/** Only submitted, immutable guide creation attempts survive this tab's reload. */
const PREFIX='travel.guide-creation.v1.'
const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const invalid=()=>new Error('无法恢复上次攻略创建信息，请先核对云端攻略，避免重复发布。')
function key(uid){if(!Number.isSafeInteger(uid)||uid<=0)throw new Error('请先确认当前账号');return PREFIX+uid}
function validated(value){
  const p=value?.payload
  if(!uuid.test(value?.request_id||'')||!p||typeof p.title!=='string'||!p.title.trim()||p.title.length>100||
    typeof p.content!=='string'||!p.content.trim()||p.content.length>20000||typeof p.city!=='string'||p.city.length>30||
    typeof p.is_draft!=='boolean'||!Array.isArray(p.images)||p.images.length>30||p.images.some(x=>typeof x!=='string')||
    !Array.isArray(p.spot_ids)||p.spot_ids.length>10||p.spot_ids.some(x=>!Number.isSafeInteger(x)||x<=0))throw invalid()
  return Object.freeze({request_id:value.request_id,payload:Object.freeze({title:p.title,content:p.content,city:p.city,
    is_draft:p.is_draft,images:Object.freeze([...p.images]),spot_ids:Object.freeze([...p.spot_ids])})})
}
export function newGuideCreation(payload){return validated({request_id:crypto.randomUUID(),payload})}
export function readGuideCreation(uid){
  const raw=sessionStorage.getItem(key(uid));if(!raw)return null
  let value;try{value=JSON.parse(raw)}catch{throw invalid()}
  if(value.schema!==1||value.user_id!==uid)throw invalid()
  return validated(value)
}
export function saveGuideCreation(uid,attempt){
  const value=validated(attempt)
  try{sessionStorage.setItem(key(uid),JSON.stringify({schema:1,user_id:uid,...value}))}
  catch{throw new Error('无法保留本次攻略创建信息，请检查浏览器存储空间后重试。')}
}
export function clearGuideCreation(uid,requestId){
  // No requestId is used only after the user's explicit discard of corrupt data.
  if(!requestId||readGuideCreation(uid)?.request_id===requestId)sessionStorage.removeItem(key(uid))
}
