import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { BarChart, HeatmapChart, LineChart, PieChart } from 'echarts/charts'
import {
  DataZoomComponent, GridComponent, LegendComponent, MarkLineComponent, TooltipComponent, VisualMapComponent,
} from 'echarts/components'
import VChart from 'vue-echarts'

// Only the chart types the UIs use (keeps the bundle small).
export default defineNuxtPlugin((nuxtApp) => {
  use([CanvasRenderer, LineChart, BarChart, HeatmapChart, PieChart, GridComponent, TooltipComponent,
    LegendComponent, DataZoomComponent, VisualMapComponent, MarkLineComponent])
  nuxtApp.vueApp.component('VChart', VChart)
})
