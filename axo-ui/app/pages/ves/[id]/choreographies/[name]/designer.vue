<template>
  <div>
  <ClientOnly>
    <ChoreographyDesigner v-if="doc && seedReady" :key="`${doc.choreography_id}:${seed?.version ?? ''}`" :doc="doc" :seed="seed" :readonly="!canEdit"
                          readonly-reason="View only: Developers and above edit" @saved="onSaved" />
    <template #fallback><AxoSkeleton :lines="10" /></template>
  </ClientOnly>
  </div>
</template>

<script setup lang="ts">
// The designer tab: Developers and above edit (a draft kept in this
// browser until saved as the next version), Viewers look. ?from=N starts
// the draft from version N ("Edit from this version").
import type { Choreography } from '~/utils/choreographies'

const { doc, canEdit, load, name, version } = useCurrentChoreography()
const { load: loadList } = useChoreographies()
const { id } = useCurrentVe()
const notifier = useNotifier()
const route = useRoute()
const router = useRouter()
const seed = ref<Choreography | null>(null)
const seedReady = ref(!route.query.from)
onMounted(async () => {
  const from = Number(route.query.from)
  if (!from) return
  try { seed.value = await version(from) } catch (err) { notifier.error(err, `v${from} couldn't load`) }
  seedReady.value = true
  router.replace({ query: { ...route.query, from: undefined } })
})
async function onSaved(saved: Choreography) {
  seed.value = null
  await Promise.all([load(), loadList(true)])
  if (saved.name !== name.value) await navigateTo(`/ves/${id.value}/choreographies/${encodeURIComponent(saved.name)}/designer`)
}
</script>
