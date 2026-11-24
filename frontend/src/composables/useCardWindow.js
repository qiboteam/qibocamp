import { onScopeDispose, reactive } from 'vue'

/** How close to the panel's viewport a card is mounted. */
export const MOUNT_MARGIN = '1000px'
/** How far from it a card is unmounted: further, so a card at the edge does not come and go by turns. */
export const UNMOUNT_MARGIN = '2500px'
/** The height of a card never measured, until any is. */
export const ESTIMATED_HEIGHT = 800

/**
 * The cards of the continuous view, mounted near the panel's viewport only.
 *
 * Every protocol has a slot, which keeps its place in the page: its card
 * mounted, or its height. With every card mounted, a report of a hundred
 * protocols made each plot drawn in it cost half a second in WebKitGTK,
 * against a sixth in the slideshow: Plotly lays the page out several times
 * per plot, and the page was a hundred cards long.
 *
 * A card unmounted leaves its height to its slot, so nothing moves. A card
 * mounting where only an estimate stood changes the slot's height: above the
 * viewport, the panel is scrolled by as much, so that what is read stays
 * still — by hand, since WebKit's own scroll anchoring cannot be relied on.
 *
 * `active()` false (the slideshow, printing) mounts every slot's card.
 */
export function useCardWindow({ container, active }) {
  const mounted = reactive(new Set())
  /** The last height each card had, mounted. */
  const heights = new Map()
  /** Each slot's height as last seen, to tell by how much it changed. */
  const sizes = new WeakMap()
  const slots = new Map()
  const idOf = new WeakMap()
  const refs = new Map()
  let observers = null

  function averageHeight() {
    if (!heights.size) return ESTIMATED_HEIGHT
    let total = 0
    for (const height of heights.values()) total += height
    return Math.round(total / heights.size)
  }

  function createObservers() {
    const root = container.value
    if (!root || typeof IntersectionObserver === 'undefined') return null
    const near = new IntersectionObserver(entries => {
      for (const entry of entries) {
        const id = idOf.get(entry.target)
        if (id !== undefined && entry.isIntersecting) mounted.add(id)
      }
    }, { root, rootMargin: MOUNT_MARGIN })
    const far = new IntersectionObserver(entries => {
      for (const entry of entries) {
        const id = idOf.get(entry.target)
        if (id === undefined || entry.isIntersecting || !mounted.has(id)) continue
        const height = entry.target.offsetHeight
        if (height) heights.set(id, height)
        mounted.delete(id)
      }
    }, { root, rootMargin: UNMOUNT_MARGIN })
    const resize = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(entries => {
      const panel = container.value
      const top = panel?.getBoundingClientRect().top
      for (const entry of entries) {
        const slot = entry.target
        const height = slot.offsetHeight
        const before = sizes.get(slot)
        sizes.set(slot, height)
        const id = idOf.get(slot)
        if (id !== undefined && mounted.has(id) && height) heights.set(id, height)
        // A slot wholly above the viewport moved what is read: it is put back.
        // One across its top may grow below the reader as well as above: left alone.
        if (active() && panel && before !== undefined && height !== before && slot.getBoundingClientRect().top + before <= top) {
          panel.scrollTop += height - before
        }
      }
    })
    return { near, far, resize }
  }

  function observe(element) {
    observers ||= createObservers()
    if (!observers) return
    observers.near.observe(element)
    observers.far.observe(element)
    observers.resize?.observe(element)
  }

  function unobserve(element) {
    observers?.near.unobserve(element)
    observers?.far.unobserve(element)
    observers?.resize?.unobserve(element)
  }

  /** The slot's ref: one function per protocol, so that Vue does not call it again on every update. */
  function slotRef(id) {
    if (!refs.has(id)) {
      refs.set(id, element => {
        const previous = slots.get(id)
        if (element === previous) return
        if (previous) {
          unobserve(previous)
          slots.delete(id)
        }
        if (!element) {
          refs.delete(id)
          mounted.delete(id)
          return
        }
        slots.set(id, element)
        idOf.set(element, id)
        observe(element)
      })
    }
    return refs.get(id)
  }

  /** Whether the card is mounted; everywhere, outside the continuous view or without observers. */
  function isMounted(id) {
    if (!active()) return true
    if (typeof IntersectionObserver === 'undefined') return true
    return mounted.has(id)
  }

  /** The slot's style: none with its card, the card's height without. */
  function slotStyle(id) {
    if (isMounted(id)) return null
    return { height: `${heights.get(id) ?? averageHeight()}px` }
  }

  onScopeDispose(() => {
    observers?.near.disconnect()
    observers?.far.disconnect()
    observers?.resize?.disconnect()
    observers = null
  })

  return { slotRef, isMounted, slotStyle, mounted, heights }
}
