import {reactive} from 'vue'
const copy=x=>JSON.parse(JSON.stringify(x))
const freeze=x=>{if(x&&typeof x==='object'){Object.values(x).forEach(freeze);Object.freeze(x)}return x}
/** One source keeps its last confirmed data; identity/reset/disposal invalidate every old read. */
export function createReadResource({read,validate,initial,owner=()=>0}){
  const state=reactive({data:copy(initial),loaded:false,loading:false,error:'',applied:null,requested:null})
  let seq=0,epoch=0,alive=true
  async function load(query={}){
    if(!alive)return false
    const fixed=freeze(copy(query)),run=++seq,scope=epoch,uid=owner()
    const current=()=>alive&&scope===epoch&&run===seq&&uid===owner()
    state.requested=fixed;state.loading=true;state.error=''
    try{
      const value=await read(fixed)
      if(!current())return false
      if(!validate(value,fixed))throw Error('返回内容不完整，请重试。')
      state.data=value;state.applied=fixed;state.loaded=true
      return true
    }catch(e){if(current())state.error=e.message||'暂时无法加载，请重试。';return false}
    finally{if(current())state.loading=false}
  }
  function reset(){++seq;++epoch;Object.assign(state,{data:copy(initial),loaded:false,loading:false,error:'',applied:null,requested:null})}
  function dispose(){alive=false;++seq;++epoch}
  return {state,load,retry:()=>load(state.requested||{}),reset,dispose}
}
