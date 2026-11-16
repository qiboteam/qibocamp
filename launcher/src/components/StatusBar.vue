<!--
  The status bar: the page's own colour, no edge, always on top, so it
  blends into a page that fits the window and sits over one that does
  not. The command line's version on the left; on the right, the latest
  thing said, fading by itself, and the count of everything said, which
  opens the list. An installation in progress sits between the two for
  as long as it runs, with the way to its output.

  It is the same bar on the launcher page and on the dashboard's server
  list: the links are plain addresses, good from either page.
-->
<template>
  <footer class="lx-status" role="status" aria-live="polite">
    <div class="px-5 h-full flex items-center gap-5">
      <a :href="launcherHref({ name: 'settings' })" class="inline-flex items-center gap-2 truncate shrink-0 max-w-[40%]" :title="versionTitle">
        <span class="lx-dot" :class="dotClass" />
        <span class="truncate">{{ versionLabel }}</span>
      </a>

      <a
        v-if="installing"
        :href="launcherHref({ name: 'processes' })"
        class="inline-flex items-center gap-1.5 shrink-0"
        title="Follow the installation in Running"
      >
        <Loader2 class="w-3 h-3 lx-spin" />
        Installing qibocamp… follow it in Running
      </a>

      <div class="ml-auto flex items-center gap-2 min-w-0">
        <!-- The latest message, leading to the section it is about when it has one. -->
        <component
          :is="state.message?.link ? 'a' : 'span'"
          v-if="state.message"
          :href="state.message.link ? launcherHref(state.message.link) : undefined"
          class="truncate"
          :class="state.message.kind === 'error' ? 'lx-status-error' : ''"
          :title="state.message.text"
        >{{ state.message.text }}</component>
        <notification-bell />
      </div>
    </div>
  </footer>
</template>

<script setup>
import { computed } from 'vue'
import { Loader2 } from 'lucide-vue-next'
import NotificationBell from './NotificationBell.vue'
import { state, hasQibocamp, installing } from '../state.js'
import { describeSpec } from '../lib/versions.js'
import { launcherHref } from '../lib/links.js'

/** What the command line says of itself, with the version the launcher put there. */
const versionLabel = computed(() => {
  const environment = state.environment
  if (!environment) return ''
  if (!hasQibocamp.value) {
    if (environment.bundled) return 'qibocamp missing from this package'
    return installing.value ? 'qibocamp: installing' : 'qibocamp not installed'
  }
  const version = String(environment.version || '').replace(/^qibocamp,?\s*version\s*/i, '').trim()
  const held = environment.source === 'bundled'
    ? 'bundled'
    : environment.source === 'managed' && environment.managedPackage
      ? describeSpec(environment.managedPackage, environment)
      : ''
  return `qibocamp ${version || ''}${held ? ` · ${held}` : ''}`.replace(/\s+/g, ' ').trim()
})
const versionTitle = computed(() => state.environment?.executable || 'Configuration')
/** Found, being installed, or missing: green, amber, red. */
const dotClass = computed(() =>
  hasQibocamp.value ? 'bg-emerald-500' : installing.value ? 'bg-amber-400' : 'bg-red-400'
)
</script>
