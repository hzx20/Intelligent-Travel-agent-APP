import assert from 'node:assert/strict'
import {mkdir} from 'node:fs/promises'
import {join} from 'node:path'
import {chromium} from '@playwright/test'

const origin=process.argv[2],output=process.argv[3]
await mkdir(output,{recursive:true})
const browser=await chromium.launch({channel:process.env.TRAVEL_E2E_CHANNEL||'msedge',headless:true})
const results=[]
try{
  for(const [name,viewport] of [['desktop',{width:1280,height:900}],['mobile',{width:390,height:844}]]){
    const context=await browser.newContext({viewport,isMobile:name==='mobile',hasTouch:name==='mobile'})
    try{
      const page=await context.newPage(),errors=[]
      page.on('pageerror',e=>errors.push(e.message))
      await page.route('**/*',r=>new URL(r.request().url()).origin===origin?r.continue():r.abort())
      await page.goto(origin+'/travel/')
      await page.getByRole('link',{name:'＋ 创建旅行',exact:true}).click()
      await page.getByRole('textbox',{name:'旅行名称',exact:true}).fill('静态发布包合成旅行')
      await page.getByRole('spinbutton',{name:'旅行天数',exact:true}).fill('2')
      await page.getByRole('button',{name:'创建旅行 →',exact:true}).click()
      await page.getByRole('heading',{name:'静态发布包合成旅行',exact:true}).waitFor()
      const tabs=page.getByRole('navigation',{name:'旅行工作区'})
      await tabs.getByRole('button',{name:/^收集/}).click()
      await page.getByRole('textbox',{name:'手动输入想去的地点',exact:true}).fill('合成发布包地点')
      await page.getByRole('button',{name:'加入',exact:true}).click()
      await page.getByRole('button',{name:'安排到当天',exact:true}).click()
      await tabs.getByRole('button',{name:/^行程/}).click()
      await page.getByRole('heading',{name:'合成发布包地点',exact:true}).waitFor()
      const resource=new URL(page.url()).pathname
      await page.reload()
      await page.getByRole('heading',{name:'合成发布包地点',exact:true}).waitFor()
      assert.equal(new URL(page.url()).pathname,resource)
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth))
      await page.getByRole('heading',{name:'合成发布包地点',exact:true}).evaluate(el=>el.scrollIntoView({block:'center'}))
      await page.screenshot({path:join(output,name+'.png')})
      assert.deepEqual(errors,[])
      results.push({viewport:name,created:true,collected:true,scheduled:true,deep_reload:true,uncaught_errors:errors.length})
    }finally{await context.close()}
  }
}finally{await browser.close()}
process.stdout.write(JSON.stringify({production_bundle_smoke:results})+'\n')
