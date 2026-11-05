<template>
  <div class="flex h-screen overflow-hidden bg-[#f7f7f7]">
    <!-- Left Sidebar with Protocols Summary in lower half -->
    <sidebar
      :report-protocols="protocols"
      :report-id="reportId"
      :live="live"
      :regenerating="regenerating"
      :active-slide="slideshow ? selectedId : undefined"
      @select-protocol="navigateToProtocol"
      @regenerate="handleRegenerate"
      @print-pdf="handlePrintPDF"
    />

    <!-- Main Report Visualization Panel -->
    <main ref="reportPanel" class="flex-1 overflow-y-auto p-4 sm:p-8 report-print-container">
      <div v-if="loading" class="flex flex-col items-center justify-center h-64">
        <loading-spinner label="Preparing your report..." />
        <p class="mt-4 text-xs text-gray-400 font-mono">{{ loadingStatus }}</p>
      </div>

      <div v-else-if="error" class="bg-red-50 text-red-700 p-6 rounded-2xl border border-red-200 max-w-2xl mx-auto my-8">
        <h3 class="font-bold text-base">Unable to load report</h3>
        <p class="text-xs mt-1">{{ error }}</p>
        <p class="text-xs text-gray-500 mt-2 font-mono">Server: {{ activeServer?.name || 'Local Instance' }} ({{ activeServer?.url || 'local' }})</p>
        <div class="flex items-center gap-2 mt-4">
          <button
            type="button"
            @click="goBackToSearch"
            class="inline-block px-3 py-1.5 bg-red-100 text-red-800 rounded-lg text-xs font-semibold cursor-pointer hover:bg-red-200 transition"
          >
            Back to Search
          </button>
          <button @click="loadReportData" class="px-3 py-1.5 bg-white border border-red-200 text-red-700 rounded-lg text-xs font-semibold hover:bg-red-50 transition cursor-pointer">
            Retry
          </button>
        </div>
      </div>

      <div v-else-if="report" class="max-w-5xl mx-auto space-y-6">
        <!-- Top Navigation / Action Bar (hidden when printing) -->
        <div class="no-print flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-4 border-b border-gray-200">
          <button
            type="button"
            @click="goBackToSearch"
            class="inline-flex items-center gap-1.5 text-xs font-semibold text-gray-600 hover:text-[#833dff] transition cursor-pointer group"
            title="Back to search (filters preserved)"
          >
            <svg class="w-4 h-4 text-gray-500 group-hover:text-[#833dff] transition-colors" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 19l-7-7m0 0l7-7m-7 7h18" />
            </svg>
            <span>Back to Search</span>
            <span
              v-if="hasActiveSearch"
              class="px-1.5 py-0.5 rounded-full text-[10px] font-mono bg-purple-100 text-purple-700 font-medium"
            >
              Filtered
            </span>
          </button>

          <!-- Actions: Print to PDF & Regenerate plots -->
          <div class="flex items-center flex-wrap gap-2">
            <div class="inline-flex items-center rounded-xl bg-white border border-gray-200 hover:border-purple-300 shadow-2xs">
              <!--
                The navigation slides in; it goes at once. A leave animation
                would wait on the main thread, which a heavy report keeps
                busy for seconds after the switch, leaving a blank where the
                buttons were.
              -->
              <Transition
                name="slideshow-nav"
                @enter="sizeSlideshowNav"
                @after-enter="el => el.style.removeProperty('width')"
              >
                <nav v-if="slideshow" id="slideshow-navigation" aria-label="Slideshow navigation" class="overflow-hidden shrink-0">
                  <div class="flex items-center w-max gap-0.5 px-1 border-r border-gray-200">
                    <button type="button" @click="moveSlide(-1)" :disabled="slideIndex === 0" aria-label="Previous slide" title="Previous slide (Left arrow)" class="p-1.5 rounded-lg text-gray-600 hover:bg-gray-50 cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed">
                      <ChevronLeft aria-hidden="true" class="w-3.5 h-3.5" />
                    </button>
                    <button type="button" @click="selectSlide(null)" aria-label="Report Overview" title="Report Overview (Up arrow)" class="p-1.5 rounded-lg text-purple-700 hover:bg-gray-50 cursor-pointer">
                      <Home aria-hidden="true" class="w-3.5 h-3.5" />
                    </button>
                    <span class="px-1 text-[10px] text-gray-500 font-mono whitespace-nowrap" aria-live="polite">{{ slideIndex + 1 }} / {{ protocols.length + 1 }}</span>
                    <button type="button" @click="moveSlide(1)" :disabled="slideIndex === protocols.length" aria-label="Next slide" title="Next slide (Right arrow)" class="p-1.5 rounded-lg text-gray-600 hover:bg-gray-50 cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed">
                      <ChevronRight aria-hidden="true" class="w-3.5 h-3.5" />
                    </button>
                  </div>
                </nav>
              </Transition>
              <!-- The button names the mode it switches to, and shows it. -->
              <button
                type="button"
                @click="toggleSlideshow"
                :title="slideshow ? 'Continuous' : 'Slideshow'"
                :aria-label="slideshow ? 'Continuous' : 'Slideshow'"
                :aria-pressed="slideshow"
                :aria-expanded="slideshow"
                :aria-controls="slideshow ? 'slideshow-navigation' : undefined"
                class="relative px-3 py-1.5 rounded-xl bg-white hover:bg-gray-50 text-[#833dff] shrink-0 cursor-pointer"
              >
                <GalleryVertical v-if="slideshow" aria-hidden="true" class="w-4 h-4" />
                <GalleryHorizontalEnd v-else aria-hidden="true" class="w-4 h-4" />
              </button>
            </div>
            <div class="inline-flex items-stretch gap-0.5 text-gray-700">
              <a
                href="https://qibo.science/qibocal/stable/protocols/"
                target="_blank"
                rel="noopener noreferrer"
                class="px-3 py-1.5 rounded-l-full bg-white border border-gray-200 hover:border-purple-300 hover:bg-gray-50 shadow-2xs flex items-center justify-center gap-1.5 transition cursor-pointer"
                title="Open Qibocal official protocols documentation"
                aria-label="Open Qibocal official protocols documentation"
              >
                <svg aria-hidden="true" class="w-3.5 h-3.5 text-[#833dff]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
                </svg>
                <svg aria-hidden="true" class="w-3 h-3 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
                </svg>
              </a>
              <button
                type="button"
                @click="showDocsModal = true"
                class="px-2 py-1.5 rounded-r-full bg-white border border-gray-200 hover:border-purple-300 hover:bg-gray-50 shadow-2xs flex items-center justify-center transition cursor-pointer"
                title="Edit custom protocol documentation mappings for this server"
                aria-label="Edit custom protocol documentation mappings for this server"
              >
                <svg aria-hidden="true" class="w-3.5 h-3.5 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                </svg>
              </button>
            </div>

            <button
              type="button"
              @click="toggleLive"
              :disabled="!canEdit || regenerating"
              :aria-pressed="live"
              class="px-3 py-1.5 rounded-xl text-xs font-semibold bg-white border shadow-2xs flex items-center gap-1.5 transition cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
              :class="live ? 'border-red-300 text-red-700' : 'border-gray-200 text-gray-700 hover:border-purple-300'"
              :title="!canEdit ? 'Live mode requires editor access' : 'Watch for new data and update only changed protocol plots'"
            >
              <span
                class="w-2 h-2 rounded-full"
                :class="live ? 'bg-red-500 live-recording-light' : 'bg-gray-300'"
                aria-hidden="true"
              ></span>
              {{ liveConnecting ? 'Connecting...' : 'Live' }}
            </button>

            <!-- Regenerate button -->
            <button
              @click="!isViewer && handleRegenerate()"
              :disabled="regenerating || isViewer"
              class="px-3 py-1.5 rounded-xl text-xs font-semibold bg-white border border-gray-200 text-gray-700 shadow-2xs flex items-center gap-1.5 transition"
              :class="isViewer ? 'opacity-40 cursor-not-allowed' : 'hover:border-purple-300 cursor-pointer disabled:opacity-50'"
              :title="isViewer ? 'Viewer role cannot regenerate plots' : 'Delete cached plots and re-evaluate protocols'"
            >
              <svg
                class="w-3.5 h-3.5 text-[#833dff]"
                :class="regenerating ? 'animate-spin' : ''"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
              </svg>
              {{ regenerating ? 'Regenerating...' : 'Regenerate Plots' }}
            </button>

            <!-- Print to PDF button -->
            <button
              @click="handlePrintPDF"
              class="px-3.5 py-1.5 rounded-xl text-xs font-semibold bm-btn-primary shadow-2xs flex items-center gap-1.5 cursor-pointer"
            >
              <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 17h2a2 2 0 002-2v-4a2 2 0 00-2-2H5a2 2 0 00-2 2v4a2 2 0 002 2h2m2 4h6a2 2 0 002-2v-4a2 2 0 00-2-2H9a2 2 0 00-2 2v4a2 2 0 002 2zm8-12V5a2 2 0 00-2-2H9a2 2 0 00-2 2v4h10z" />
              </svg>
            </button>
            <component :is="desktopBell" v-if="desktopBell" toolbar />
          </div>
        </div>

        <!-- Notification if regenerated -->
        <div
          v-if="statusBanner"
          class="no-print p-3 border rounded-xl text-xs flex items-center justify-between"
          :class="statusBannerError ? 'bg-amber-50 border-amber-200 text-amber-900' : 'bg-purple-50 border-purple-200 text-purple-900'"
        >
          <span>{{ statusBanner }}</span>
          <button @click="statusBanner = ''" class="text-purple-600 hover:text-purple-900 cursor-pointer">&times;</button>
        </div>

        <plot-generation-errors v-if="qibocalMissing" :protocols="protocols" />

        <!-- Report Header Card -->
        <div id="report-overview" :class="{ 'slide-hidden': slideshow && selectedId !== null }">
          <report-header-card
            :report="report"
            :saving-author="savingAuthor"
            @remove-tag="handleRemoveTag"
            @save-author="handleSaveAuthor"
          />
          <notes-panel
            class="mt-6"
            :report-id="reportId"
            :notes="report.notes"
            title="Session comments"
            @update:notes="report.notes = $event"
          />
          <!-- The previews, a page at a time: only those of the page are drawn. -->
          <div v-if="slideshow" class="no-print mt-6">
            <div v-if="protocols.length > 1" class="flex items-center justify-end gap-1 mb-3 text-xs" role="group" aria-label="Order of the previews">
              <button
                v-for="choice in previewOrders"
                :key="choice.value"
                type="button"
                :aria-pressed="previews.order.value === choice.value"
                class="px-2.5 py-1 rounded-lg border transition cursor-pointer"
                :class="previews.order.value === choice.value
                  ? 'bg-purple-50 border-purple-200 text-[#833dff] font-semibold'
                  : 'bg-white border-gray-200 text-gray-600 hover:bg-gray-50'"
                @click="previews.setOrder(choice.value)"
              >{{ choice.label }}</button>
            </div>
            <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
              <protocol-thumbnail
                v-for="item in previews.items.value"
                :key="item.proto.id"
                :proto="item.proto"
                :index="item.index"
                @select="selectSlide(item.proto.id)"
              />
              <p v-if="!protocols.length" class="text-xs text-gray-500">No protocols in this report yet.</p>
            </div>
            <pagination-bar
              v-if="previews.total.value > PREVIEW_PAGE_SIZES[0]"
              :current-page="previews.page.value"
              :total-pages="previews.totalPages.value"
              :total-reports="previews.total.value"
              :page-size="previews.size.value"
              :pagination-range="previews.range.value"
              :visible-pages="previews.pages.value"
              :sizes="PREVIEW_PAGE_SIZES"
              noun="protocols"
              @change-page="previews.goTo"
              @update:page-size="previews.setSize"
            />
          </div>
        </div>

        <!-- Protocols List with Figures -->
        <div ref="protocolList" :class="slideshow ? '' : 'space-y-6'">
          <protocol-card
            v-for="(proto, idx) in protocols"
            :key="proto.id"
            :proto="proto"
            :index="idx"
            :report-id="reportId"
            :focused="cursorShown && !slideshow && selectedId === proto.id"
            @update-notes="proto.notes = $event"
            :class="{ 'slide-hidden': slideshow && selectedId !== proto.id }"
          />
        </div>
      </div>
    </main>

    <!-- Protocol Documentation Mapping Modal -->
    <protocol-docs-modal
      :show="showDocsModal"
      @close="showDocsModal = false"
    />
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount, nextTick, provide } from 'vue'
import { desktopWidget, publishPage } from '../utils/desktop.js'
import { isPlainKey, isTypingTarget } from '../utils/shortcuts.js'
import { downloadApiFile, getReportDownloadFilename } from '../utils/apiFiles.js'
import { getProtocolDocUrl } from '../utils/protocolDocs.js'

/** The launcher's bell, when this page is inside it. */
const desktopBell = desktopWidget('bell')
import { GalleryHorizontalEnd, GalleryVertical, ChevronLeft, ChevronRight, Home } from 'lucide-vue-next'
import { useRoute, useRouter } from 'vue-router'
import { state, ensureServersLoaded, hasActiveSearchFilters, isViewer, canEdit, canManageQibocal } from '../store.js'
import { useReportDetail } from '../composables/useReportDetail.js'
import { useMagneticFollow } from '../composables/useMagneticFollow.js'
import { slideFromQuery, useReportSlideshow } from '../composables/useReportSlideshow.js'
import LoadingSpinner from '../components/LoadingSpinner.vue'
import Sidebar from '../components/Sidebar.vue'
import ReportHeaderCard from '../components/report/ReportHeaderCard.vue'
import NotesPanel from '../components/report/NotesPanel.vue'
import ProtocolCard from '../components/report/ProtocolCard.vue'
import ProtocolThumbnail from '../components/report/ProtocolThumbnail.vue'
import PaginationBar from '../components/PaginationBar.vue'
import { PREVIEW_PAGE_SIZES, usePreviewPages } from '../composables/usePreviewPages.js'
import ProtocolDocsModal from '../components/modals/ProtocolDocsModal.vue'
import { diagnostics } from '../composables/useDiagnostics.js'
import PlotGenerationErrors from '../components/report/PlotGenerationErrors.vue'
import { isQibocalMissing } from '../utils/plotGeneration.js'

const showDocsModal = ref(false)

function sizeSlideshowNav(element) {
  element.style.width = `${element.scrollWidth}px`
}

const route = useRoute()
const router = useRouter()
const reportId = computed(() => route.params.id)
const activeServer = computed(() => state.activeServer)

const hasActiveSearch = computed(() => {
  return hasActiveSearchFilters(state.searchState?.filters)
})

function goBackToSearch() {
  router.push('/dashboard')
}

const {
  loading,
  loadingStatus,
  error,
  report,
  protocols,
  regenerating,
  statusBanner,
  statusBannerError,
  savingAuthor,
  live,
  liveConnecting,
  toggleLive,
  loadReportData,
  handleRegenerate,
  handleSaveAuthor,
  handleRemoveTag,
  requestProtocolOutputs,
  regenerateProtocol
} = useReportDetail(reportId)

// The cards and thumbnails ask for their outputs as they come near.
provide('requestProtocolOutputs', requestProtocolOutputs)
// Editors regenerate one protocol from its card; viewers see no button.
provide('regenerateProtocol', computed(() => (canEdit.value && !regenerating.value && !live.value ? regenerateProtocol : null)))

const reportPanel = ref(null)
const protocolList = ref(null)
/**
 * The slide in the address, `?protocol=`: each one a page of the browser's
 * history, so Back returns from a protocol to the overview it was opened
 * from, and the overview has none.
 */
function routeToSlide(id, { replace = false } = {}) {
  const query = { ...route.query }
  if (id === null) delete query.protocol
  else query.protocol = id
  if ((route.query.protocol ?? null) === (query.protocol ?? null)) return
  router[replace ? 'replace' : 'push']({ query })
}

const { slideshow, selectedId, slideIndex, showSlide, selectSlide, moveSlide, handleKeydown } = useReportSlideshow({
  protocols: () => protocols.value,
  live: () => live.value,
  context: () => [reportId.value, state.activeServer?.id, state.activeServer?.url],
  container: reportPanel,
  navigate: routeToSlide
})
const previewOrders = [
  { value: 'newest', label: 'Newest first' },
  { value: 'oldest', label: 'Oldest first' }
]
const previews = usePreviewPages({
  protocols: () => protocols.value,
  selectedId: () => selectedId.value,
  context: () => [reportId.value, state.activeServer?.id, state.activeServer?.url]
})
useMagneticFollow({
  protocols: () => protocols.value,
  live: () => live.value && !slideshow.value,
  container: reportPanel,
  lastCard: () => protocolList.value?.lastElementChild
})

const qibocalMissing = computed(() => isQibocalMissing(protocols.value))
const pendingInstallationRegeneration = ref(false)

watch(() => diagnostics.qibocalRevision, () => {
  pendingInstallationRegeneration.value = true
})

watch(
  [pendingInstallationRegeneration, loading, regenerating, report],
  () => {
    if (pendingInstallationRegeneration.value && canManageQibocal.value && report.value && !loading.value && !regenerating.value) {
      pendingInstallationRegeneration.value = false
      handleRegenerate()
    }
  }
)

watch(
  [
    reportId,
    () => state.activeServer?.id,
    () => state.activeServer?.url,
    () => state.auth.token,
    () => state.auth.user?.id,
    () => state.auth.user?.role,
    canManageQibocal
  ],
  () => { pendingInstallationRegeneration.value = false },
  { flush: 'sync' }
)

/**
 * Scroll the report panel — and the panel alone — to an element. The
 * element's own `scrollIntoView` would scroll every ancestor, the window
 * included, which is what moved the whole page when the view switched.
 */
function scrollPanelTo(element, smooth = true) {
  const panel = reportPanel.value
  if (!panel) return
  const top = element
    ? element.getBoundingClientRect().top - panel.getBoundingClientRect().top + panel.scrollTop
    : 0
  panel.scrollTo({ top: Math.max(0, top), behavior: smooth ? 'smooth' : 'auto' })
}

function protocolElement(protoId) {
  if (!protoId) return null
  const cleanId = String(protoId).toLowerCase().trim()
  let el = document.getElementById(`proto-${protoId}`) || document.getElementById(`proto-${cleanId}`)
  if (!el && protocols.value?.length) {
    const matched = protocols.value.find(
      p => p.id?.toLowerCase() === cleanId || p.name?.toLowerCase() === cleanId
    )
    if (matched) {
      el = document.getElementById(`proto-${matched.id}`)
    }
  }
  return el
}

function scrollToProtocol(protoId, smooth = true) {
  const el = protocolElement(protoId)
  if (el) scrollPanelTo(el, smooth)
}

function navigateToProtocol(id, smooth = true) {
  selectSlide(id)
  if (!slideshow.value) {
    if (id === null) scrollPanelTo(null, smooth)
    else scrollToProtocol(id, smooth)
  }
}

/**
 * Switch the view, keeping the place: the protocol on screen becomes the
 * slide, and the slide becomes the card the panel opens on — placed at
 * once, before the eye can see it move.
 */
async function toggleSlideshow() {
  if (!slideshow.value) {
    const panel = reportPanel.value
    const top = panel.getBoundingClientRect().top + panel.clientTop
    const overview = document.getElementById('report-overview')?.getBoundingClientRect()
    const card = [...(protocolList.value?.children || [])].find(element => {
      const bounds = element.getBoundingClientRect()
      return bounds.bottom > top && bounds.top < top + panel.clientHeight
    })
    const visibleProtocol = protocols.value.find(proto => `proto-${proto.id}` === card?.id)
    selectSlide(overview?.bottom > top ? null : visibleProtocol?.id ?? null)
  }
  slideshow.value = !slideshow.value
  // The continuous view scrolls: only the slideshow keeps its place in the address.
  routeToSlide(slideshow.value ? selectedId.value : null, { replace: true })
  await nextTick()
  if (slideshow.value) reportPanel.value.scrollTop = 0
  else navigateToProtocol(selectedId.value, false)
}

// --- The keys of this page -----------------------------------------------------

/** Whether the keyboard's cursor is drawn: after a key, until a click or Escape. */
const cursorShown = ref(false)

/** The protocol under the cursor; null for the header. */
function currentProtocol() {
  return protocols.value.find(proto => proto.id === selectedId.value) || null
}

/** Step to the next or previous protocol: the slide, or the card. */
function stepProtocol(delta) {
  if (slideshow.value) {
    moveSlide(delta)
    return
  }
  const list = protocols.value
  if (!list.length) return
  const index = list.findIndex(proto => proto.id === selectedId.value)
  const next = index < 0 ? (delta > 0 ? 0 : list.length - 1) : Math.max(-1, Math.min(list.length - 1, index + delta))
  cursorShown.value = true
  navigateToProtocol(next < 0 ? null : list[next].id)
}

/** The comments of what is under the cursor: the protocol's, or the session's. */
function toggleComments() {
  const proto = currentProtocol()
  const scope = proto ? protocolElement(proto.id) : document.getElementById('report-overview')
  const panel = scope?.querySelector('details')
  if (!panel) return
  panel.open = !panel.open
  if (panel.open) panel.querySelector('summary')?.focus?.()
}

function onReportKeys(event) {
  if (!isPlainKey(event) || isTypingTarget(event) || event.shiftKey) return
  if (event.target?.closest?.('.js-plotly-plot, [role="dialog"], .fixed')) return
  if (loading.value || error.value || !report.value || showDocsModal.value) return
  const proto = currentProtocol()
  switch (event.key) {
    case 'b': goBackToSearch(); break
    case 'v': toggleSlideshow(); break
    case 'j': case 'l': stepProtocol(1); break
    case 'k': case 'h': stepProtocol(-1); break
    case 'd': {
      const url = proto
        ? getProtocolDocUrl(proto.id, proto.name, state.activeServer)
        : 'https://qibo.science/qibocal/stable/protocols/'
      if (url) window.open(url, '_blank', 'noopener,noreferrer')
      break
    }
    case 'x': {
      if (!proto || !reportId.value) return
      const encRepId = encodeURIComponent(reportId.value)
      const encProtoId = encodeURIComponent(proto.id)
      downloadApiFile(
        `/api/reports/${encRepId}/download/data/${encProtoId}`,
        getReportDownloadFilename(reportId.value, `_${proto.id.replaceAll(':', '-')}`)
      )
      break
    }
    case 'c': toggleComments(); break
    case 'n': router.push({ name: 'platform', params: { id: reportId.value }, query: { type: 'new' } }); break
    case 'o': router.push({ name: 'platform', params: { id: reportId.value }, query: { type: 'old' } }); break
    case 'w':
      if (!canEdit.value || regenerating.value) return
      toggleLive()
      break
    case 'Escape':
      if (!cursorShown.value) return
      cursorShown.value = false
      break
    default:
      return
  }
  event.preventDefault()
}

function hideCursor() {
  cursorShown.value = false
}

watch([slideshow, selectedId], async () => {
  await nextTick()
  window.dispatchEvent(new Event('resize'))
})

function onKeydown(event) {
  if (!loading.value && !error.value && report.value && !showDocsModal.value) handleKeydown(event)
  if (!event.defaultPrevented) onReportKeys(event)
}

async function handlePrintPDF() {
  window.dispatchEvent(new Event('resize'))
  await new Promise(r => setTimeout(r, 120))
  window.print()
}

onMounted(async () => {
  document.addEventListener('keydown', onKeydown)
  document.addEventListener('pointerdown', hideCursor)
  publishPage('report')
  await ensureServersLoaded()
  await loadReportData()
})

onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKeydown)
  document.removeEventListener('pointerdown', hideCursor)
  publishPage(null)
})

/**
 * The protocol the address names: the slide shown, Back and Forward
 * included — the overview when it names none — or, in the continuous
 * view, the card scrolled to. A protocol a Live report has not produced
 * yet is shown when it arrives.
 */
watch(
  [() => loading.value, () => slideFromQuery(route.query.protocol, protocols.value)],
  ([isLoading, id]) => {
    if (isLoading) return
    if (slideshow.value) {
      if (id !== selectedId.value) showSlide(id)
    } else if (id !== null) {
      setTimeout(() => navigateToProtocol(id), 150)
    }
  },
  { immediate: true }
)

watch(
  () => route.params.id,
  (newId, oldId) => {
    if (newId && newId !== oldId) {
      loadReportData()
    }
  }
)

watch(
  () => [state.activeServer?.id, state.activeServer?.url],
  ([newId, newUrl], [oldId, oldUrl]) => {
    if (newId !== oldId || newUrl !== oldUrl) {
      loadReportData()
    }
  }
)
</script>

<style scoped>
@media screen {
  .slide-hidden { display: none !important; }
}

.live-recording-light {
  animation: live-recording 2.4s ease-in-out infinite;
}

.slideshow-nav-enter-active {
  transition: width 180ms ease, opacity 180ms ease, transform 180ms ease;
}

.slideshow-nav-enter-from {
  width: 0 !important;
  opacity: 0;
  transform: translateX(1rem);
}

/* Gone the moment it leaves: no width, no wait. */
.slideshow-nav-leave-active,
.slideshow-nav-leave-to {
  transition: none;
  width: 0 !important;
  opacity: 0;
}

@keyframes live-recording {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.25; }
}

@media (prefers-reduced-motion: reduce) {
  .live-recording-light { animation: none; }
  .slideshow-nav-enter-active,
  .slideshow-nav-leave-active { transition: none; }
}
</style>
