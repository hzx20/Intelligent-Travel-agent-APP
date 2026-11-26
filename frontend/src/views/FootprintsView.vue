<script setup>
import {computed,ref,watch,onBeforeUnmount} from 'vue'
import {api} from '../api'
import {useAuth} from '../store/auth'
import {createReadResource} from '../store/readResource'
import {validFootprints} from '../utils/discoveryData'
import TripMap from '../components/TripMap.vue'
import FootprintPhoto from '../components/FootprintPhoto.vue'
const {user,openLogin}=useAuth()
const city=ref(''),tripId=ref(''),selected=ref(null)
const resource=createReadResource({read:()=>api.get('/api/footprints'),validate:validFootprints,initial:{items:[]},owner:()=>user.value?.id||0})
const source=resource.state,records=computed(()=>source.data.items)
watch(()=>user.value?.id||0,uid=>{resource.reset();city.value='';tripId.value='';selected.value=null;if(uid)resource.load()},{immediate:true,flush:'sync'})
onBeforeUnmount(()=>resource.dispose())
const cities=computed(()=>[...new Set(records.value.map(r=>r.city).filter(Boolean))].sort())
const trips=computed(()=>[...new Map(records.value.map(r=>[r.trip_id,r.trip_title])).entries()])
const filtered=computed(()=>records.value.filter(r=>(!city.value||r.city===city.value)&&(!tripId.value||r.trip_id===Number(tripId.value))))
const mapItems=computed(()=>filtered.value.filter(r=>r.location_status==='associated').map(r=>({id:r.id,name:r.place_name,lng:r.lng,lat:r.lat})))
watch(filtered,rows=>{if(!rows.some(r=>r.id===selected.value))selected.value=null})
watch(()=>source.data,()=>{if(city.value&&!cities.value.includes(city.value))city.value='';if(tripId.value&&!trips.value.some(([id])=>id===Number(tripId.value)))tripId.value=''})
</script>
<template>
  <main class="footprints-page">
    <router-link to="/me" class="back">← 个人中心</router-link>
    <header><p>TRAVEL MEMORIES</p><h1>我的旅行足迹</h1><span>这里汇总你主动记录的到访地点。</span></header>
    <p v-if="!user" class="state">登录后查看足迹。 <button @click="openLogin('login')">登录</button></p>
    <template v-else>
      <div class="refresh-row"><p v-if="source.loading" role="status">正在更新足迹…</p><button :disabled="source.loading" @click="resource.load()">刷新足迹</button></div>
      <p v-if="source.error" class="state" role="alert">{{ source.error }} <span v-if="source.loaded">仍显示上次已加载的足迹。</span><button :disabled="source.loading" @click="resource.retry">重试足迹</button></p>
      <template v-if="source.loaded">
        <div class="filters"><label>城市<select v-model="city"><option value="">全部城市</option><option v-for="name in cities" :key="name">{{ name }}</option></select></label><label>旅行<select v-model="tripId"><option value="">全部旅行</option><option v-for="[id,title] in trips" :key="id" :value="id">{{ title }}</option></select></label><span role="status">{{ filtered.length }} 条记录</span></div>
        <div class="layout"><section aria-label="足迹记录">
          <p v-if="!filtered.length && !source.loading && !source.error" class="state">还没有符合条件的足迹。可以在旅行详情中手动记录到访。</p>
          <article v-for="record in filtered" :key="record.id" :class="{selected:selected===record.id}">
            <div><h2>{{ record.place_name }}</h2><p>{{ record.city || '城市未填' }} · {{ record.visited_on || '日期未填' }} · {{ record.location_status==='associated' ? '已关联地图' : '地点待确认' }}</p><p v-if="record.address">{{ record.address }}</p><p v-if="record.note" class="note">{{ record.note }}</p><div v-if="record.photo_ids.length" class="photos"><FootprintPhoto v-for="photo in record.photo_ids" :key="photo" :trip-id="record.trip_id" :footprint-id="record.id" :photo-id="photo" /></div></div>
            <div class="record-actions"><button v-if="record.location_status==='associated'" :aria-label="`选择地点：${record.place_name}`" :aria-pressed="selected===record.id" @click="selected=record.id">{{ selected===record.id ? '已选择地点' : '选择地点' }}</button><router-link :to="`/trips/${record.trip_id}`">{{ record.trip_title }} →</router-link></div>
          </article>
        </section><aside><h2>足迹地图</h2><p>仅展示已关联地图的地点；未关联地点仍保留在列表中。</p><TripMap :key="user.id" :items="mapItems" :selected-id="selected" :connect="false" unavailable-message="地图暂不可用，足迹记录仍可查看。" empty-message="已关联地图的到访地点会显示在这里。" @select="selected=$event" /></aside></div>
      </template>
    </template>
  </main>
</template>
<style scoped>
.refresh-row{display:flex;align-items:center;justify-content:flex-end;gap:12px;min-height:44px;margin-bottom:12px;font-size:13px;color:#6c8276}.record-actions{display:flex;flex-direction:column;align-items:flex-start;gap:8px;flex-shrink:0}.footprints-page button{min-height:44px;padding:8px 12px;border:1px solid #cdded2;border-radius:8px;background:#fff;color:#1a6e50;cursor:pointer}.footprints-page button:disabled{opacity:.5}.footprints-page button:focus-visible,.footprints-page select:focus-visible,.footprints-page a:focus-visible{outline:3px solid #548b73;outline-offset:3px}.filters select{min-height:44px;max-width:100%}article{overflow-wrap:anywhere}@media(max-width:700px){.record-actions{flex-direction:row;flex-wrap:wrap;align-items:center}}
.footprints-page{width:90%;max-width:1120px;margin:35px auto 70px;color:#203b35}.back{color:#6c8276;font-size:13px}header{margin:32px 0}header p{color:#789487;font-size:10px;font-weight:bold;letter-spacing:.14em}header h1{font-size:30px;margin:9px 0}header span,.filters span,aside p{color:#84988b;font-size:12px}.filters{display:flex;align-items:end;gap:12px;flex-wrap:wrap;margin-bottom:18px}.filters label{display:grid;gap:6px;font-size:12px}.filters select{padding:8px 10px;background:#fff;border:1px solid #dbe5de;border-radius:8px;min-width:150px}.filters span{margin-left:auto}.layout{display:grid;grid-template-columns:1fr 1fr;gap:18px;align-items:start}article,.state,aside{background:#fff;border:1px solid #e1e9e2;border-radius:12px;padding:18px;margin-bottom:10px}article{display:flex;justify-content:space-between;gap:12px}.selected{border-color:#78b18e}.photos{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}article h2,aside h2{font-size:16px}article p{font-size:12px;color:#7b8e81;line-height:1.6;margin-top:5px;white-space:pre-wrap}article a,.state button{color:#1a6e50;font-size:12px;white-space:nowrap}.state button{border:0;background:none;text-decoration:underline}aside p{margin:8px 0 12px;line-height:1.5}aside :deep(.map-wrap){height:420px}@media(max-width:700px){.layout{grid-template-columns:1fr}aside :deep(.map-wrap){height:300px;min-height:300px}article{flex-direction:column}.filters span{margin-left:0}}
</style>
