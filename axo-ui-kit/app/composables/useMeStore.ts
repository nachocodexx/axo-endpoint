import { defineStore } from 'pinia'
import type { Me, Session } from '../types/cp'

// A Pinia store (kept in composables/ so it is auto-imported from the layer).
// Who is signed in (GET /me) and signing in / out against the Control Plane.
export const useMeStore = defineStore('axo-me', () => {
  const me = ref<Me | null>(null)
  const loading = ref(false)

  const displayName = computed(() => {
    const u = me.value
    if (!u) return ''
    return `${u.first_name ?? ''} ${u.last_name ?? ''}`.trim() || u.username
  })
  const initials = computed(() => displayName.value.split(/\s+/).map(p => p[0]).join('').slice(0, 2).toUpperCase())
  const isPlatformAdmin = computed(() => me.value?.platform_role === 'admin')

  async function load(force = false) {
    if (me.value && !force) return me.value
    const { apiFetch } = useApi()
    loading.value = true
    try {
      me.value = await apiFetch<Me>('/me')
      return me.value
    } finally {
      loading.value = false
    }
  }

  async function signIn(username: string, password: string) {
    const { apiFetch, session } = useApi()
    const answer = await apiFetch<Session & Record<string, any>>('/auth/login', {
      method: 'POST', body: { username, password },
    })
    session.value = { access_token: answer.access_token, temporal_secret: answer.temporal_secret, username }
    me.value = null
    await Promise.all([load(), useSettings().load(true)])
    useLive().start()
  }

  async function signOut() {
    const { apiFetch, session } = useApi()
    try {
      if (session.value) await apiFetch('/auth/logout', { method: 'POST', quiet401: true })
    } catch { /* already gone: signing out locally is enough */ }
    useLive().stop()
    session.value = null
    me.value = null
    useSettings().clear()
    useNotificationsStore().reset()
    await navigateTo('/login')
  }

  return { me, loading, displayName, initials, isPlatformAdmin, load, signIn, signOut }
})
