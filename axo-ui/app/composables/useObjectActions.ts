import type { ActiveObject, ClassVersion } from '../utils/objects'

/** Deleting objects (Admin+, type the name) and class versions (an Admin
 *  any, a Developer the ones they registered; never one an object is on). */
export function useObjectActions() {
  const { api, can } = useCurrentVe()
  const store = useObjects()
  const me = useMeStore()
  const { confirm } = useConfirm()
  const notifier = useNotifier()
  const busy = ref<string | null>(null)

  async function removeObject(o: ActiveObject): Promise<boolean> {
    const ok = await confirm({
      title: `Delete ${o.name}?`, danger: true, typeToConfirm: o.name, confirmText: 'Delete for good',
      text: 'The object and every kept revision go for good; it can’t be restored.',
      consequences: [`${o.calls} call${o.calls === 1 ? '' : 's'} stay in the jobs list and the VE's history`,
        'Values only this object used are removed from storage'],
    })
    if (!ok) return false
    busy.value = `o:${o.name}`
    try {
      await api(`/objects/${encodeURIComponent(o.name)}`, { method: 'DELETE' })
      notifier.success(`${o.name} deleted`)
      await store.load(true)
      return true
    } catch (err) {
      notifier.error(err, `${o.name} couldn't be deleted`)
      return false
    } finally {
      busy.value = null
    }
  }

  const mayDeleteVersion = (v: ClassVersion) => can('admin') || (can('developer') && !!v.created_by && v.created_by === me.me?.user_id)

  async function removeVersion(v: ClassVersion): Promise<boolean> {
    const using = store.objectsOn(v.class_name, v.version)
    if (using.length) {
      notifier.warning(`${v.class_name} v${v.version} is used by ${using.map(o => o.name).slice(0, 5).join(', ')}: upgrade or delete them first`)
      return false
    }
    const ok = await confirm({ title: `Delete ${v.class_name} v${v.version}?`, danger: true, confirmText: 'Delete',
      text: 'Its code goes for good. No object is on it; new objects can’t use it any more.' })
    if (!ok) return false
    busy.value = `v:${v.class_name}:${v.version}`
    try {
      await api(`/object-classes/${encodeURIComponent(v.class_name)}/${v.version}`, { method: 'DELETE' })
      notifier.success(`${v.class_name} v${v.version} deleted`)
      await store.load(true)
      return true
    } catch (err) {
      notifier.error(err, `${v.class_name} v${v.version} couldn't be deleted`)
      return false
    } finally {
      busy.value = null
    }
  }

  return { busy, removeObject, removeVersion, mayDeleteVersion }
}
