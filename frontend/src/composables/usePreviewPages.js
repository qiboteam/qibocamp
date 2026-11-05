import { computed, ref, watch } from 'vue'

/** How many previews a page of the overview holds: rows of the three-column grid. */
export const PREVIEW_PAGE_SIZES = [12, 24, 48]
const ORDER_KEY = 'qibocamp_preview_order'
const SIZE_KEY = 'qibocamp_preview_page_size'

function remembered(key, allowed, fallback) {
  try {
    const value = globalThis.localStorage?.getItem(key)
    const parsed = typeof fallback === 'number' ? Number(value) : value
    return allowed.includes(parsed) ? parsed : fallback
  } catch {
    return fallback
  }
}

function remember(key, value) {
  try {
    globalThis.localStorage?.setItem(key, String(value))
  } catch {
    // A preference not kept is no reason to fail.
  }
}

/**
 * The previews of one page: `{ proto, index }`, `index` being the protocol's
 * place in the execution order — its slide — whichever order they are shown in.
 */
export function previewPage(protocols = [], { order = 'newest', page = 1, size = PREVIEW_PAGE_SIZES[0] } = {}) {
  const items = protocols.map((proto, index) => ({ proto, index }))
  if (order === 'newest') items.reverse()
  const start = (page - 1) * size
  return items.slice(start, start + size)
}

/** The page, in a given order, that shows the protocol at `index` of the execution. */
export function pageOf(index, total, { order = 'newest', size = PREVIEW_PAGE_SIZES[0] } = {}) {
  if (index < 0 || index >= total) return 1
  const position = order === 'newest' ? total - 1 - index : index
  return Math.floor(position / size) + 1
}

/** The page numbers to offer, with ellipses past seven, as the reports list does. */
export function visiblePages(current, total) {
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1)
  if (current <= 4) return [1, 2, 3, 4, 5, '...', total]
  if (current >= total - 3) return [1, '...', total - 4, total - 3, total - 2, total - 1, total]
  return [1, '...', current - 1, current, current + 1, '...', total]
}

/**
 * The previews of the slideshow's overview, a page at a time: only those of
 * the page are drawn, and fetch their figures. Newest first by default, the
 * order and the page size remembered. Coming back to the overview from a
 * slide shows the page that slide's preview is on.
 */
export function usePreviewPages({ protocols, selectedId, context }) {
  const order = ref(remembered(ORDER_KEY, ['newest', 'oldest'], 'newest'))
  const size = ref(remembered(SIZE_KEY, PREVIEW_PAGE_SIZES, PREVIEW_PAGE_SIZES[0]))
  const page = ref(1)

  const total = computed(() => protocols().length)
  const totalPages = computed(() => Math.max(1, Math.ceil(total.value / size.value)))
  const items = computed(() => previewPage(protocols(), { order: order.value, page: page.value, size: size.value }))
  const range = computed(() => {
    if (!total.value) return { start: 0, end: 0 }
    const start = (page.value - 1) * size.value + 1
    return { start, end: Math.min(start + size.value - 1, total.value) }
  })
  const pages = computed(() => visiblePages(page.value, totalPages.value))

  function goTo(next) {
    page.value = Math.min(Math.max(1, next), totalPages.value)
  }

  function setOrder(next) {
    order.value = next
    remember(ORDER_KEY, next)
    page.value = 1
  }

  function setSize(next) {
    // The first preview on screen stays on screen.
    const first = items.value[0]?.index
    size.value = next
    remember(SIZE_KEY, next)
    page.value = first === undefined ? 1 : pageOf(first, total.value, { order: order.value, size: next })
  }

  // Protocols removed — a Live report shrinking — never leave an empty page.
  watch(totalPages, last => { if (page.value > last) page.value = last })

  // Back on the overview from a slide: the page its preview is on.
  watch(selectedId, (current, previous) => {
    if (current !== null || previous === null || previous === undefined) return
    const index = protocols().findIndex(proto => proto.id === previous)
    if (index >= 0) page.value = pageOf(index, total.value, { order: order.value, size: size.value })
  })

  // Another report: its first page.
  watch(context, () => { page.value = 1 })

  return { order, size, page, total, totalPages, items, range, pages, goTo, setOrder, setSize }
}
