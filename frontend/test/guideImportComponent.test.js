import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { parse, compileScript } from '@vue/compiler-sfc'
import { createRenderer, createVNode, nextTick } from 'vue'

const file={type:'image/png',size:12}
const trip=(id,version=1)=>({id,version,destination:'杭州'})
const settle=async()=>{await new Promise(resolve=>setImmediate(resolve));await nextTick()}
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no});return {promise,resolve,reject}}
async function mountGuide(api){
  globalThis.__guideTestApi=api
  const url=new URL('../src/components/TripGuideImport.vue',import.meta.url)
  const {descriptor}=parse(await readFile(url,'utf8'))
  const code=compileScript(descriptor,{id:'guide-regression'}).content
    .replace(/from ['"]vue['"]/g,`from ${JSON.stringify(import.meta.resolve('vue'))}`)
    .replace(/import \{ api \} from ['"]\.\.\/api['"]/, 'const api=globalThis.__guideTestApi')
    .replace(/from ['"](\.\.[^'"]+)['"]/g,(_,path)=>`from ${JSON.stringify(new URL(path+'.js',url).href)}`)
  const {default:component}=await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}#${Math.random()}`)
  component.render=()=>null
  const renderer=createRenderer({createComment:()=>({}),insert(){},remove(){},parentNode:()=>null,nextSibling:()=>null,createText:()=>({}),setText(){},createElement:()=>({}),setElementText(){},patchProp(){}})
  const root={},updates=[],props={trip:trip(1),onUpdated:value=>updates.push(value)}
  let vnode=createVNode(component,props);renderer.render(vnode,root);await settle()
  return {state:vnode.component.setupState,updates,update(value){vnode=createVNode(component,{...props,trip:value});renderer.render(vnode,root)},unmount(){renderer.render(null,root)}}
}

test('攻略导入与图片识别的上下文保护',async t=>{
  const oldReader=globalThis.FileReader
  globalThis.FileReader=class {readAsDataURL(){this.result='data:image/png;base64,AAAA';queueMicrotask(()=>this.onload())}}
  try{
    await t.test('OCR 失败保留手动正文与来源，结果确认前不覆盖正文',async()=>{
      let fail=true
      const mounted=await mountGuide({post:async()=>{if(fail)throw new Error('OCR unavailable');return {version:1,text:'识别文本'}}})
      try{
        const s=mounted.state;s.text='手动正文';s.source='朋友攻略';s.chooseImage({target:{files:[file]}})
        await s.readImage()
        assert.equal(s.text,'手动正文');assert.equal(s.source,'朋友攻略');assert.equal(s.imageLoading,false)
        fail=false;await s.readImage()
        assert.equal(s.text,'手动正文');assert.equal(s.imageText,'识别文本')
        s.useImageText();assert.equal(s.text,'手动正文\n识别文本')
      }finally{mounted.unmount()}
    })
    await t.test('旧 OCR 返回不能清除新旅行 OCR 的处理中状态',async()=>{
      const a=deferred(),b=deferred();let calls=0
      const mounted=await mountGuide({post:()=>++calls===1?a.promise:b.promise})
      try{
        const s=mounted.state;s.chooseImage({target:{files:[file]}})
        const first=s.readImage();await settle()
        mounted.update(trip(2));await settle()
        assert.equal(s.imageFile,null);assert.equal(s.text,'')
        s.chooseImage({target:{files:[file]}});const second=s.readImage();await settle()
        a.resolve({version:1,text:'旧结果'});await first
        assert.equal(s.imageLoading,true);assert.equal(s.imageText,'')
        b.resolve({version:1,text:'新结果'});await second
        assert.equal(s.imageLoading,false);assert.equal(s.imageText,'新结果')
      }finally{mounted.unmount()}
    })
    await t.test('切换旅行后丢弃旧导入结果，不通知新页面',async()=>{
      const pending=deferred(),mounted=await mountGuide({post:()=>pending.promise})
      try{
        const s=mounted.state;s.previewVersion=1;s.candidates=[{name:'西湖',note:'',spot_id:null,matches:[],selected:true}]
        const applying=s.apply();mounted.update(trip(2));await settle()
        pending.resolve({added:1,merged:0,unchanged:0,trip:trip(1,2)});await applying
        assert.deepEqual(mounted.updates,[]);assert.equal(s.message,'');assert.equal(s.applying,false)
      }finally{mounted.unmount()}
    })
    await t.test('取消换图和重识别失败保留人工核对文字',async()=>{
      const oldWindow=globalThis.window;globalThis.window={confirm:()=>false}
      const m=await mountGuide({post:async()=>{throw Error('OCR失败')}})
      try{const s=m.state;s.chooseImage({target:{files:[file]}});s.imageReady=true;s.imageText='人工核对';s.chooseImage({target:{files:[{...file,name:'other.png'}]}});assert.equal(s.imageText,'人工核对');globalThis.window.confirm=()=>true;await s.readImage();assert.equal(s.imageText,'人工核对');assert(s.imageReady);s.clearImage();assert.equal(s.imageFile,null)}finally{m.unmount();globalThis.window=oldWindow}
    })
    await t.test('坏OCR回执不能替换原文，文件校验不发送请求',async()=>{
      let calls=0;const m=await mountGuide({post:async()=>{calls++;return {version:99,text:{bad:true}}}})
      try{const s=m.state;s.text='原正文';s.chooseImage({target:{files:[{type:'image/png',size:0}]}});await s.readImage();assert.equal(calls,0);s.chooseImage({target:{files:[file]}});await s.readImage();assert.equal(s.text,'原正文');assert.equal(s.imageReady,false);assert.match(s.error,/回执无法确认/)}finally{m.unmount()}
    })
    await t.test('同旅行版本改变时等待已有OCR结束，旧结果不回填',async()=>{
      const wait=deferred();const m=await mountGuide({post:()=>wait.promise})
      try{const s=m.state;s.chooseImage({target:{files:[file]}});const p=s.readImage();await settle();m.update(trip(1,2));await settle();assert(s.imageLoading);wait.resolve({version:1,text:'旧版识别'});await p;assert.equal(s.imageLoading,false);assert.equal(s.imageReady,false)}finally{m.unmount()}
    })
    await t.test('未知导入保留首次正文与选择，只读核对不自动重发',async()=>{
      let calls=0;const m=await mountGuide({post:async()=>{calls++;throw Error('丢响应')},get:async()=>({id:1,version:2,title:'旅行',days:[{id:1,day_number:1,items:[]}],places:[{id:1,name:'西湖'}]})})
      try{const s=m.state;s.previewVersion=1;s.candidates=[{name:'西湖',note:'原备注',spot_id:null,matches:[],selected:true}];await s.apply();assert(s.pendingApply);await s.apply();assert.equal(calls,1);await s.checkApply();assert.equal(s.applyReview.version,2);await s.apply();assert.equal(calls,1);assert.equal(s.pendingApply.body.places[0].note,'原备注')}finally{m.unmount()}
    })
  }finally{globalThis.FileReader=oldReader;delete globalThis.__guideTestApi}
})
