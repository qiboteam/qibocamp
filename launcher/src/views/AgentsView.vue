<template>
  <div class="max-w-4xl mx-auto px-5 py-8 space-y-5">
    <div class="flex items-start gap-4">
      <div class="min-w-0">
        <h1 class="text-xl font-semibold text-gray-900 tracking-tight">Agents</h1>
        <p class="mt-1 text-xs text-gray-500">
          The configurations of the <a href="https://github.com/qiboteam/qibocal-mcp-servers" class="text-[#833dff] hover:text-[#3b107e]" @click.prevent="openExternal('https://github.com/qiboteam/qibocal-mcp-servers')">Qibocal MCP servers</a>:
          for an assistant on this machine, in <code class="lx-mono">{{ state.agents.path || 'agents.json' }}</code>, or for one on another
          machine, kept here and pushed there. A configuration also dispatches autonomous agents.
        </p>
      </div>
      <div class="ml-auto shrink-0 flex flex-col items-end gap-1.5">
        <button
          type="button"
          class="lx-primary"
          :class="{ 'is-open': creating }"
          :aria-expanded="creating"
          :title="creating ? 'Close the form' : 'New configuration'"
          @click="creating = !creating"
        >
          <component :is="creating ? X : Plus" class="w-3.5 h-3.5" /> {{ creating ? 'Close' : 'New configuration' }}
        </button>
        <!-- What the configurations share: the tools, the MCP registration, the remotes. -->
        <router-link :to="{ name: 'agents-setup' }" class="lx-action" title="qibocal-agent, the MCP registration and the remotes">
          <Settings class="w-3.5 h-3.5" /> Setup
        </router-link>
      </div>
    </div>

    <section v-if="creating" class="bm-card p-5">
      <h2 class="text-sm font-semibold text-gray-900 mb-4">New configuration</h2>
      <agent-form :form="form" :remotes="remotes" :names="takenNames(form.host === 'local' ? 'local' : form.hostName.trim())" />
      <div class="flex flex-wrap items-center gap-2.5 mt-5">
        <button type="button" class="lx-primary" :disabled="!formValid(form) || saving" @click="create">
          <Bookmark class="w-3.5 h-3.5" /> {{ form.host === 'local' ? 'Save' : 'Save and push' }}
        </button>
        <span class="lx-help !mt-0">{{ form.host === 'local' ? 'Written to agents.json, which qibocal-agent reads.' : `Kept here, and written to agents.json on ${form.hostName || 'that machine'}.` }}</span>
      </div>
    </section>

    <section class="space-y-2.5">
      <div class="flex flex-wrap items-center gap-2 px-1">
        <h2 class="text-sm font-semibold text-gray-900">Configurations</h2>
        <span class="text-xs text-gray-400">{{ shown.length }}</span>
        <button
          type="button"
          class="ml-auto text-xs text-gray-500 hover:text-gray-900 inline-flex items-center gap-1"
          @click="refreshAgents"
        >
          <RefreshCw class="w-3 h-3" />
          Refresh
        </button>
      </div>

      <!-- Two filters, together, on one line: chips while they are few, dropdowns past that. -->
      <div v-if="configurations.length" class="flex flex-wrap items-center gap-x-4 gap-y-1.5 px-1">
        <div v-for="filter in filterRows" :key="filter.key" class="flex items-center gap-1.5">
          <span class="text-[11px] text-gray-400 shrink-0">{{ filter.label }}</span>
          <select v-if="compactFilters" v-model="filters[filter.key]" class="lx-select !py-0.5 text-xs" :aria-label="filter.label">
            <option v-for="option in filter.options" :key="option.value" :value="option.value">{{ option.label }}</option>
          </select>
          <template v-else>
            <button v-for="option in filter.options" :key="option.value" type="button" class="lx-chip" :class="{ 'is-on': filters[filter.key] === option.value }" @click="filters[filter.key] = option.value">{{ option.label }}</button>
          </template>
        </div>
      </div>

      <p v-if="!shown.length" class="bm-card p-5 text-xs text-gray-500">
        No configuration {{ configurations.length ? 'among these' : 'yet' }}. <em>New configuration</em> adds one, for this machine or another.
      </p>

      <article
        v-for="(item, index) in shown"
        :key="item.key"
        class="bm-card p-4"
        :class="{ 'lx-focus': focused(index) }"
      >
        <!-- One row: the title gives way, the actions stay beside it. -->
        <div class="flex items-center gap-3">
          <div class="min-w-0 flex-1">
            <p class="text-sm font-medium text-gray-900 truncate">
              {{ item.name }}
              <span class="ml-1.5 text-[10px] uppercase tracking-wide font-semibold" :class="item.host === 'local' ? 'text-gray-400' : 'text-[#833dff]'" :title="item.host === 'local' ? 'Kept for this machine' : `Kept for the machine ${item.host}, pushed to ${item.destination}`">{{ item.host === 'local' ? 'this machine' : `for ${item.host}` }}</span>
              <span class="ml-1 text-[10px] uppercase tracking-wide font-semibold text-gray-400" :title="reachedRemote(item.entry, remotes) ? 'Its calibration server starts there, over SSH' : 'Its calibration server starts where it runs'">{{ reachedRemote(item.entry, remotes) ? `via ${reachedRemote(item.entry, remotes)}` : 'direct' }}</span>
            </p>
            <p class="lx-mono text-gray-500 truncate">{{ describeAgent(item.entry) }}</p>
          </div>
          <router-link
            v-if="runningOf(item)"
            :to="{ name: 'processes', query: { process: runningOf(item).id } }"
            class="shrink-0 px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-700 text-[11px] font-medium hover:bg-emerald-100 transition"
          >running</router-link>

          <div class="shrink-0 flex items-center gap-1.5">
            <button v-if="item.host === 'local'" type="button" class="lx-primary !py-1" title="Dispatch an autonomous agent with this configuration" @click="openRun(item)">
              <Play class="w-3.5 h-3.5" /> Run
            </button>
            <button v-else type="button" class="lx-primary !py-1" :disabled="pushing === item.key" :title="`Write it to agents.json on ${item.host} (${item.destination}): the configuration only, not environments`" @click="push(item)">
              <Upload class="w-3.5 h-3.5" /> {{ pushing === item.key ? 'Pushing…' : 'Push' }}
            </button>
            <button type="button" class="lx-action" :disabled="startingReports === item.key" title="Start a report server on the execution host, serving its output, and open it in the dashboard" @click="reports(item)">
              <LayoutDashboard class="w-3.5 h-3.5" /> Reports
            </button>
            <button type="button" class="lx-action" title="Edit it" @click="toggleEdit(item)">
              <Pencil class="w-3.5 h-3.5" /> Edit
            </button>
            <button type="button" class="lx-action" title="The form for a new configuration, with these settings" @click="duplicate(item)">
              <Files class="w-3.5 h-3.5" /> Duplicate
            </button>
            <button type="button" class="lx-action" title="Remove it" @click="remove(item)">
              <Trash2 class="w-3.5 h-3.5" />
            </button>
          </div>
        </div>

        <form v-if="editing.key === item.key" class="mt-3.5 pt-3.5 border-t border-gray-100" @submit.prevent="saveEdit(item)">
          <agent-form :form="editing.form" :remotes="remotes" :names="takenNames(editing.form.host === 'local' ? 'local' : editing.form.hostName.trim(), item)" />
          <div class="flex flex-wrap items-center gap-2 mt-4">
            <button type="submit" class="lx-primary" :disabled="editing.busy || !formValid(editing.form, item)">{{ editing.form.host === 'local' ? 'Save' : 'Save and push' }}</button>
            <button type="button" class="lx-action" @click="editing.key = null">Cancel</button>
          </div>
        </form>
      </article>
    </section>

    <!-- The prompts kept for autonomous runs: what to ask, apart from where. -->
    <section class="space-y-2.5">
      <div class="flex items-center gap-2 px-1">
        <h2 class="text-sm font-semibold text-gray-900">Saved prompts</h2>
        <span class="text-xs text-gray-400">{{ prompts.length }}</span>
        <button type="button" class="ml-auto lx-action !py-0.5" :class="{ 'is-open': addingPrompt }" :title="addingPrompt ? 'Close' : 'Save a prompt, without running it'" @click="addingPrompt = !addingPrompt">
          <component :is="addingPrompt ? X : Plus" class="w-3.5 h-3.5" />
        </button>
      </div>
      <form v-if="addingPrompt" class="bm-card p-4 space-y-2" @submit.prevent="addPrompt">
        <input v-model.trim="newPrompt.name" type="text" class="lx-input" placeholder="Name" aria-label="Name of the prompt" />
        <textarea v-model="newPrompt.text" rows="5" class="lx-input" placeholder="Calibrate qubit 0's drive frequency using spectroscopy. Sweep no wider than 20 MHz… Stop on errors." aria-label="The prompt" />
        <div class="flex items-center gap-2">
          <button type="submit" class="lx-primary" :disabled="!newPrompt.name || !newPrompt.text.trim()"><Bookmark class="w-3.5 h-3.5" /> Save</button>
          <span class="lx-help !mt-0">{{ prompts.some(prompt => prompt.name === newPrompt.name) ? `Replaces the prompt ${newPrompt.name}.` : 'Offered by Run; a run\'s prompt can also be saved from Running.' }}</span>
        </div>
      </form>
      <p v-if="!prompts.length && !addingPrompt" class="bm-card p-5 text-xs text-gray-500">No prompt saved yet: <strong>+</strong> saves one, and so does Running, from an agent's run.</p>
      <article v-for="(prompt, index) in prompts" :key="prompt.name" class="bm-card p-4 flex items-start gap-3">
        <div class="min-w-0 flex-1">
          <p class="text-sm font-medium text-gray-900">{{ prompt.name }}</p>
          <p class="text-xs text-gray-500 whitespace-pre-wrap line-clamp-3">{{ prompt.text }}</p>
        </div>
        <button type="button" class="lx-action shrink-0" title="Forget this prompt" @click="forgetPrompt(index)"><Trash2 class="w-3.5 h-3.5" /></button>
      </article>
    </section>

    <!-- An autonomous run: the configuration, and the prompt. -->
    <div v-if="run.item" class="lx-veil" @pointerdown.self="run.item = null">
      <form class="lx-dialog lx-dialog-sheet" @submit.prevent="dispatch">
        <h2 class="text-sm font-semibold text-gray-900 shrink-0">Run an agent: {{ run.item.name }}</h2>
        <div class="lx-dialog-scroll mt-3 space-y-3">
          <p class="text-xs text-gray-500">
            <code class="lx-mono">qibocal-agent</code> plans, runs one protocol at a time, inspects the plots and accepts or rejects
            each update. State the target, approved hardware limits, a budget and when to stop. It is
            <strong>not a hardware safety layer</strong>.
          </p>
          <div v-if="prompts.length" class="flex flex-wrap gap-1.5">
            <button v-for="prompt in prompts" :key="prompt.name" type="button" class="lx-chip" @click="run.objective = prompt.text">{{ prompt.name }}</button>
          </div>
          <textarea
            v-model="run.objective"
            rows="7"
            class="lx-input"
            :placeholder="run.item.entry.objective ? `Empty: the saved objective — ${run.item.entry.objective}` : 'Calibrate qubit 0\'s drive frequency using spectroscopy. Sweep no wider than 20 MHz… Stop on errors or insufficient evidence.'"
          />
          <p class="lx-help !mt-0">The model's credentials come from your shell profile; the launcher keeps none.</p>
        </div>
        <div class="mt-3 pt-3 border-t border-gray-100 flex items-center justify-end gap-2 shrink-0">
          <button type="button" class="lx-action" @click="run.item = null">Cancel</button>
          <button type="submit" class="lx-primary" :disabled="run.busy || (!run.objective.trim() && !run.item.entry.objective) || !run.item.entry.model">
            <Play class="w-3.5 h-3.5" /> {{ run.item.entry.model ? (run.busy ? 'Starting…' : 'Run') : 'Set a model first' }}
          </button>
        </div>
      </form>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Bookmark, Files, LayoutDashboard, Pencil, Play, Plus, RefreshCw, Settings, Trash2, Upload, X } from 'lucide-vue-next'
import AgentForm from '../components/AgentForm.vue'
import { desktop, openExternal } from '../bridge.js'
import {
  state, attempt, fail, notebookReportsServer, notify, refreshAgents,
  savePrompt, savedPrompts, startAgent, startNotebookReports
} from '../state.js'
import { usePageKeys } from '../composables/pageKeys.js'
import {
  agentConfigurations,
  agentEditorValues,
  agentEntry,
  agentNameError,
  agentRemotes,
  agentReports,
  agentReportsKey,
  agentShown,
  describeAgent,
  reachedRemote,
  withHostAgent,
  withLocalAgent
} from '../lib/agents.js'

const router = useRouter()

/** A blank form: where it is kept, its name, and its settings. */
const blank = () => ({ host: 'local', hostName: '', name: '', ...agentEditorValues() })

const creating = ref(false)
const saving = ref(false)
const form = reactive(blank())
const editing = reactive({ key: null, form: blank(), busy: false })
const filters = reactive({ kept: 'all', reaches: 'all' })
const pushing = ref(null)
const startingReports = ref(null)
const run = reactive({ item: null, objective: '', busy: false })
const addingPrompt = ref(false)
const newPrompt = reactive({ name: '', text: '' })

const configurations = computed(() => agentConfigurations(state.agents.document, state.agentHosts, state.machines))
const remotes = computed(() => agentRemotes(state.machines, state.agentHosts))
const keptFilters = computed(() => [
  { value: 'all', label: 'All' },
  { value: 'local', label: 'This machine' },
  ...remotes.value.map(remote => ({ value: remote.name, label: remote.name }))
])
const reachFilters = computed(() => {
  // The registered remotes, and any destination typed without one.
  const reached = configurations.value.map(item => reachedRemote(item.entry, remotes.value)).filter(Boolean)
  const names = [...new Set([...remotes.value.map(remote => remote.name), ...reached])]
  return [{ value: 'all', label: 'All' }, { value: 'direct', label: 'Directly' }, ...names.map(name => ({ value: name, label: name }))]
})
const shown = computed(() => configurations.value.filter(item => agentShown(item, filters, remotes.value)))
const prompts = computed(savedPrompts)
const filterRows = computed(() => [
  { key: 'kept', label: 'Kept for', options: keptFilters.value },
  { key: 'reaches', label: 'Reaches', options: reachFilters.value }
])
/** Chips while both filters fit a line; dropdowns once the remotes make them long. */
const compactFilters = computed(() => keptFilters.value.length + reachFilters.value.length > 8)

/** Names already used where a form keeps its configuration, but its own. */
function takenNames(host, own = null) {
  return configurations.value
    .filter(item => item !== own && item.host === host)
    .map(item => item.name)
    .filter(Boolean)
}

function formValid(values, own = null) {
  if (values.host !== 'local' && !remotes.value.some(remote => remote.name === values.hostName)) return false
  if (agentNameError(values.name)) return false
  const host = values.host === 'local' ? 'local' : values.hostName.trim()
  const clash = configurations.value.some(item => item !== own && item.host === host && item.name === values.name)
  return !clash && agentEntry(values).valid
}

const { focused } = usePageKeys({
  page: 'agents',
  count: () => shown.value.length,
  actions: {
    Enter: index => { const item = shown.value[index]; if (item) item.host === 'local' ? openRun(item) : push(item) },
    e: index => toggleEdit(shown.value[index]),
    d: index => duplicate(shown.value[index])
  }
})

/**
 * Write a configuration where it is kept — and push it, when that is another
 * machine — and say so. True once kept: a push that failed leaves it kept here,
 * and says that too.
 */
async function store(values, previous = null) {
  const host = values.host === 'local' ? 'local' : values.hostName.trim()
  const name = values.name
  const { entry } = agentEntry(values, { previous: previous?.entry || {} })
  // Moved or renamed: the old one goes first.
  if (previous && (previous.host !== host || previous.name !== name)) await drop(previous)
  if (host === 'local') {
    const written = await desktop.writeAgents(withLocalAgent(state.agents.document, name, entry))
    state.agents = written
    notify(`Configuration ${name} saved.`, 'agents')
  } else {
    state.agentHosts = await desktop.saveAgentHosts(withHostAgent(state.agentHosts, host, name, entry))
    await pushOne(host, name, entry)
  }
  return true
}

/** Where a machine's configurations are pushed: its SSH destination. */
function destinationOf(host) {
  return remotes.value.find(remote => remote.name === host)?.ssh || host
}

async function drop(item) {
  if (item.host === 'local') {
    state.agents = await desktop.writeAgents(withLocalAgent(state.agents.document, item.name, null))
  } else {
    state.agentHosts = await desktop.saveAgentHosts(withHostAgent(state.agentHosts, item.host, item.name, null))
    await desktop.pushAgents(destinationOf(item.host), {}, [item.name]).catch(error => fail(`Not removed from ${item.host}: ${error?.message || error}`, 'agents'))
  }
}

async function pushOne(host, name, entry) {
  try {
    await desktop.pushAgents(destinationOf(host), { [name]: entry }, [])
    notify(`${name} written to agents.json on ${host}.`, 'agents')
  } catch (error) {
    fail(`${name} is kept here, but not pushed to ${host}: ${error?.message || error}. Push it again when the host is reachable.`, 'agents')
  }
}

async function create() {
  saving.value = true
  const done = await attempt(() => store(form), null, 'agents')
  saving.value = false
  if (done === undefined) return
  creating.value = false
  Object.assign(form, blank())
}

function toggleEdit(item) {
  if (!item) return
  if (editing.key === item.key) {
    editing.key = null
    return
  }
  Object.assign(editing.form, blank(), agentEditorValues(item.entry), {
    host: item.host === 'local' ? 'local' : 'remote',
    hostName: item.host === 'local' ? '' : item.host,
    name: item.name
  })
  Object.assign(editing, { key: item.key, busy: false })
}

async function saveEdit(item) {
  editing.busy = true
  const done = await attempt(() => store(editing.form, item), null, 'agents')
  editing.busy = false
  if (done !== undefined) editing.key = null
}

function duplicate(item) {
  if (!item) return
  Object.assign(form, blank(), agentEditorValues(item.entry), {
    host: item.host === 'local' ? 'local' : 'remote',
    hostName: item.host === 'local' ? '' : item.host,
    name: `${item.name}-copy`
  })
  creating.value = true
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

async function remove(item) {
  await attempt(() => drop(item), `${item.name} removed.`, 'agents')
  if (editing.key === item.key) editing.key = null
}

async function push(item) {
  pushing.value = item.key
  await pushOne(item.host, item.name, item.entry)
  pushing.value = null
}

/** Its reports: a server on the execution host, serving its output. */
async function reports(item) {
  startingReports.value = item.key
  const ssh = item.host === 'local' ? item.entry.ssh || null : item.destination
  const options = { ssh, reports: agentReports(item.entry, item.name) }
  const key = agentReportsKey(item.name, item.host)
  const running = notebookReportsServer(key)
  const started = await attempt(
    () => startNotebookReports(options, `${item.name} agent`, { key }),
    running ? null : 'Starting the report server of the agent; it appears in the dashboard once it answers.',
    'processes'
  )
  startingReports.value = null
  if (started && !running) router.push({ name: 'processes' })
}

/** The run of this configuration, while it lasts. */
function runningOf(item) {
  return state.processes.find(process =>
    process.kind === 'agent' && (process.status === 'running' || process.status === 'stopping') &&
    state.launches[process.id]?.options?.name === item.name && item.host === 'local') || null
}

function openRun(item) {
  Object.assign(run, { item, objective: '', busy: false })
}

async function dispatch() {
  run.busy = true
  const objective = run.objective.trim()
  const started = await attempt(
    () => startAgent({ name: run.item.name, objective: objective || null }),
    `Agent ${run.item.name} dispatched; its output is in Running.`,
    'processes'
  )
  run.busy = false
  if (started) {
    run.item = null
    router.push({ name: 'processes', query: { process: started.id } })
  }
}

async function addPrompt() {
  const saved = await attempt(() => savePrompt(newPrompt.name, newPrompt.text.trim()), `Prompt ${newPrompt.name} saved.`, 'agents')
  if (saved === undefined) return
  Object.assign(newPrompt, { name: '', text: '' })
  addingPrompt.value = false
}

async function forgetPrompt(index) {
  await savePreferences({ agentPrompts: prompts.value.filter((_, position) => position !== index) })
}

/** Save the preferences with `changes`; `replace` writes them as given, to drop a key. */
async function savePreferences(changes, { replace = false } = {}) {
  const next = replace ? changes : { ...state.preferences, ...changes }
  const saved = await attempt(() => desktop.savePreferences(next), null, 'agents')
  if (saved !== undefined) state.preferences = saved
}

onMounted(() => refreshAgents())
</script>
