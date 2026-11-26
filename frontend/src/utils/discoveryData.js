const positive=n=>Number.isSafeInteger(n)&&n>0
const count=n=>Number.isSafeInteger(n)&&n>=0
const rows=(a,valid)=>Array.isArray(a)&&a.every(valid)&&new Set(a.map(x=>x.id)).size===a.length
export const validSpot=x=>positive(x?.id)&&typeof x.name==='string'&&typeof x.city==='string'&&typeof x.tags==='string'&&typeof x.is_free==='boolean'
export const validGuide=x=>positive(x?.id)&&typeof x.title==='string'&&typeof x.created_at==='string'&&typeof x.author==='string'&&typeof x.summary==='string'&&count(x.views)&&count(x.spot_count)
export const validRegions=x=>Array.isArray(x?.cities)&&x.cities.every(c=>typeof c?.city==='string'&&count(c.count)&&Array.isArray(c.districts)&&c.districts.every(d=>typeof d?.name==='string'&&count(d.count)))&&(x.total===undefined||count(x.total))
export function validPage(x,q,kind){const size=kind==='guides'?10:12;return count(x?.total)&&x.page===q.page&&x.page_size===size&&rows(x.items,kind==='guides'?validGuide:validSpot)&&x.items.length<=size&&x.items.length<=x.total}
export const validRecommendations=x=>validPage(x,{page:1},'spots')
export const validFootprints=x=>rows(x?.items,r=>positive(r?.id)&&positive(r.trip_id)&&typeof r.trip_title==='string'&&typeof r.place_name==='string'&&typeof r.city==='string'&&['associated','unconfirmed'].includes(r.location_status)&&Array.isArray(r.photo_ids)&&r.photo_ids.every(positive)&&(r.location_status!=='associated'||Number.isFinite(r.lng)&&Number.isFinite(r.lat)&&Math.abs(r.lng)<=180&&Math.abs(r.lat)<=90))
const single=x=>typeof x==='string'?x:''
export function discoveryQuery(raw={},kind='spots'){
  const p=single(raw.page)
  return {page:/^[1-9]\d{0,5}$/.test(p)?Number(p):1,keyword:single(raw.keyword).trim().slice(0,200),city:single(raw.city).trim().slice(0,100),
    ...(kind==='guides'?{sort:raw.sort==='hot'?'hot':'new'}:{is_free:['free','paid'].includes(raw.is_free)?raw.is_free:'all'})}
}
export function discoveryRoute(q,kind){return {...(q.page>1?{page:String(q.page)}:{}),...(q.keyword?{keyword:q.keyword}:{}),...(q.city?{city:q.city}:{}),...(kind==='guides'?(q.sort==='hot'?{sort:'hot'}:{}):(q.is_free!=='all'?{is_free:q.is_free}:{}))}}
export function discoveryPath(q,kind){return `/api/${kind}?`+new URLSearchParams({...q,page_size:kind==='guides'?10:12})}
