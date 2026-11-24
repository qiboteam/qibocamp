import { onBeforeUnmount, onMounted, watch } from 'vue'
import { scrollRoot } from '../utils/plotDrawing.js'

/** How far ahead of the viewport "near" begins. */
const MARGIN = '1200px'

/**
 * Call `onNear` when an element comes within reach of the viewport — once
 * per approach, and again whenever `again()` changes to something truthy,
 * for an element whose content was replaced. Without IntersectionObserver
 * the element counts as near at once.
 */
export function useNearViewport(target, onNear) {
  let observer = null

  function observe() {
    const element = target.value
    if (!element) return
    if (typeof IntersectionObserver === 'undefined') {
      onNear()
      return
    }
    // Measured from the container the element scrolls in: from the viewport, the margin stops at its edge.
    observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) onNear()
    }, { root: scrollRoot(element), rootMargin: MARGIN })
    observer.observe(element)
  }

  onMounted(observe)
  // An element rendered later than the component — behind a v-if — is watched when it appears.
  watch(target, (element, previous) => {
    if (element === previous) return
    observer?.disconnect()
    observer = null
    if (element) observe()
  })
  onBeforeUnmount(() => {
    observer?.disconnect()
    observer = null
  })
}
