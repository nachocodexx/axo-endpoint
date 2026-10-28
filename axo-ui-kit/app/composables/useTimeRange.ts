// The time windows the charts offer (agreed: 1h / 24h / 7d / 30d).
export const RANGE_PRESETS = [
  { value: '1h', title: '1 hour', seconds: 3600 },
  { value: '24h', title: '24 hours', seconds: 86400 },
  { value: '7d', title: '7 days', seconds: 7 * 86400 },
  { value: '30d', title: '30 days', seconds: 30 * 86400 },
] as const
export type RangeValue = typeof RANGE_PRESETS[number]['value']

/** `initial` left out = the person's chart range setting. */
export function useTimeRange(initial?: RangeValue, key = 'axo-range') {
  const range = useState<RangeValue>(key, () => initial ?? currentSettings().chart_range)
  const now = ref(Date.now() / 1000)

  const seconds = computed(() => RANGE_PRESETS.find(r => r.value === range.value)!.seconds)
  const window = computed(() => ({ start: now.value - seconds.value, end: now.value }))
  /** The window just before (to compare: "+12 % vs previous 24 hours"). */
  const previous = computed(() => ({ start: now.value - 2 * seconds.value, end: now.value - seconds.value }))

  function touch() { now.value = Date.now() / 1000 }
  watch(range, touch)
  return { range, seconds, window, previous, touch }
}
