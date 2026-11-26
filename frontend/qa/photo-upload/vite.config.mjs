import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
let photo = null
const trip = () => ({id:1,version:photo?2:1,destination:'测试城市',days:[{id:11,day_number:1,items:[{id:2,name:'测试地点'}]}],places:[]})
export default defineConfig(({mode}) => mode === 'real-backend'
  ? {plugins:[vue()],server:{host:'127.0.0.1',port:5262,strictPort:true,proxy:{'/api':'http://127.0.0.1:18962'}}}
  : {plugins:[vue(),{name:'isolated-photo-upload',configureServer(server){
  server.middlewares.use('/api/qa-bootstrap',(req,res)=>{res.setHeader('Content-Type','application/json');res.end(JSON.stringify({token:'',user:{id:1},trip:trip()}))})
  server.middlewares.use('/api/trips/1/footprints',(req,res)=>{
    res.setHeader('Cache-Control','no-store')
    if(req.method==='POST' && req.url==='/1/photos'){
      const chunks=[]; let size=0
      req.on('data',chunk=>{size+=chunk.length;if(size>4*1024*1024){res.statusCode=413;res.end();req.destroy()}else chunks.push(chunk)})
      req.on('end',()=>{if(res.writableEnded)return;photo=Buffer.concat(chunks);res.setHeader('Content-Type','application/json');res.end(JSON.stringify(trip()))})
    }else if(req.url==='/1/photos/1'){
      res.statusCode=photo?200:404;res.setHeader('Content-Type','image/png');res.end(photo||'')
    }else{
      res.setHeader('Content-Type','application/json');res.end(JSON.stringify({items:[{id:1,place_name:'测试地点',city:'测试城市',location_status:'unconfirmed',photo_ids:photo?[1]:[]}]}))
    }
  })
}}],server:{host:'127.0.0.1',port:5262,strictPort:true}})
