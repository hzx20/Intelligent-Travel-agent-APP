const positive=n=>Number.isSafeInteger(n)&&n>0
const unique=rows=>new Set(rows.map(x=>x.id)).size===rows.length
export function validWorkspaceTrip(t,id){
  if(String(t?.id)!==String(id)||!positive(t.version)||typeof t.title!=='string'||!Array.isArray(t.days)||!t.days.length||t.days.length>30||!Array.isArray(t.places))return false
  if(!t.days.every(d=>positive(d?.id)&&positive(d.day_number)&&Array.isArray(d.items)&&d.items.every(i=>positive(i?.id)&&typeof i.name==='string')))return false
  return unique(t.days)&&new Set(t.days.map(d=>d.day_number)).size===t.days.length&&unique(t.days.flatMap(d=>d.items))&&t.places.every(p=>positive(p?.id)&&typeof p.name==='string')&&unique(t.places)
}
export const workspaceTabs=local=>[
  {id:'itinerary',label:'行程'},{id:'map',label:'地图'},{id:'places',label:'收集'},
  {id:'assistant',label:'助手'},{id:'records',label:'记录'},...(!local?[{id:'sharing',label:'同行'}]:[]),
]
export function workspaceView(query,trip,local){
  const panel=workspaceTabs(local).some(t=>t.id===query.panel)?query.panel:typeof query.guide==='string'&&/^[1-9]\d*$/.test(query.guide)?'assistant':'itinerary'
  const n=typeof query.day==='string'&&/^[1-9]\d?$/.test(query.day)?Number(query.day):null
  return {panel,day:trip.days.find(d=>d.day_number===n)||null}
}
