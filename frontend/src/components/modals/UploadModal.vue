<!--
  Upload reports to another server, as `qq upload` does, between two report
  servers: the reports are downloaded from the one they are on — the same
  zip as Download — and sent to the one chosen. Any registered server can be
  chosen, signed in as on it; a server known only by its address, only when
  it accepts uploads without signing in.
-->
<template>
  <div
    v-if="show"
    class="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm p-4"
    @click.self="!busy && $emit('close')"
  >
    <div class="bg-white rounded-2xl max-w-lg w-full p-5 shadow-2xl border border-black/[0.08] flex flex-col max-h-[85vh] overflow-hidden">
      <div class="flex items-start justify-between gap-3 mb-3">
        <div class="min-w-0">
          <h2 class="text-sm font-semibold text-gray-900">
            Upload {{ reportIds.length === 1 ? 'the report' : `${reportIds.length} reports` }} to another server
          </h2>
          <p class="text-xs text-gray-500 mt-0.5">Each report lands at the top of the server's reports, under its folder's name; one already there is left as it is.</p>
        </div>
        <button type="button" class="border-0 w-6 h-6 flex items-center justify-center rounded-lg text-gray-400 hover:text-gray-700 hover:bg-gray-100 transition cursor-pointer" aria-label="Close" :disabled="busy" @click="$emit('close')">&times;</button>
      </div>

      <div class="min-h-0 flex-1 overflow-y-auto space-y-3">
        <!-- The registered servers, each with what it says of uploads. -->
        <div v-if="destinations.length" class="rounded-xl border border-gray-100 divide-y divide-gray-50">
          <label
            v-for="server in destinations"
            :key="server.id || server.url"
            class="flex items-center gap-2.5 px-3 py-2 text-xs"
            :class="answer(server).state === 'ready' ? 'cursor-pointer hover:bg-purple-50/60' : 'opacity-70'"
          >
            <input
              type="radio"
              name="upload-destination"
              class="accent-[#833dff]"
              :disabled="answer(server).state !== 'ready' || busy"
              :checked="chosen === key(server)"
              @change="chosen = key(server)"
            />
            <span class="min-w-0 flex-1">
              <span class="block font-medium text-gray-900 truncate">{{ server.name || server.url }}</span>
              <span class="block font-mono text-[11px] text-gray-400 truncate">{{ server.url }}</span>
            </span>
            <span class="shrink-0 text-[11px]" :class="tone(answer(server).state)">{{ answer(server).text }}</span>
          </label>
        </div>
        <p v-else class="text-xs text-gray-500">No other server is registered: give an address below.</p>

        <!-- A server known only by its address: only if it takes uploads without signing in. -->
        <div class="rounded-xl bg-gray-50 px-3 py-2.5">
          <p class="text-[11px] font-semibold uppercase tracking-wider text-gray-400 mb-1.5">By address</p>
          <form class="flex items-center gap-2" @submit.prevent="checkAddress">
            <input
              v-model.trim="address"
              type="text"
              class="flex-1 min-w-0 px-2.5 py-1.5 rounded-lg border border-gray-200 bg-white text-xs font-mono focus:outline-none focus:ring-2 focus:ring-[#833dff]/40"
              placeholder="http://host:8000"
              aria-label="Address of a server"
              spellcheck="false" autocomplete="off" autocorrect="off" autocapitalize="off"
              :disabled="busy"
            />
            <button type="submit" class="px-2.5 py-1.5 rounded-lg text-xs font-semibold bg-white border border-gray-200 hover:border-purple-300 cursor-pointer" :disabled="!address || busy">Check</button>
          </form>
          <label v-if="byAddress" class="mt-2 flex items-center gap-2.5 text-xs" :class="byAddress.state === 'ready' ? 'cursor-pointer' : 'opacity-70'">
            <input
              type="radio"
              name="upload-destination"
              class="accent-[#833dff]"
              :disabled="byAddress.state !== 'ready' || busy"
              :checked="chosen === byAddress.url"
              @change="chosen = byAddress.url"
            />
            <span class="font-mono truncate flex-1">{{ byAddress.url }}</span>
            <span class="shrink-0 text-[11px]" :class="tone(byAddress.state)">{{ byAddress.text }}</span>
          </label>
        </div>

        <!-- How each report went. -->
        <ul v-if="results.length" class="space-y-1 text-xs">
          <li v-for="result in results" :key="result.id" class="flex items-start gap-2">
            <span class="w-4 shrink-0 text-center" :class="tone(result.state)">{{ result.state === 'ready' ? '✓' : result.state === 'working' ? '…' : '✗' }}</span>
            <span class="font-mono truncate min-w-0 flex-1" :title="result.id">{{ result.id }}</span>
            <span class="shrink-0 text-[11px] text-gray-500">{{ result.text }}</span>
          </li>
        </ul>
      </div>

      <div class="flex items-center justify-end gap-2 mt-4 text-xs">
        <button type="button" class="px-3 py-1.5 rounded-lg font-medium text-gray-600 hover:bg-gray-100 cursor-pointer" :disabled="busy" @click="$emit('close')">
          {{ finished ? 'Close' : 'Cancel' }}
        </button>
        <button
          type="button"
          class="px-3 py-1.5 rounded-lg font-semibold bm-btn-primary cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
          :disabled="!destination || busy || !reportIds.length"
          @click="upload"
        >{{ busy ? 'Uploading…' : 'Upload' }}</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { state, getActiveAuthToken } from '../../store.js'
import { apiFetch, apiUploadCapability, apiUploadReport } from '../../api.js'
import { normalizeUrl } from '../../utils/url.js'
import { readBlob } from '../../utils/apiFiles.js'
import { uploadDestinations, uploadFailure, uploadState } from '../../utils/uploads.js'

const props = defineProps({
  show: { type: Boolean, default: false },
  /** The reports to send, by id on the server they are on. */
  reportIds: { type: Array, default: () => [] },
  /** The server they are on: the active one, unless said otherwise. */
  source: { type: Object, default: null }
})
const emit = defineEmits(['close', 'uploaded'])

const from = computed(() => props.source || state.activeServer)
const destinations = computed(() => uploadDestinations(state.servers, from.value))
const answers = reactive({})
const chosen = ref(null)
const address = ref('')
const byAddress = ref(null)
const busy = ref(false)
const results = ref([])
const finished = ref(false)

const key = server => normalizeUrl(server.url)
const answer = server => answers[key(server)] || { state: 'checking', text: 'Asking…' }

function tone(kind) {
  return { ready: 'text-emerald-700', working: 'text-gray-400', checking: 'text-gray-400', 'sign-in': 'text-amber-700' }[kind] || 'text-red-600'
}

async function ask(server) {
  try {
    const response = await apiUploadCapability(server)
    return response.ok ? await response.json() : null
  } catch {
    return null
  }
}

/** What each registered server says of uploads, signed in as on it. */
async function askAll() {
  for (const server of destinations.value) {
    const capability = await ask(server)
    answers[key(server)] = uploadState(capability, { registered: true, signedIn: Boolean(getActiveAuthToken(server)) })
  }
}

async function checkAddress() {
  const url = normalizeUrl(address.value)
  if (!url) return
  const registered = destinations.value.find(server => key(server) === url)
  if (registered) {
    chosen.value = answer(registered).state === 'ready' ? url : chosen.value
    byAddress.value = null
    return
  }
  byAddress.value = { url, state: 'checking', text: 'Asking…' }
  // Unregistered: asked without signing in, and taken only if that is enough.
  const capability = await ask({ url })
  byAddress.value = { url, ...uploadState(capability, { registered: false }) }
  if (byAddress.value.state === 'ready') chosen.value = url
}

/** The server chosen: a registered one, or the one given by address. */
const destination = computed(() => {
  if (!chosen.value) return null
  return destinations.value.find(server => key(server) === chosen.value) ||
    (byAddress.value?.url === chosen.value && byAddress.value.state === 'ready' ? { url: byAddress.value.url } : null)
})

async function upload() {
  const target = destination.value
  if (!target) return
  busy.value = true
  finished.value = false
  results.value = props.reportIds.map(id => ({ id, state: 'working', text: 'Waiting…' }))
  let sent = 0
  for (const result of results.value) {
    try {
      result.text = 'Downloading…'
      const download = await apiFetch(`/api/reports/${encodeURIComponent(result.id)}/download/full`, {}, from.value)
      if (!download.ok) throw new Error(`The report could not be downloaded (${download.status}).`)
      const archive = await readBlob(download)
      result.text = 'Uploading…'
      const response = await apiUploadReport(archive, target)
      if (!response.ok) {
        const body = await response.json().catch(() => ({}))
        throw new Error(uploadFailure(response.status, body.detail))
      }
      const body = await response.json()
      Object.assign(result, { state: 'ready', text: `Uploaded as ${body.name}.` })
      sent += 1
    } catch (error) {
      Object.assign(result, { state: 'failed', text: error.message || 'The upload failed.' })
    }
  }
  busy.value = false
  finished.value = true
  if (sent) emit('uploaded', { count: sent, server: target })
}

watch(() => props.show, open => {
  if (!open) return
  Object.keys(answers).forEach(name => delete answers[name])
  chosen.value = null
  address.value = ''
  byAddress.value = null
  results.value = []
  finished.value = false
  askAll()
}, { immediate: true })
</script>
