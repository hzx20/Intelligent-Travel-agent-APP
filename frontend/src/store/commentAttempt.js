const PREFIX='travel.comment-creation.v1.'
const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
function key(uid,sid){if(!Number.isSafeInteger(uid)||uid<=0||!Number.isSafeInteger(sid)||sid<=0)throw Error('请先核对当前账号和景点。');return `${PREFIX}${uid}.${sid}`}
function validated(value){
  const p=value?.payload
  if(!uuid.test(value?.request_id||'')||typeof p?.content!=='string'||!p.content.trim()||p.content.length>500||
    !(p.rating===null||Number.isInteger(p.rating)&&p.rating>=1&&p.rating<=5))throw Error('无法恢复上次评论信息，请先核对评论列表，避免重复发布。')
  return Object.freeze({request_id:value.request_id,payload:Object.freeze({content:p.content,rating:p.rating})})
}
export function newCommentAttempt(payload){return validated({request_id:crypto.randomUUID(),payload})}
export function readCommentAttempt(uid,sid){
  const raw=sessionStorage.getItem(key(uid,sid));if(!raw)return null
  let value;try{value=JSON.parse(raw)}catch{throw Error('无法恢复上次评论信息，请先核对评论列表。')}
  if(value.schema!==1||value.user_id!==uid||value.spot_id!==sid)throw Error('上次评论信息与账号或景点不一致，请先核对。')
  return validated(value)
}
export function saveCommentAttempt(uid,sid,attempt){
  const value=validated(attempt)
  try{sessionStorage.setItem(key(uid,sid),JSON.stringify({schema:1,user_id:uid,spot_id:sid,...value}))}
  catch{throw Error('无法保留本次评论信息，请检查浏览器存储后重试。')}
}
export function clearCommentAttempt(uid,sid,requestId){if(!requestId||readCommentAttempt(uid,sid)?.request_id===requestId)sessionStorage.removeItem(key(uid,sid))}
