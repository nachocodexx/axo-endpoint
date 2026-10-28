<template>
  <v-dialog :model-value="modelValue" max-width="500" @update:model-value="emit('update:modelValue', $event)">
    <v-card rounded="lg">
      <v-card-title class="pt-5 px-6">Transfer {{ targetName }}</v-card-title>
      <v-card-text class="px-6">
        <v-combobox v-model="to" :items="suggestions" label="Username or team name" autofocus
                    :hint="hint" persistent-hint :return-object="false" item-title="title" item-value="value">
          <template #item="{ props: item }">
            <v-list-item v-bind="item" prepend-icon="mdi-account-group-outline"
                         :subtitle="`Your team · you're ${teamRoleTitle(roleIn(String(item.title)))}`" />
          </template>
        </v-combobox>
      </v-card-text>
      <v-card-actions class="px-6 pb-5">
        <v-spacer />
        <AxoBtn look="plain" variant="text" @click="emit('update:modelValue', false)">Cancel</AxoBtn>
        <AxoBtn :loading="busy" :disabled="!String(to ?? '').trim()" @click="send">Ask them</AxoBtn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
// Ask a person or a team to become the owner of a VE or a bucket; your own
// teams are suggested. Nothing changes until they accept (in their inbox).
const props = defineProps<{ modelValue: boolean; kind: 've' | 'bucket'; target: string; targetName: string; currentOwner?: string }>()
const emit = defineEmits<{ 'update:modelValue': [boolean]; sent: [] }>()
const { apiFetch } = useApi()
const me = useMeStore()
const notify = useNotifier()

const to = ref<string | null>('')
const busy = ref(false)
watch(() => props.modelValue, (open) => { if (open) to.value = '' })

const suggestions = computed(() => (me.me?.teams ?? [])
  .filter(t => t.name !== props.currentOwner)
  .map(t => ({ title: t.name, value: t.name, role: t.role })))
const roleIn = (team: string) => me.me?.teams.find(t => t.name === team)?.role
const hint = computed(() => (props.kind === 've'
  ? 'The new owner pays its renewals from the next period. Nothing changes until they accept; you can cancel until then.'
  : 'The new owner pays for what it stores. Nothing changes until they accept; you can cancel until then.'))

async function send() {
  const who = String(to.value ?? '').trim()
  if (!who || busy.value) return
  busy.value = true
  try {
    await apiFetch('/transfers', { method: 'POST', body: { kind: props.kind, target: props.target, to: who } })
    notify.success(`${who} was asked to accept ${props.targetName}`, { to: { path: '/inbox', query: { tab: 'transfers' } } })
    useInbox().load()
    emit('sent')
    emit('update:modelValue', false)
  } catch (err) { notify.error(err, "The transfer couldn't be asked for") } finally { busy.value = false }
}
</script>
