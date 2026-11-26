const text=v=>typeof v==='string'
const optionalText=v=>v==null||text(v)
const positive=v=>Number.isSafeInteger(v)&&v>0
const date=v=>v==null||v===''||text(v)&&/^\d{4}-\d{2}-\d{2}$/.test(v)&&Number.isFinite(Date.parse(v))&&new Date(v).toISOString().slice(0,10)===v
export const validShareToken=value=>typeof value==='string'&&/^[A-Za-z0-9_-]{43}$/.test(value)
export function validSharedJourney(value){
  if(!value||!text(value.title)||!value.title.trim()||!optionalText(value.destination)||!date(value.start_date)||!Array.isArray(value.days)||value.days.length<1||value.days.length>30)return false
  const numbers=new Set()
  return value.days.every(day=>{
    if(!day||!positive(day.day_number)||day.day_number>30||numbers.has(day.day_number)||!optionalText(day.theme)||!Array.isArray(day.items))return false
    numbers.add(day.day_number)
    return day.items.every(item=>item&&text(item.name)&&item.name.trim()&&text(item.kind)&&['start_time','address','activity','note'].every(k=>optionalText(item[k]))&&(item.duration_min==null||(Number.isSafeInteger(item.duration_min)&&item.duration_min>=0)))
  })
}
export const validInvitationReceipt=value=>Boolean(value&&positive(value.trip_id)&&['owner','editor'].includes(value.role))
export const validInvitationStatus=value=>Boolean(value&&(value.state==='not_joined'&&value.trip_id==null||value.state==='joined'&&validInvitationReceipt(value)))
