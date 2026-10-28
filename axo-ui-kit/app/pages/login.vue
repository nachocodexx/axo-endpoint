<template>
  <div>
    <h2 class="text-h4 font-weight-bold mb-1">Welcome back</h2>
    <p class="text-body-2 axo-muted mb-8">Sign in to {{ cfg.appName }} to manage your VEs.</p>
    <v-form ref="form" @submit.prevent="submit">
      <v-text-field v-model="username" label="Username or email" prepend-inner-icon="mdi-account-outline"
                    autocomplete="username" autofocus :rules="[required]" class="mb-2" />
      <v-text-field v-model="password" label="Password" prepend-inner-icon="mdi-lock-outline"
                    :type="show ? 'text' : 'password'" :append-inner-icon="show ? 'mdi-eye-off' : 'mdi-eye'"
                    autocomplete="current-password" :rules="[required]" @click:append-inner="show = !show" />
      <v-expand-transition>
        <AxoErrorAlert v-if="failed" :error="failed" title="Couldn't sign in" class="mb-2" />
      </v-expand-transition>
      <AxoBtn type="submit" block size="large" class="mt-4" :loading="busy">Sign in</AxoBtn>
    </v-form>
    <p v-if="cfg.signup" class="text-body-2 text-center mt-8">
      New to {{ cfg.appName }}? <NuxtLink to="/signup" class="text-primary font-weight-medium">Create an account</NuxtLink>
    </p>
  </div>
</template>

<script setup lang="ts">
// Sign in against the Control Plane; then where the person was going, or
// the password page when the account must change its password first.
definePageMeta({ auth: false, layout: 'auth' })
useHead({ title: 'Sign in · axo' })

const cfg = useAppConfig().axo
const me = useMeStore()
const route = useRoute()
const form = ref()
const username = ref('')
const password = ref('')
const show = ref(false)
const busy = ref(false)
const failed = ref<unknown>(null)
const required = (v: string) => !!v || 'Required'

async function submit() {
  const { valid } = await form.value.validate()
  if (!valid) return
  busy.value = true
  failed.value = null
  try {
    await me.signIn(username.value.trim(), password.value)
    if (me.me?.password_change_required) return navigateTo('/account/password')
    const next = typeof route.query.next === 'string' && route.query.next.startsWith('/') ? route.query.next : cfg.home
    await navigateTo(next)
  } catch (err) {
    failed.value = err
  } finally {
    busy.value = false
  }
}
</script>
