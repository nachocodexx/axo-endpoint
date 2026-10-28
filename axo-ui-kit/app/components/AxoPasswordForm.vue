<template>
  <v-form ref="form" @submit.prevent="submit">
    <v-text-field v-model="next" label="New password" prepend-inner-icon="mdi-lock-outline" :autofocus="forced"
                  :type="show ? 'text' : 'password'" :append-inner-icon="show ? 'mdi-eye-off' : 'mdi-eye'"
                  autocomplete="new-password" :rules="[minLength]" class="mb-1" @click:append-inner="show = !show" />
    <AxoPasswordStrength :password="next" class="mb-4" />
    <v-text-field v-model="again" label="Repeat the new password" prepend-inner-icon="mdi-lock-check-outline"
                  :type="show ? 'text' : 'password'" autocomplete="new-password" :rules="[same]" />
    <AxoErrorAlert v-if="failed" :error="failed" title="Couldn't change the password" />
    <AxoBtn type="submit" :block="forced" :size="forced ? 'large' : undefined" class="mt-4" :loading="busy">Change password</AxoBtn>
  </v-form>
</template>

<script setup lang="ts">
// The new password form: the Account page and the forced first change
// (/account/password) both use it. Signs in again with the new password
// right after, then emits `changed`.
defineProps<{ forced?: boolean }>()
const emit = defineEmits<{ changed: [] }>()

const me = useMeStore()
const { apiFetch, session } = useApi()
const notify = useNotifier()
const form = ref()
const next = ref('')
const again = ref('')
const show = ref(false)
const busy = ref(false)
const failed = ref<unknown>(null)
const minLength = (v: string) => (v?.length ?? 0) >= 8 || 'At least 8 characters'
const same = (v: string) => v === next.value || "The two passwords don't match"

async function submit() {
  const { valid } = await form.value.validate()
  if (!valid) return
  busy.value = true
  failed.value = null
  const username = me.me?.username ?? session.value?.username ?? ''
  try {
    await apiFetch('/auth/password', { method: 'POST', body: { new_password: next.value } })
    await me.signIn(username, next.value)
    notify.success('Password changed')
    form.value.reset()
    emit('changed')
  } catch (err) {
    failed.value = err
  } finally {
    busy.value = false
  }
}
</script>
