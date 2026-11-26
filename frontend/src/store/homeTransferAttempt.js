const PREFIX='travel.home-transfer.v1.'
const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const positive=x=>Number.isSafeInteger(x)&&x>0
const freeze=x=>{if(x&&typeof x==='object'){Object.values(x).forEach(freeze);Object.freeze(x)}return x}
export function homeTransferAttempt(value){
  const p=JSON.parse(JSON.stringify(value)),b=p?.body
  if(!['plan','local'].includes(p?.kind)||typeof p.label!=='string'||p.label.length>100||!b)throw Error('无法恢复首页转换信息，请先核对账号旅行再放弃本标签恢复。')
  if(p.kind==='plan'&&(!positive(p.source_id)||!positive(b.version)||!uuid.test(b.revision||'')))throw Error('无法确认原方案版本，请重新读取。')
  if(p.kind==='local'&&(!/^guest-[A-Za-z0-9-]{1,74}$/.test(p.source_id)||b.guest_id!==p.source_id||!positive(b.guest_version)||b.trip?.version!==b.guest_version||b.trip?.id!==p.source_id||!Array.isArray(b.trip?.days)||!Array.isArray(b.trip?.reservations)))throw Error('无法确认本机旅行快照，请先核对已复制内容。')
  return freeze({kind:p.kind,source_id:p.source_id,label:p.label,body:b})
}
export function readHomeTransfer(storage,uid){
  const raw=storage.getItem(PREFIX+uid);if(!raw)return null
  let p;try{p=JSON.parse(raw)}catch{throw Error('首页转换恢复信息损坏，请先核对账号旅行。')}
  if(p.schema!==1||p.owner_id!==uid)throw Error('首页转换恢复信息与账号不符，请先核对。')
  return homeTransferAttempt(p.attempt)
}
export function saveHomeTransfer(storage,uid,attempt){
  if(!positive(uid))throw Error('请先确认当前账号')
  try{storage.setItem(PREFIX+uid,JSON.stringify({schema:1,owner_id:uid,attempt:homeTransferAttempt(attempt)}))}
  catch{throw Error('无法保存本标签恢复信息，本次未发出；请检查浏览器存储。')}
}
export function clearHomeTransfer(storage,uid){storage.removeItem(PREFIX+uid)}
