import { createApp, h, ref } from 'vue'
import TripFootprints from '../../src/components/TripFootprints.vue'
import { useAuth } from '../../src/store/auth.js'
import { setToken } from '../../src/api/index.js'
const boot = await (await fetch('/api/qa-bootstrap')).json()
setToken(boot.token); useAuth().user.value=boot.user
createApp({setup(){
  const trip=ref(boot.trip)
  return()=>h(TripFootprints,{trip:trip.value,day:trip.value.days[0],onUpdated:value=>trip.value=value})
}}).mount('#app')
