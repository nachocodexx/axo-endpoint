import { defineStore } from 'pinia'
import type { Notice } from '../types/cp'

// A Pinia store (kept in composables/ so it is auto-imported from the layer).
// The bell: every notice the person got (newest first) and the unread count.
export const useNotificationsStore = defineStore('axo-notifications', () => {
  const items = ref<Notice[]>([])
  const unread = ref(0)
  const loaded = ref(false)

  async function load() {
    const { apiFetch } = useApi()
    const answer = await apiFetch<{ unread: number; items: Notice[] }>('/notifications', { query: { limit: 100 } })
    items.value = answer.items
    unread.value = answer.unread
    loaded.value = true
  }

  /** A notice from the live channel; false when it was already known. */
  function add(note: Notice): boolean {
    if (items.value.some(n => n.notification_id === note.notification_id)) return false
    items.value = [note, ...items.value].slice(0, 200)
    if (!note.read) unread.value++
    return true
  }

  async function markRead(ids?: string[]) {
    const { apiFetch } = useApi()
    const before = items.value.map(n => ({ ...n }))
    const beforeUnread = unread.value
    const target = new Set(ids ?? items.value.filter(n => !n.read).map(n => n.notification_id))
    items.value = items.value.map(n => (target.has(n.notification_id) ? { ...n, read: true } : n))
    unread.value = Math.max(0, unread.value - [...target].filter(id => before.find(n => n.notification_id === id && !n.read)).length)
    try {
      await apiFetch('/notifications/read', { method: 'POST', body: { notification_ids: ids ?? null } })
    } catch (err) {
      items.value = before
      unread.value = beforeUnread
      useNotifier().error(err, "Couldn't mark notices as read")
    }
  }

  function reset() { items.value = []; unread.value = 0; loaded.value = false }

  return { items, unread, loaded, load, add, markRead, reset }
})
