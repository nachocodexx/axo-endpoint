import type { ActiveObject, ClassVersion } from '../utils/objects'

/** The VE's objects and classes (GET /objects, GET /object-classes), shared
 *  by the objects pages of one VE and read again when asked (or when another
 *  VE is opened). The pages read again on the VEM's "objects" live feed. */
export function useObjects() {
  const { id, api, vemUp } = useCurrentVe()
  const state = useState<{ ve: string | null; objects: ActiveObject[]; classes: ClassVersion[]; loadedAt: number }>(
    'axo-objects', () => ({ ve: null, objects: [], classes: [], loadedAt: 0 }))
  const loading = ref(false)
  const error = ref<unknown>(null)

  async function load(force = false) {
    if (!vemUp.value) return
    if (!force && state.value.ve === id.value && Date.now() - state.value.loadedAt < 5000) return
    loading.value = true
    error.value = null
    try {
      const [objects, classes] = await Promise.all([api<ActiveObject[]>('/objects'), api<ClassVersion[]>('/object-classes')])
      state.value = { ve: id.value, objects, classes, loadedAt: Date.now() }
    } catch (err) {
      error.value = err
    } finally {
      loading.value = false
    }
  }

  const mine = computed(() => state.value.ve === id.value)
  const objects = computed(() => (mine.value ? state.value.objects : []))
  const classes = computed(() => groupClasses(mine.value ? state.value.classes : []))
  const classOf = (name: string) => classes.value.find(c => c.class_name === name)
  const versionOf = (name: string, version: number) => classOf(name)?.versions.find(v => v.version === version)
  const objectOf = (name: string) => objects.value.find(o => o.name === name)
  const objectsOn = (className: string, version?: number) => objects.value.filter(o => o.class_name === className
    && (version === undefined || o.class_version === version))

  return { objects, classes, loading, error, load, classOf, versionOf, objectOf, objectsOn }
}
