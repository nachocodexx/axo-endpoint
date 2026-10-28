// Colors ECharts needs as plain values, following the light / dark theme.
export function useChartTheme() {
  const theme = useTheme()
  return computed(() => {
    const dark = theme.current.value.dark
    return {
      dark,
      text: dark ? '#C9CDD2' : '#4A4F57',
      muted: dark ? '#7D838B' : '#8A8F98',
      grid: dark ? 'rgba(255,255,255,0.07)' : 'rgba(0,0,0,0.07)',
      tooltipBg: dark ? '#1B1C1F' : '#FFFFFF',
      tooltipBorder: dark ? '#2A2B2F' : '#E7E9EE',
    }
  })
}
