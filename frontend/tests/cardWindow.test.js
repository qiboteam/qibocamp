import assert from 'node:assert/strict'
import { test } from 'node:test'
import { effectScope, ref } from 'vue'

// The page's observers, driven by the test.
const intersections = []
globalThis.IntersectionObserver = class {
  constructor(callback, options) {
    Object.assign(this, { callback, options, elements: new Set() })
    intersections.push(this)
  }
  observe(element) { this.elements.add(element) }
  unobserve(element) { this.elements.delete(element) }
  disconnect() { this.elements.clear() }
}
let resizer = null
globalThis.ResizeObserver = class {
  constructor(callback) { Object.assign(this, { callback, elements: new Set() }); resizer = this }
  observe(element) { this.elements.add(element) }
  unobserve(element) { this.elements.delete(element) }
  disconnect() { this.elements.clear() }
}

const { ESTIMATED_HEIGHT, MOUNT_MARGIN, UNMOUNT_MARGIN, useCardWindow } = await import('../src/composables/useCardWindow.js')

/** A slot: its height, and where its top is. */
function slot(height, top = 0) {
  return { offsetHeight: height, top, getBoundingClientRect() { return { top: this.top } } }
}

function setup({ active = true } = {}) {
  const panel = { scrollTop: 1000, getBoundingClientRect: () => ({ top: 0 }) }
  const state = { active }
  const scope = effectScope()
  const window = scope.run(() => useCardWindow({ container: ref(panel), active: () => state.active }))
  const before = intersections.length
  return { panel, state, scope, window, observers: () => intersections.slice(before - (before % 2)) }
}

function near(element, intersecting) {
  intersections.find(o => o.options.rootMargin === MOUNT_MARGIN && o.elements.has(element)).callback([{ target: element, isIntersecting: intersecting }])
}
function far(element, intersecting) {
  intersections.find(o => o.options.rootMargin === UNMOUNT_MARGIN && o.elements.has(element)).callback([{ target: element, isIntersecting: intersecting }])
}

test('a card is mounted near the viewport only, its slot standing for it elsewhere', () => {
  const { window, scope } = setup()
  const a = slot(0)
  const b = slot(0)
  window.slotRef('a')(a)
  window.slotRef('b')(b)
  assert.equal(window.isMounted('a'), false)
  assert.deepEqual(window.slotStyle('b'), { height: `${ESTIMATED_HEIGHT}px` })
  near(a, true)
  assert.equal(window.isMounted('a'), true)
  assert.equal(window.slotStyle('a'), null)
  assert.equal(window.isMounted('b'), false)
  scope.stop()
})

test('a card far away is unmounted, leaving its height: nothing moves', () => {
  const { window, scope } = setup()
  const a = slot(1234)
  window.slotRef('a')(a)
  near(a, true)
  // Between the margins it stays.
  near(a, false)
  far(a, true)
  assert.equal(window.isMounted('a'), true)
  far(a, false)
  assert.equal(window.isMounted('a'), false)
  assert.deepEqual(window.slotStyle('a'), { height: '1234px' })
  // A card never measured stands at the measured ones' height.
  const b = slot(0)
  window.slotRef('b')(b)
  assert.deepEqual(window.slotStyle('b'), { height: '1234px' })
  scope.stop()
})

test('a slot above the viewport changing height keeps what is read in place', () => {
  const { window, scope, panel } = setup()
  const above = slot(800, -2000)
  const across = slot(800, -100)
  window.slotRef('above')(above)
  window.slotRef('across')(across)
  resizer.callback([{ target: above }, { target: across }])
  assert.equal(panel.scrollTop, 1000, 'the first sight of a slot moves nothing')
  above.offsetHeight = 1100
  across.offsetHeight = 1300
  resizer.callback([{ target: above }, { target: across }])
  // Wholly above: compensated. Across the top: it may have grown below the reader.
  assert.equal(panel.scrollTop, 1300)
  scope.stop()
})

test('outside the continuous view every card is mounted, and nothing is compensated', () => {
  const { window, scope, state, panel } = setup({ active: false })
  const a = slot(800, -2000)
  window.slotRef('a')(a)
  assert.equal(window.isMounted('a'), true)
  assert.equal(window.slotStyle('a'), null)
  resizer.callback([{ target: a }])
  a.offsetHeight = 900
  resizer.callback([{ target: a }])
  assert.equal(panel.scrollTop, 1000)
  state.active = true
  assert.equal(window.isMounted('a'), false)
  scope.stop()
})

test('a slot ref is one function per protocol, and a removed slot is forgotten', () => {
  const { window, scope } = setup()
  assert.equal(window.slotRef('a'), window.slotRef('a'))
  const a = slot(500)
  window.slotRef('a')(a)
  near(a, true)
  window.slotRef('a')(null)
  assert.equal(window.isMounted('a'), false)
  assert.equal(window.mounted.size, 0)
  scope.stop()
})
