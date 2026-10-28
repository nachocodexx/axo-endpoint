<template>
  <div>
    <h2 class="text-h4 font-weight-bold mb-1">{{ invitation ? 'Accept your invitation' : 'Create your account' }}</h2>
    <p class="text-body-2 axo-muted mb-8">
      {{ invitation ? 'You were invited to axo. Create an account to accept.' : 'Free to start: rent a Free VE once you are in.' }}
    </p>
    <v-form ref="form" @submit.prevent="submit">
      <v-row density="compact">
        <v-col cols="6"><v-text-field v-model="body.first_name" label="First name" autocomplete="given-name" /></v-col>
        <v-col cols="6"><v-text-field v-model="body.last_name" label="Last name" autocomplete="family-name" /></v-col>
      </v-row>
      <v-text-field v-model="body.username" label="Username" prepend-inner-icon="mdi-at" autocomplete="username"
                    :rules="[required, usernameRule]" hint="Lowercase letters, digits and dashes — it names your buckets (you/bucket)"
                    persistent-hint class="mb-3" @update:model-value="v => body.username = (v || '').toLowerCase()" />
      <v-text-field v-model="body.email" label="Email" type="email" prepend-inner-icon="mdi-email-outline"
                    autocomplete="email" :rules="[required, emailRule]" class="mb-3" />
      <v-text-field v-model="body.password" label="Password" prepend-inner-icon="mdi-lock-outline"
                    :type="show ? 'text' : 'password'" :append-inner-icon="show ? 'mdi-eye-off' : 'mdi-eye'"
                    autocomplete="new-password" :rules="[minLength]" @click:append-inner="show = !show" />
      <AxoPasswordStrength :password="body.password" class="mb-2" />
      <v-expand-transition>
        <AxoErrorAlert v-if="failed" :error="failed" title="Couldn't create the account" />
      </v-expand-transition>
      <AxoBtn type="submit" block size="large" class="mt-4" :loading="busy">Create account</AxoBtn>
    </v-form>
    <p class="text-body-2 text-center mt-8">
      Already have an account? <NuxtLink to="/login" class="text-primary font-weight-medium">Sign in</NuxtLink>
    </p>
  </div>
</template>

<script setup lang="ts">
// Sign up, then sign in at once. ?code=... comes from an email invitation:
// the account is created as a guest and the invitation accepted.
definePageMeta({ auth: false, layout: 'auth' })
useHead({ title: 'Create account · axo' })

const route = useRoute()
const me = useMeStore()
const { apiFetch } = useApi()
const notify = useNotifier()
const form = ref()
const show = ref(false)
const busy = ref(false)
const failed = ref<unknown>(null)
const invitation = computed(() => (typeof route.query.code === 'string' ? route.query.code : ''))
const body = reactive({ username: '', email: '', password: '', first_name: '', last_name: '' })

// Same rules as the Control Plane (domain/names.py, 8+ character passwords).
const required = (v: string) => !!v || 'Required'
const usernameRule = (v: string) => /^[a-z0-9](?:[a-z0-9-]{0,37}[a-z0-9])?$/.test(v) || 'Lowercase letters, digits and dashes (not at the ends), up to 39'
const emailRule = (v: string) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v) || 'Not an email address'
const minLength = (v: string) => (v?.length ?? 0) >= 8 || 'At least 8 characters'

async function submit() {
  const { valid } = await form.value.validate()
  if (!valid) return
  busy.value = true
  failed.value = null
  try {
    await apiFetch('/auth/signup', { method: 'POST', body: { ...body, invitation_code: invitation.value || null } })
    await me.signIn(body.username, body.password)
    notify.success(`Welcome to axo, ${body.first_name || body.username}!`)
    await navigateTo('/dashboard')
  } catch (err) {
    failed.value = err
  } finally {
    busy.value = false
  }
}
</script>
