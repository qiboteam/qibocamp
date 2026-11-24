<template>
  <div
    ref="plotWrapper"
    :class="thumbnail ? 'my-2 relative' : 'my-4 bg-white p-4 rounded-xl border border-gray-100 shadow-sm relative transition-all duration-300'"
  >
    <div v-if="figure.title && !thumbnail" class="text-sm font-semibold text-gray-800 mb-2 font-mono pr-48">
      {{ figure.title }}
    </div>
    <div v-else-if="!thumbnail" class="h-3"></div>
    <div ref="plotContainer" class="w-full" :class="thumbnail ? 'h-40' : 'min-h-[350px]'"></div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, watch, nextTick, toRaw } from 'vue'
import Plotly from 'plotly.js-dist-min'
import { copyToClipboard } from '../utils/clipboard.js'
import { redrawPlot, registerPlot, unregisterPlot } from '../utils/plotDrawing.js'

const props = defineProps({
  figure: { type: Object, required: true },
  thumbnail: { type: Boolean, default: false }
})

const plotWrapper = ref(null)
const plotContainer = ref(null)
let isRendered = false

/**
 * Drawn when near the viewport, released when far from it, every one drawn
 * for printing: see `utils/plotDrawing.js`. The element registered, and
 * kept, is the wrapper; the plot is drawn in the container inside it.
 */
let registered = null
function schedulePlot() {
  registered = plotWrapper.value
  registerPlot(registered, { draw: renderPlot, release: releasePlot })
}
function unschedulePlot() {
  unregisterPlot(registered)
  registered = null
}
/** Each drawing, counted: one a release overtook draws nothing. */
let drawing = 0
/** Let the graph go, keeping its height: the page does not move under the reader. */
function releasePlot() {
  drawing += 1
  const container = plotContainer.value
  if (!container || !isRendered) return
  const height = container.offsetHeight
  if (typeof container.removeAllListeners === 'function') container.removeAllListeners('plotly_click')
  container.removeEventListener('click', handleNativeClick)
  Plotly.purge(container)
  if (height) container.style.height = `${height}px`
  isRendered = false
}
let lastCopiedTime = 0
let copyFeedbackTimer = null

function getCleanDataAndLayout() {
  const fig = toRaw(props.figure)
  const rawData = fig.data ? JSON.parse(JSON.stringify(fig.data)) : []
  const rawLayout = {
    autosize: true,
    font: { family: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif' },
    paper_bgcolor: 'transparent',
    plot_bgcolor: 'transparent',
    margin: { t: 30, r: 20, b: 40, l: 50 },
    hovermode: 'closest',
    modebar: {
      orientation: 'h',
      bgcolor: 'transparent',
      color: '#6b7280',
      activecolor: '#833dff',
      ...(fig.layout?.modebar || {})
    },
    ...(fig.layout ? JSON.parse(JSON.stringify(fig.layout)) : {})
  }
  if (props.thumbnail) {
    delete rawLayout.width
    rawLayout.height = 160
    rawLayout.autosize = true
    rawLayout.margin = { t: 10, r: 10, b: 25, l: 30 }
    rawLayout.font = { ...rawLayout.font, size: 8 }
    rawLayout.showlegend = false
    rawLayout.title = undefined
  }
  return { rawData, rawLayout }
}

function getPlotHoverText(container) {
  if (!container) return null
  const hoverLayer = container.querySelector('.hoverlayer')
  if (!hoverLayer) return null

  const hoverTexts = hoverLayer.querySelectorAll('.hovertext, .axistext')
  if (!hoverTexts || hoverTexts.length === 0) return null

  const lines = []
  hoverTexts.forEach(ht => {
    const textNodes = ht.querySelectorAll('text')
    textNodes.forEach(t => {
      const tspans = t.querySelectorAll('tspan')
      if (tspans && tspans.length > 0) {
        tspans.forEach(sp => {
          const s = sp.textContent?.replace(/\u00a0/g, ' ').trim()
          if (s) lines.push(s)
        })
      } else {
        const s = t.textContent?.replace(/\u00a0/g, ' ').trim()
        if (s) lines.push(s)
      }
    })
  })

  const uniqueLines = lines.filter(Boolean)
  if (uniqueLines.length === 0) return null

  if (uniqueLines.length > 1) {
    return uniqueLines.join(', ')
  }
  return uniqueLines[0]
}

function flashPlotFeedback() {
  // Flash SVG hover balloon border in purple (solid line)
  const hoverPaths = plotContainer.value?.querySelectorAll('.hoverlayer .hovertext path')
  if (hoverPaths && hoverPaths.length > 0) {
    hoverPaths.forEach(p => {
      const origStroke = p.style.stroke
      const origStrokeWidth = p.style.strokeWidth
      p.style.transition = 'stroke 0.1s ease, stroke-width 0.1s ease'
      p.style.stroke = '#833dff'
      p.style.strokeWidth = '2px'
      setTimeout(() => {
        p.style.stroke = origStroke
        p.style.strokeWidth = origStrokeWidth
      }, 300)
    })
  }

  // Flash plot card container with milder transparent shadow (no solid line)
  if (plotWrapper.value) {
    plotWrapper.value.classList.remove('copy-flash-plot')
    void plotWrapper.value.offsetWidth
    plotWrapper.value.classList.add('copy-flash-plot')
    if (copyFeedbackTimer) clearTimeout(copyFeedbackTimer)
    copyFeedbackTimer = setTimeout(() => {
      plotWrapper.value?.classList.remove('copy-flash-plot')
      copyFeedbackTimer = null
    }, 350)
  }
}

function handleCopyCoordinates(coordsText) {
  if (!coordsText) return
  copyToClipboard(coordsText)
  lastCopiedTime = Date.now()
  flashPlotFeedback()
}

function handlePlotlyClick(data) {
  const balloonText = getPlotHoverText(plotContainer.value)
  let coordsText = balloonText

  if (!coordsText && data?.points && data.points.length > 0) {
    const pt = data.points[0]
    if (pt.hovertext) {
      coordsText = String(pt.hovertext).replace(/\u00a0/g, ' ').trim()
    } else if (pt.text) {
      coordsText = String(pt.text).replace(/\u00a0/g, ' ').trim()
    } else if (pt.z !== undefined && pt.z !== null) {
      coordsText = `x: ${pt.x}, y: ${pt.y}, z: ${pt.z}`
    } else if (pt.x !== undefined && pt.y !== undefined) {
      coordsText = `(${pt.x}, ${pt.y})`
    }
  }

  if (coordsText) {
    handleCopyCoordinates(coordsText)
  }
}

function handleNativeClick(event) {
  if (Date.now() - lastCopiedTime < 350) return
  if (event.target.closest('.modebar-container')) return

  const balloonText = getPlotHoverText(plotContainer.value)
  if (balloonText) {
    handleCopyCoordinates(balloonText)
  }
}

function attachPlotListeners() {
  const el = plotContainer.value
  if (!el) return

  if (typeof el.removeAllListeners === 'function') {
    el.removeAllListeners('plotly_click')
  }
  el.on('plotly_click', handlePlotlyClick)

  el.removeEventListener('click', handleNativeClick)
  el.addEventListener('click', handleNativeClick)
}

async function renderPlot() {
  const current = ++drawing
  await nextTick()
  if (current !== drawing || !plotContainer.value || !props.figure) return

  const { rawData, rawLayout } = getCleanDataAndLayout()
  // The height kept by a release goes: the graph sizes the container again.
  plotContainer.value.style.removeProperty('height')

  const config = {
    responsive: true,
    displayModeBar: !props.thumbnail,
    staticPlot: props.thumbnail,
    displaylogo: false,
    modeBarButtonsToRemove: ['lasso2d', 'select2d']
  }

  try {
    // Plotly.react is high-performance and reuses the graph div safely
    await Plotly.react(plotContainer.value, rawData, rawLayout, config)
    isRendered = true
    if (!props.thumbnail) attachPlotListeners()
  } catch (err) {
    console.error('Error rendering Plotly figure:', err)
  }
}

let resizeTimer = null
function handleResize() {
  if (resizeTimer) cancelAnimationFrame(resizeTimer)
  resizeTimer = requestAnimationFrame(() => {
    // A plot not drawn yet is drawn when its turn comes, at its new size.
    if (isRendered && plotContainer.value?.clientHeight > 0) Plotly.Plots.resize(plotContainer.value)
  })
}

onMounted(() => {
  schedulePlot()
  window.addEventListener('resize', handleResize)
  // Printing draws every plot (utils/plotDrawing.js); the page's size is the screen's again after.
  window.addEventListener('beforeprint', handleResize)
  window.addEventListener('afterprint', handleResize)
})

// Watch replacements from Live without traversing mutable Plotly figures.
watch(() => [props.figure, props.figure?.id, props.figure?.title], () => redrawPlot(registered))

onUnmounted(() => {
  drawing += 1
  unschedulePlot()
  window.removeEventListener('resize', handleResize)
  window.removeEventListener('beforeprint', handleResize)
  window.removeEventListener('afterprint', handleResize)
  if (resizeTimer) cancelAnimationFrame(resizeTimer)
  if (copyFeedbackTimer) clearTimeout(copyFeedbackTimer)
  if (plotContainer.value) {
    if (typeof plotContainer.value.removeAllListeners === 'function') {
      plotContainer.value.removeAllListeners('plotly_click')
    }
    plotContainer.value.removeEventListener('click', handleNativeClick)
    Plotly.purge(plotContainer.value)
  }
})
</script>

<style scoped>
/* Plotly Modebar Styling - Injected in container to match web app UI */
:deep(.modebar-container) {
  top: -26px !important;
  right: 4px !important;
  z-index: 20 !important;
  transition: opacity 0.2s ease-in-out;
}

:deep(.modebar-container:hover .modebar) {
  opacity: 1 !important;
}

:deep(.modebar) {
  background: rgba(255, 255, 255, 0.96) !important;
  backdrop-filter: blur(8px) !important;
  -webkit-backdrop-filter: blur(8px) !important;
  border: 1px solid #e5e7eb !important;
  border-radius: 0.5rem !important;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05), 0 1px 2px rgba(0, 0, 0, 0.03) !important;
  padding: 2px 4px !important;
  display: inline-flex !important;
  align-items: center !important;
  gap: 2px !important;
}

:deep(.modebar-group) {
  display: inline-flex !important;
  align-items: center !important;
  padding: 0 !important;
  margin: 0 !important;
  background: transparent !important;
}

:deep(.modebar-group:not(:last-child)) {
  border-right: 1px solid #f3f4f6 !important;
  padding-right: 3px !important;
  margin-right: 3px !important;
}

:deep(.modebar-btn) {
  height: 24px !important;
  width: 24px !important;
  padding: 3px !important;
  display: inline-flex !important;
  align-items: center !important;
  justify-content: center !important;
  border-radius: 0.375rem !important;
  cursor: pointer !important;
  transition: background-color 0.15s ease, color 0.15s ease !important;
  margin: 0 !important;
}

:deep(.modebar-btn:hover) {
  background-color: #f3f0ff !important;
}

:deep(.modebar-btn.active) {
  background-color: #ebe0ff !important;
}

:deep(.modebar-btn svg) {
  height: 14px !important;
  width: 14px !important;
  position: static !important;
  top: auto !important;
}

:deep(.modebar-btn svg path) {
  fill: #6b7280 !important;
  transition: fill 0.15s ease !important;
}

:deep(.modebar-btn:hover svg path) {
  fill: #833dff !important;
}

:deep(.modebar-btn.active svg path) {
  fill: #833dff !important;
}

/* Tooltip on hover */
:deep([data-title]:hover:after) {
  background-color: #111827 !important;
  color: #f9fafb !important;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif !important;
  font-size: 11px !important;
  font-weight: 500 !important;
  border-radius: 0.375rem !important;
  padding: 4px 8px !important;
  box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06) !important;
  border: 1px solid rgba(255, 255, 255, 0.1) !important;
}

:deep([data-title]:hover:before) {
  border-bottom-color: #111827 !important;
}

/* Plot Click-to-Copy Feedback - Mild transparent shadow only (no solid border) */
.copy-flash-plot {
  animation: plot-shadow-flash 0.35s cubic-bezier(0.16, 1, 0.3, 1) forwards !important;
}

@keyframes plot-shadow-flash {
  0% {
    box-shadow: 0 0 0 4px rgba(131, 61, 255, 0.22), 0 4px 12px rgba(131, 61, 255, 0.12) !important;
  }
  50% {
    box-shadow: 0 0 0 2px rgba(131, 61, 255, 0.12), 0 2px 6px rgba(131, 61, 255, 0.06) !important;
  }
  100% {
    box-shadow: 0 1px 3px 0 rgba(0, 0, 0, 0.05) !important;
  }
}

:deep(.hoverlayer) {
  cursor: pointer !important;
}

:deep(.hoverlayer text) {
  user-select: none !important;
}
</style>
