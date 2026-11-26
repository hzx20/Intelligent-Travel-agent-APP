import {validWorkspaceTrip} from './tripWorkspace.js'
import { WIKIVOYAGE_LICENSE, wikivoyageRevisionUrl } from './wikivoyage.js'
const text=(s,max)=>typeof s==='string'&&s.length<=max
const positive=n=>Number.isSafeInteger(n)&&n>0
export const validImageText=(value,version)=>Boolean(value&&value.version===version&&text(value.text,12000))
const validGuideSummary=g=>Boolean(g&&positive(g.id)&&text(g.title,200)&&typeof g.is_draft==='boolean'&&(g.city==null||text(g.city,100)))
export const validSiteGuide=(g,id)=>validGuideSummary(g)&&g.id===id&&typeof g.text==='string'
export const validSiteGuides=(g,page)=>Boolean(g&&g.page===page&&Number.isSafeInteger(g.total)&&g.total>=0&&Array.isArray(g.items)&&g.items.length<=10&&g.items.length<=g.total&&g.items.every(validGuideSummary)&&new Set(g.items.map(x=>x.id)).size===g.items.length)
export function validPublicGuidePreview(value, version, local) {
  const guide = value?.guide
  return Boolean(guide && text(guide.title, 160) && guide.title.trim() && positive(guide.revision)
    && guide.url === wikivoyageRevisionUrl(guide.revision)
    && guide.license_url === WIKIVOYAGE_LICENSE
    && validGuidePreview(value, version, local))
}
export function imageSelectionError(file){
  return !file||!['image/jpeg','image/png'].includes(file.type)||!Number.isFinite(file.size)||file.size<=0||file.size>4*1024*1024?'请选择不超过 4 MB 的 PNG 或 JPEG 图片':''
}
export function validGuidePreview(value,version,local){
  return Boolean(value&&value.version===version&&Array.isArray(value.places)&&value.places.length<=50&&value.places.every(place=>{
    if(!place||!text(place.name,160)||!place.name.trim()||!text(place.note,2000)||!['matched','ambiguous','unmatched'].includes(place.status)||!Array.isArray(place.matches)||place.matches.length>10)return false
    if(place.status==='matched'&&place.matches.length!==1||place.status==='unmatched'&&place.matches.length!==0||place.status==='ambiguous'&&!place.matches.length)return false
    return new Set(place.matches.map(m=>m?.id)).size===place.matches.length&&place.matches.every(m=>{
      if(!m||!positive(m.id)||m.name!==place.name||!['city','district','address'].every(k=>m[k]==null||text(m[k],1000)))return false
      if(!local)return true
      return text(m.amap_id,200)&&(m.lng==null&&m.lat==null||typeof m.lng==='number'&&typeof m.lat==='number'&&Number.isFinite(m.lng)&&Number.isFinite(m.lat)&&Math.abs(m.lng)<=180&&Math.abs(m.lat)<=90)
    })
  }))
}
export function validGuideApply(value,id,version,count){
  if(!value||!validWorkspaceTrip(value.trip,id)||!['added','merged','unchanged'].every(k=>Number.isSafeInteger(value[k])&&value[k]>=0))return false
  return value.added+value.merged+value.unchanged===count&&value.trip.version>=version+(value.added+value.merged>0?1:0)
}
