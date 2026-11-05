import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createRenderer, h, nextTick, ref } from 'vue'
import { pageOf, previewPage, usePreviewPages, visiblePages } from '../src/composables/usePreviewPages.js'

const run = n => Array.from({ length: n }, (_, i) => ({ id: `p${i}` }))

test('a page of previews, newest first by default, each keeping its slide', () => {
  const protocols = run(30)
  const first = previewPage(protocols)
  assert.equal(first.length, 12)
  assert.deepEqual(first.slice(0, 2).map(item => item.index), [29, 28], 'the latest run first')
  assert.equal(first[0].proto.id, 'p29')
  assert.deepEqual(previewPage(protocols, { order: 'oldest', page: 3 }).map(item => item.index), [24, 25, 26, 27, 28, 29])
  assert.deepEqual(previewPage(protocols, { page: 3 }).map(item => item.index), [5, 4, 3, 2, 1, 0])
  assert.equal(pageOf(0, 30), 3)
  assert.equal(pageOf(0, 30, { order: 'oldest' }), 1)
  assert.equal(pageOf(17, 30, { size: 24 }), 1)
  assert.deepEqual(visiblePages(6, 10), [1, '...', 5, 6, 7, '...', 10])
})

function mount(n = 30) {
  globalThis.localStorage = (() => {
    const map = new Map()
    return { getItem: key => map.get(key) ?? null, setItem: (key, value) => map.set(key, value), removeItem: key => map.delete(key) }
  })()
  const protocols = ref(run(n))
  const selectedId = ref(null)
  const context = ref('report')
  let pages
  const renderer = createRenderer({ createElement: () => ({}), insert: () => {}, remove: () => {}, parentNode: () => null, nextSibling: () => null })
  const app = renderer.createApp({
    setup() {
      pages = usePreviewPages({ protocols: () => protocols.value, selectedId: () => selectedId.value, context: () => context.value })
      return () => h('div')
    }
  })
  app.mount({})
  return { pages, protocols, selectedId, context, unmount: () => app.unmount() }
}

test('the order and the size are remembered, and the page follows what was on screen', async () => {
  const { pages, protocols, selectedId, context, unmount } = mount()
  try {
    assert.equal(pages.order.value, 'newest')
    assert.equal(pages.totalPages.value, 3)
    pages.goTo(9)
    assert.equal(pages.page.value, 3, 'clamped to the last page')
    pages.setOrder('oldest')
    assert.equal(pages.page.value, 1, 'a new order starts again')
    assert.equal(globalThis.localStorage.getItem('qibocamp_preview_order'), 'oldest')
    pages.goTo(2)
    pages.setSize(24)
    assert.equal(pages.page.value, 1, 'the first preview on screen, p12, stays on screen')
    assert.equal(globalThis.localStorage.getItem('qibocamp_preview_page_size'), '24')

    pages.setSize(12)
    selectedId.value = 'p20'
    await nextTick()
    selectedId.value = null
    await nextTick()
    assert.equal(pages.page.value, 2, 'back on the overview: the page of the slide left')

    pages.goTo(3)
    protocols.value = run(10)
    await nextTick()
    assert.equal(pages.page.value, 1, 'never an empty page when protocols go')
    pages.setOrder('newest')
    pages.goTo(1)
    context.value = 'another'
    await nextTick()
    assert.equal(pages.page.value, 1)
  } finally { unmount() }
})
