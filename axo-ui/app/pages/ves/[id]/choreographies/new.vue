<template>
  <div>
    <v-breadcrumbs :items="[{ title: 'Choreographies', to: `/ves/${id}/choreographies` }, { title: 'New', disabled: true }]"
                   density="compact" class="px-0 pt-0 mb-1" />
    <div class="text-h6 mb-3">New choreography</div>
    <v-alert v-if="!can('developer')" type="info" variant="tonal" density="compact">Only Developers and above make choreographies.</v-alert>
    <template v-else-if="vemUp">
      <div class="d-flex flex-wrap ga-3 mb-3">
        <v-text-field v-model="name" density="compact" variant="outlined" label="Name" placeholder="e.g. nightly-report" persistent-placeholder
                      :error-messages="nameError" hide-details="auto" style="flex: 1 1 240px; max-width: 360px" autofocus />
        <v-text-field v-model="description" density="compact" variant="outlined" label="Description" placeholder="optional" persistent-placeholder
                      hide-details style="flex: 3 1 300px" />
      </div>
      <ClientOnly>
        <ChoreographyDesigner :doc="null" :new-name="name" :new-description="description" @saved="onSaved" />
      </ClientOnly>
    </template>
    <v-alert v-else type="info" variant="tonal" density="compact">The VE is {{ ve?.lifecycle?.toLowerCase() }}: start it to draw a choreography.</v-alert>
  </div>
</template>

<script setup lang="ts">
// A new choreography: its name, then the designer; Create saves v1 and
// opens its page. The drawing so far stays in this browser until then.
import type { Choreography } from '~/utils/choreographies'

const { id, ve, can, vemUp } = useCurrentVe()
useHead(() => ({ title: `New choreography · ${ve.value?.name ?? 'VE'} · axo` }))
const { rows, load } = useChoreographies()
const name = ref('')
const description = ref('')
onMounted(() => load())
const nameError = computed(() => {
  if (!name.value) return undefined
  return choreographyNameProblem(name.value) ?? (rows.value.some(r => r.name === name.value) ? 'A choreography with this name already exists' : undefined)
})
async function onSaved(doc: Choreography) {
  await load(true)
  await navigateTo(`/ves/${id.value}/choreographies/${encodeURIComponent(doc.name)}`)
}
</script>
