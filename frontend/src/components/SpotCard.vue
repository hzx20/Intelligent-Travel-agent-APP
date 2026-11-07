<script setup>
/** 景点卡片：首页与列表页共用；phClass 按城市/序位给占位色，有真图则显示真图 */
const props = defineProps({ spot: { type: Object, required: true } })
const TONES = ['', 'sea', 'sun', 'mount']
function phClass(s) {
  return TONES[s.id % TONES.length]
}
function bgStyle(s) {
  return s.image_url ? { backgroundImage: `url(${s.image_url})` } : {}
}
</script>

<template>
  <router-link :to="`/spots/${spot.id}`" class="gcard">
    <div class="ph" :class="phClass(spot)" :style="bgStyle(spot)">
      <span v-if="!spot.image_url">{{ spot.name }} · 图待补</span>
    </div>
    <div class="gi">
      <b>{{ spot.name }}</b>
      <small>{{ spot.city }} · {{ spot.district || '未知区域' }}</small>
      <div class="row">
        <span v-if="spot.is_free" class="badge-free">免费</span>
        <span v-else-if="spot.price" class="badge-price">¥{{ spot.price }} 起</span>
        <!-- 票价未采集到：不能用绿色"免费"样式冒充（会让人误以为不要钱），
             也不该承诺"详情页看票价"——详情页同样没有这个数。 -->
        <span v-else class="badge-unknown">票价以景区为准</span>
        <span class="heart">{{ spot.tags ? spot.tags.split(',')[0] : '景点' }}</span>
      </div>
    </div>
  </router-link>
</template>
