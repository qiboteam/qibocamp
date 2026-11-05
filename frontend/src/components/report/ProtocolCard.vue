<template>
  <div
    :id="`proto-${proto.id}`"
    ref="root"
    class="bm-card p-6 border border-gray-100 page-break"
    :class="{ 'outline outline-2 outline-offset-2 outline-[#833dff]/45': focused }"
  >
    <!-- Protocol Header -->
    <div class="flex items-center justify-between pb-3 border-b border-gray-100">
      <div class="flex items-center gap-2.5">
        <span class="w-6 h-6 rounded-lg bg-purple-100 text-[#833dff] font-bold text-xs flex items-center justify-center font-mono">
          {{ index + 1 }}
        </span>
        <h2 class="text-lg font-bold text-gray-900 flex items-center gap-1.5">
          <span>{{ proto.name }}</span>
          <a
            v-if="docUrl"
            :href="docUrl"
            target="_blank"
            rel="noopener noreferrer"
            class="text-gray-400 hover:text-[#833dff] transition-colors p-1 rounded hover:bg-purple-50 inline-flex items-center"
            :title="isSpecificDoc ? `Open Qibocal documentation for ${proto.name}` : 'Open Qibocal protocols documentation'"
          >
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
            </svg>
          </a>
        </h2>
      </div>

      <div class="flex items-center gap-2">
        <span class="text-xs font-mono text-gray-400">⏱️ {{ proto.execution_time || 'N/A' }}</span>
        <span
          class="px-2 py-0.5 rounded text-xs font-semibold"
          :class="badge.class"
          :title="proto.regenerating ? 'Being generated again: the plots below are the previous ones until the new ones arrive.' : ''"
        >
          {{ badge.label }}
        </span>

        <!-- Generate this protocol's plots again: its previous ones stay until the new ones arrive. -->
        <button
          v-if="regenerateProtocol"
          type="button"
          class="no-print px-2 py-1 rounded-lg bg-white hover:bg-purple-50 text-gray-700 hover:text-[#833dff] border border-gray-200 hover:border-purple-300 shadow-2xs flex items-center gap-1.5 transition cursor-pointer text-xs font-medium shrink-0 disabled:opacity-50 disabled:cursor-not-allowed"
          :disabled="busy"
          :title="busy ? 'Its plots are being generated' : `Regenerate the plots of ${proto.name}`"
          @click="regenerateProtocol(proto.id)"
        >
          <RefreshCw class="w-3.5 h-3.5" :class="{ 'animate-spin': busy }" aria-hidden="true" />
          <span class="text-[11px] font-semibold">Regenerate</span>
        </button>

        <!-- Protocol Docs Button -->
        <a
          v-if="docUrl"
          :href="docUrl"
          target="_blank"
          rel="noopener noreferrer"
          class="no-print px-2.5 py-1 rounded-lg bg-white hover:bg-purple-50 text-gray-700 hover:text-[#833dff] border border-gray-200 hover:border-purple-300 shadow-2xs flex items-center gap-1.5 transition cursor-pointer text-xs font-medium shrink-0"
          :title="isSpecificDoc ? `Open Qibocal documentation for ${proto.name}` : 'Open Qibocal protocols documentation'"
        >
          <svg class="w-3.5 h-3.5 text-[#833dff]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
          </svg>
          <span class="text-[11px] font-semibold">Docs</span>
          <svg class="w-3 h-3 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
          </svg>
        </a>

        <!-- Protocol Data Download Button -->
        <api-file-button
          v-if="reportId"
          :path="downloadDataPath"
          :filename="downloadDataFilename"
          class="no-print px-2 py-1 rounded-lg bg-white hover:bg-purple-50 text-gray-700 hover:text-[#833dff] border border-gray-200 hover:border-purple-300 shadow-2xs flex items-center gap-1.5 transition cursor-pointer text-xs font-medium shrink-0"
          :title="`Download ${proto.name} data (.zip)`"
        >
          <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
          </svg>
          <span class="text-[11px] font-semibold">Data</span>
        </api-file-button>
      </div>
    </div>

    <!-- Error message if plot could not be generated -->
    <div
      v-if="proto.error"
      class="my-4 p-4 rounded-xl bg-amber-50/80 border border-amber-200/80 text-amber-900 flex items-start gap-3"
    >
      <span class="text-lg leading-none mt-0.5">⚠️</span>
      <div>
        <p class="font-semibold text-xs tracking-wide uppercase text-amber-950">Plot Unavailable</p>
        <p class="text-xs text-amber-800 mt-0.5 leading-relaxed">{{ proto.error }}</p>
      </div>
    </div>

    <!-- Injected Protocol HTML Output -->
    <div
      v-if="proto.html"
      class="protocol-html-container my-4 overflow-x-auto rounded-xl border border-gray-100 bg-white shadow-2xs"
      v-html="safeHtml"
      @click="handleTableClick"
    ></div>

    <!-- Outputs not fetched yet: they come as the card nears the viewport. -->
    <div v-if="pendingOutputs" class="my-4 rounded-xl border border-dashed border-gray-200 min-h-[120px] flex items-center justify-center text-xs text-gray-400">
      {{ proto.outputs === 'failed' ? 'The plots could not be fetched.' : proto.outputs === 'generating' ? 'Generating plots…' : 'Fetching plots…' }}
    </div>

    <!-- Injected Plotly Figures -->
    <div v-else-if="proto.figures && proto.figures.length > 0" class="mt-4 space-y-4">
      <plotly-viewer
        v-for="fig in proto.figures"
        :key="fig.id || fig.title"
        :figure="fig"
      />
    </div>
    <div v-else-if="!proto.error && !proto.html" class="my-4 text-xs text-gray-400 italic">
      No figures or tables available for this routine.
    </div>
    <!--
      The parameters the protocol was run with, as Qibocal wrote them — to
      action.yml, or params.json. Folded by default, read from the server
      when unfolded, and downloadable as the file it is.
    -->
    <details v-if="reportId" class="group/action mt-4" @toggle="onActionToggle($event.target.open)">
      <summary class="flex items-center gap-1.5 cursor-pointer select-none text-xs text-gray-500 hover:text-gray-800 transition py-1 list-none [&::-webkit-details-marker]:hidden">
        <svg class="w-3 h-3 transition-transform group-open/action:rotate-90" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
        </svg>
        <span class="font-semibold">Parameters</span>
        <span v-if="action.name" class="text-gray-400 font-mono">{{ action.name }}</span>
        <api-file-button
          :path="`${actionPath}?download=true`"
          :filename="`${proto.id}_${action.name || 'parameters'}`"
          class="no-print ml-auto inline-flex items-center gap-1 text-[11px] text-gray-500 hover:text-[#833dff] cursor-pointer"
          title="Download the parameter file"
          @click.stop
        >
          <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
          </svg>
          Download
        </api-file-button>
      </summary>
      <div class="mt-1.5 pl-4.5">
        <p v-if="action.loading" class="text-xs text-gray-400" role="status">Loading…</p>
        <p v-else-if="action.error" class="text-xs text-amber-700" role="alert">{{ action.error }}</p>
        <pre v-else-if="action.text" class="text-[11px] leading-relaxed font-mono text-gray-800 bg-[#f7f5ff] rounded-lg px-3 py-2 overflow-x-auto whitespace-pre">{{ action.text }}</pre>
      </div>
    </details>

    <notes-panel
      v-if="reportId"
      class="mt-2"
      :report-id="reportId"
      :protocol-id="proto.id"
      :notes="proto.notes"
      title="Protocol comments"
      @update:notes="emit('update-notes', $event)"
    />
  </div>
</template>

<script setup>
import { computed, inject, reactive, ref, unref, watch } from 'vue'
import { RefreshCw } from 'lucide-vue-next'
import { state, apiFetch } from '../../store.js'
import { useNearViewport } from '../../composables/useNearViewport.js'
import { getProtocolDocUrl, hasSpecificDoc } from '../../utils/protocolDocs.js'
import { copyToClipboard } from '../../utils/clipboard.js'
import { sanitizeHtml } from '../../utils/sanitize.js'
import { getReportDownloadFilename } from '../../utils/apiFiles.js'
import ApiFileButton from '../ApiFileButton.vue'
import PlotlyViewer from '../PlotlyViewer.vue'
import NotesPanel from './NotesPanel.vue'

const emit = defineEmits(['update-notes'])

const props = defineProps({
  proto: { type: Object, required: true },
  index: { type: Number, required: true },
  reportId: { type: String, default: null },
  /** Under the keyboard's cursor, in the continuous view: outlined. */
  focused: { type: Boolean, default: false }
})

/**
 * The outputs — HTML and figures — are fetched when the card comes near
 * the viewport, through what the report page provides; a report loaded
 * whole has nothing pending and asks for nothing.
 */
const root = ref(null)
const requestOutputs = inject('requestProtocolOutputs', null)
const pendingOutputs = computed(() => ['pending', 'loading', 'failed', 'generating'].includes(props.proto?.outputs))
useNearViewport(root, () => {
  // Fetched when cached, or generated first when not: the page knows which.
  if (['pending', 'generating'].includes(props.proto?.outputs) && requestOutputs) requestOutputs(props.proto.id)
})

/** Regenerating, offered to editors by the report page; absent otherwise. */
const offered = inject('regenerateProtocol', null)
const regenerateProtocol = computed(() => unref(offered))
const busy = computed(() => Boolean(props.proto?.regenerating) || props.proto?.outputs === 'generating')

/** What the protocol is: being generated, again or for the first time, or its status. */
const badge = computed(() => {
  if (props.proto?.regenerating) return { label: 'regenerating', class: 'bg-purple-100 text-[#3b107e]' }
  if (props.proto?.outputs === 'generating') return { label: 'generating', class: 'bg-gray-100 text-gray-600' }
  if (props.proto?.status === 'error') return { label: props.proto.status, class: 'bg-amber-100 text-amber-800' }
  return { label: props.proto?.status, class: 'bg-emerald-100 text-emerald-800' }
})

/** The protocol's HTML, as a report from any server may carry it: cleaned. */
const safeHtml = computed(() => sanitizeHtml(props.proto?.html))

const docUrl = computed(() => {
  return getProtocolDocUrl(props.proto?.id, props.proto?.name, state.activeServer)
})

const isSpecificDoc = computed(() => {
  return hasSpecificDoc(props.proto?.id, props.proto?.name, state.activeServer)
})

/** The parameter file: read when its block is unfolded, once per protocol. */
const action = reactive({ text: '', name: '', loading: false, error: '', loaded: false })
const actionPath = computed(() => {
  if (!props.reportId || !props.proto?.id) return ''
  return `/api/reports/${encodeURIComponent(props.reportId)}/protocols/${encodeURIComponent(props.proto.id)}/parameters`
})
async function onActionToggle(open) {
  if (!open || action.loaded || action.loading || !actionPath.value) return
  action.loading = true
  action.error = ''
  try {
    const response = await apiFetch(actionPath.value, { cache: 'no-store' })
    if (response.status === 404) {
      action.error = 'This protocol has no parameter file.'
    } else if (!response.ok) {
      action.error = `The parameter file could not be read (${response.status}).`
    } else {
      action.text = await response.text()
      action.name = response.headers.get('x-parameters-file') || ''
    }
    action.loaded = true
  } catch (error) {
    action.error = error?.message || 'The parameter file could not be read.'
  } finally {
    action.loading = false
  }
}
watch(() => [props.reportId, props.proto?.id], () => {
  Object.assign(action, { text: '', name: '', loading: false, error: '', loaded: false })
})

const downloadDataPath = computed(() => {
  if (!props.reportId || !props.proto?.id) return ''
  const encRepId = encodeURIComponent(props.reportId)
  const encProtoId = encodeURIComponent(props.proto.id)
  return `/api/reports/${encRepId}/download/data/${encProtoId}`
})

const downloadDataFilename = computed(() => props.proto?.id
  ? getReportDownloadFilename(props.reportId, `_${props.proto.id.replaceAll(':', '-')}`)
  : '')

function handleTableClick(event) {
  const target = event.target
  if (!target) return
  if (target.tagName === 'A' || target.tagName === 'BUTTON' || target.closest('a, button')) {
    return
  }

  const cell = target.closest('td, tbody th')
  if (!cell || cell.closest('thead')) return

  const text = (cell.innerText || cell.textContent || '').replace(/\u00a0/g, ' ').trim()
  if (!text) return

  copyToClipboard(text)

  cell.classList.remove('copy-flash')
  void cell.offsetWidth
  cell.classList.add('copy-flash')

  if (cell._copyFlashTimer) {
    clearTimeout(cell._copyFlashTimer)
  }
  cell._copyFlashTimer = setTimeout(() => {
    cell.classList.remove('copy-flash')
    cell._copyFlashTimer = null
  }, 350)
}
</script>

<style scoped>
:deep(.protocol-html-container table) {
  width: 100%;
  border-collapse: separate;
  border-spacing: 0;
  border: none;
  font-size: 0.75rem;
  text-align: left;
}

:deep(.protocol-html-container thead) {
  background-color: #f9fafb;
}

:deep(.protocol-html-container tr) {
  text-align: left !important;
}

:deep(.protocol-html-container th) {
  padding: 0.625rem 1rem;
  font-size: 0.6875rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: #6b7280;
  text-align: left !important;
  border-bottom: 1px solid #e5e7eb;
  white-space: nowrap;
}

:deep(.protocol-html-container td) {
  padding: 0.625rem 1rem;
  font-size: 0.75rem;
  color: #374151;
  border-bottom: 1px solid #f3f4f6;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  vertical-align: middle;
}

:deep(.protocol-html-container tbody tr) {
  transition: background-color 50ms ease;
}

:deep(.protocol-html-container tbody tr:hover) {
  background-color: #faf5ff;
}

:deep(.protocol-html-container tbody tr:last-child td) {
  border-bottom: none;
}

:deep(.protocol-html-container tbody td:first-child) {
  font-weight: 600;
  color: #111827;
}

:deep(.protocol-html-container tbody td:nth-child(n+3)) {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  color: #111827;
}

:deep(.protocol-html-container sub),
:deep(.protocol-html-container sup) {
  font-size: 75%;
  line-height: 0;
  position: relative;
  vertical-align: baseline;
}

:deep(.protocol-html-container sup) {
  top: -0.5em;
}

:deep(.protocol-html-container sub) {
  bottom: -0.25em;
}

:deep(.protocol-html-container tbody th) {
  padding: 0.625rem 1rem;
  font-size: 0.75rem;
  color: #111827;
  font-weight: 600;
  border-bottom: 1px solid #f3f4f6;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  vertical-align: middle;
}

:deep(.protocol-html-container table tbody td),
:deep(.protocol-html-container table tbody th),
:deep(.protocol-html-container table tr:not(thead tr) td) {
  transition: box-shadow 0.15s ease, background-color 0.15s ease;
  user-select: text;
}

:deep(.protocol-html-container table tbody td:hover),
:deep(.protocol-html-container table tbody th:hover),
:deep(.protocol-html-container table tr:not(thead tr) td:hover) {
  position: relative;
  z-index: 5;
  cursor: pointer;
  box-shadow: 0 3px 10px -1px rgba(0, 0, 0, 0.12), 0 1px 4px -1px rgba(0, 0, 0, 0.08);
  border-radius: 4px;
  background-color: #ffffff;
}

:deep(.protocol-html-container .copy-flash) {
  animation: cell-border-flash 0.35s cubic-bezier(0.16, 1, 0.3, 1) forwards !important;
  position: relative !important;
  z-index: 20 !important;
  outline: 2px solid #833dff !important;
  outline-offset: -2px !important;
}

@keyframes cell-border-flash {
  0% {
    outline: 2px solid #833dff !important;
    outline-offset: -2px !important;
    box-shadow: 0 0 0 3px rgba(131, 61, 255, 0.45), inset 0 0 0 2px #833dff !important;
    border-color: #833dff !important;
    background-color: #f3e8ff !important;
  }
  40% {
    outline: 2px solid #833dff !important;
    outline-offset: -2px !important;
    box-shadow: 0 0 0 2px rgba(131, 61, 255, 0.25), inset 0 0 0 2px #833dff !important;
    border-color: #833dff !important;
    background-color: #f7f1fe !important;
  }
  100% {
    outline: 2px solid transparent !important;
    outline-offset: -2px !important;
    box-shadow: none !important;
    border-color: #f3f4f6 !important;
    background-color: transparent !important;
  }
}

:deep(.protocol-html-container table + table) {
  margin-top: 1rem;
  border-top: 2px solid #f3f4f6;
}
</style>
