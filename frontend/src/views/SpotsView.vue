<script setup>
import {computed,onBeforeUnmount,ref,watch} from 'vue'
import {useRoute,useRouter} from 'vue-router'
import {api} from '../api'
import {useAuth} from '../store/auth'
import {createReadResource} from '../store/readResource'
import {discoveryQuery,discoveryRoute,discoveryPath,validPage} from '../utils/discoveryData'
import {pageList} from '../utils/pagination'
import SpotCard from '../components/SpotCard.vue'
const kind='spots',route=useRoute(),router=useRouter(),{user,openLogin}=useAuth()
const keyword=ref(''),city=ref(''),mode=ref('all')
const resource=createReadResource({read:q=>api.get(discoveryPath(q,kind)),validate:(r,q)=>validPage(r,q,kind),initial:{items:[],total:0,page:1},owner:()=>user.value?.id||0})
const source=resource.state,items=computed(()=>source.data.items),total=computed(()=>source.data.total),page=computed(()=>source.data.page),pages=computed(()=>Math.max(1,Math.ceil(total.value/12))),pageItems=computed(()=>pageList(page.value,pages.value))
const CITIES=computed(()=>[...new Set(['','成都','杭州','西安','北京','三亚',city.value])])
const applied=computed(()=>source.applied||discoveryQuery({},kind))
watch(()=>[route.query,user.value?.id||0],([query,uid],previous)=>{
  if(previous && uid!==previous[1])resource.reset()
  const q=discoveryQuery(query,kind);keyword.value=q.keyword;city.value=q.city;mode.value=q.is_free
  resource.load(q)
},{immediate:true,deep:true,flush:'sync'})
onBeforeUnmount(()=>resource.dispose())
async function commit(q){const before=route.fullPath;await router.push({query:discoveryRoute(q,kind)});if(before===route.fullPath)resource.load(q)}
function search(){return commit({page:1,keyword:keyword.value.trim(),city:city.value,is_free:mode.value})}
function goPage(n){if(n<1||n>pages.value||n===page.value)return;return commit({...applied.value,page:n})}
function writeGuide(){if(!user.value){openLogin('login');return}router.push('/guides/new')}
</script>

<template>
  <div class="wrap">
    <!-- 景点板块的新定位：规划器的素材库，不是浏览终点 -->
    <router-link class="plan-hint" to="/plan">
      <span>挑好景点后，可以交给 AI 排成一条路线</span>
      <b>去规划 →</b>
    </router-link>
    <div class="filters">
      <input v-model="keyword" aria-label="搜索景点" maxlength="200" class="finput" placeholder="🔍 搜索景点名 / 标签 / 区域，支持多关键词" @keyup.enter="search" />
      <select v-model="city" aria-label="城市筛选" class="fsel" @change="search">
        <option v-for="c in CITIES" :key="c" :value="c">{{ c || '全部城市' }}</option>
      </select>
      <select v-model="mode" aria-label="门票筛选" class="fsel" @change="search">
        <option value="all">门票不限</option>
        <option value="free">免费</option>
        <option value="paid">收费</option>
      </select>
      <button class="btn-search" @click="search">搜索</button>
    </div>
    <div v-if="source.loaded" class="count">筛选结果：{{ applied.city || '全部城市' }} · {{ applied.is_free === 'all' ? '门票不限' : (applied.is_free === 'free' ? '免费' : '收费') }}<template v-if="applied.keyword"> · {{ applied.keyword }}</template> —— 共 {{ total }} 条</div>

    <p v-if="source.loading" class="loading" role="status">正在更新结果…</p>
    <p v-if="source.error" class="empty list-error" role="alert">暂时无法更新：{{ source.error }} <span v-if="source.loaded">仍显示上次已加载的结果。</span><button :disabled="source.loading" @click="resource.retry">重试本次查询</button></p>
    <p v-if="source.loaded && !source.loading && !source.error && !items.length" class="empty">{{ total && page > pages ? '当前页已超出结果范围，请返回第一页。' : '没有符合条件的内容，试试其他关键词或城市。' }} <button v-if="total && page > pages" @click="goPage(1)">返回第一页</button></p>

    <div v-if="items.length" class="grid-3">
      <SpotCard v-for="s in items" :key="s.id" :spot="s" />
    </div>

    <nav class="pager" aria-label="景点结果分页" v-if="pages > 1">
      <button class="pg" :disabled="page <= 1" @click="page > 1 && goPage(Math.min(page - 1,pages))">‹ 上一页</button>
      <template v-for="(n, i) in pageItems" :key="`${n}-${i}`">
        <span v-if="n === '...'" class="pg ellipsis">…</span>
        <button v-else class="pg" :class="{ on: n === page }" :aria-current="(n === page) ? 'page' : undefined" @click="goPage(n)">{{ n }}</button>
      </template>
      <button class="pg" :disabled="page >= pages" @click="page < pages && goPage(page + 1)">下一页 ›</button>
    </nav>
  </div>
</template>

<style scoped>
.count{overflow-wrap:anywhere}.finput{max-width:100%}
.list-error{padding:16px;text-align:left}.list-error button,.empty button{min-height:44px;border:1px solid var(--green-border);border-radius:8px;background:#fff;color:var(--green);padding:8px 12px;margin-left:8px}.applied-query{font-size:12px;color:#73847b;line-height:1.7;margin:8px 0 12px;overflow-wrap:anywhere}.filters{flex-wrap:wrap}.finput{min-width:0;flex-basis:200px}.fsel,.btn-search,.btn-write,.pager .pg{min-height:44px}.pager{flex-wrap:wrap}.acard:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible{outline:3px solid #548b73;outline-offset:3px}.ai{min-width:0;overflow-wrap:anywhere}

.plan-hint {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 14px;
  padding: 10px 14px;
  background: var(--green-soft);
  border: 1px solid var(--green-border);
  border-radius: 10px;
  font-size: 12.5px;
  color: #3d5f52;
}
.plan-hint b { margin-left: auto; color: var(--green); font-weight: 600; }
.filters { display: flex; gap: 10px; flex-wrap: wrap; }
.finput {
  flex: 1; min-width: 220px; border: 1.5px solid #bbb; border-radius: 10px;
  padding: 10px 12px; font-size: 13.5px; background: #fff;
}
.fsel { border: 1.5px solid #bbb; border-radius: 10px; padding: 10px; font-size: 13px; background: #fff; }
.btn-search { background: var(--green); color: #fff; border: none; border-radius: 10px; padding: 10px 20px; }
.count { font-size: 12px; color: #888; margin: 10px 0 12px; }
</style>
