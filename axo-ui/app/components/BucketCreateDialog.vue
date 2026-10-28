<template>
  <v-dialog :model-value="modelValue" max-width="520" @update:model-value="emit('update:modelValue', $event)">
    <v-card rounded="lg">
      <v-card-title class="pt-5 px-6">New bucket</v-card-title>
      <v-card-text class="px-6">
        <div class="d-flex ga-3 align-start">
          <v-select v-model="owner" :items="owners" label="Owner" style="max-width: 180px" hide-details="auto" />
          <v-text-field v-model="name" label="Name" autofocus :rules="[nameRule]" :prefix="`${owner}/`"
                        hint="Lowercase letters, digits, - . and _" persistent-hint @keyup.enter="create" />
        </div>
        <div class="text-body-2 font-weight-medium mt-5 mb-1">Size limit</div>
        <div class="d-flex ga-3 align-start">
          <v-text-field v-model.number="limit" type="number" min="1" label="Up to" style="max-width: 160px"
                        :rules="[v => v > 0 || 'More than 0']" />
          <v-select v-model="unit" :items="SIZE_UNITS" style="max-width: 110px" />
        </div>
        <div class="text-caption axo-muted">
          Writes that don't fit are refused; you can change it later in the bucket's settings.
          You pay for what it holds, not for the limit.
        </div>
      </v-card-text>
      <v-card-actions class="px-6 pb-5">
        <v-spacer />
        <AxoBtn look="plain" variant="text" @click="emit('update:modelValue', false)">Cancel</AxoBtn>
        <AxoBtn :loading="busy" :disabled="nameRule(name) !== true || !(limit > 0)" @click="create">Create bucket</AxoBtn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
import type { Bucket, SizeUnit } from '../utils/buckets'

// Name, owner (you, or a team you manage) and a size limit (10 GB unless
// changed). The bucket opens once it is created.
const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [boolean]; created: [Bucket] }>()
const { apiFetch } = useApi()
const me = useMeStore()
const notify = useNotifier()

const name = ref('')
const owner = ref('')
const limit = ref(10)
const unit = ref<SizeUnit>('GB')
const busy = ref(false)
const teams = ref<{ name: string; members: { user_id: string; role: string }[] }[]>([])

const owners = computed(() => [me.me?.username ?? '', ...teams.value
  .filter(t => t.members.some(m => m.user_id === me.me?.user_id && ['owner', 'admin'].includes(m.role)))
  .map(t => t.name)].filter(Boolean))

const nameRule = (v: string) => (/^[a-z0-9](?:[a-z0-9._-]{0,61}[a-z0-9])?$/.test(v) && !v.includes('..'))
  || 'Up to 63 lowercase letters, digits, - . _ (starting and ending with a letter or digit)'

watch(() => props.modelValue, async (open) => {
  if (!open) return
  name.value = ''
  limit.value = 10
  unit.value = 'GB'
  owner.value = me.me?.username ?? ''
  try { teams.value = await apiFetch('/teams') } catch { teams.value = [] }
})

async function create() {
  if (nameRule(name.value) !== true || !(limit.value > 0)) return
  busy.value = true
  try {
    const bucket = await apiFetch<Bucket>('/buckets', { method: 'POST', body: {
      name: name.value, owner: owner.value === me.me?.username ? null : owner.value,
      quota_bytes: joinSize(limit.value, unit.value),
    } })
    notify.success(`${bucket.name} is ready`)
    emit('created', bucket)
    emit('update:modelValue', false)
  } catch (err) {
    notify.error(err, "The bucket couldn't be created")
  } finally {
    busy.value = false
  }
}
</script>
