<template>
  <div class="max-w-4xl mx-auto px-5 py-8 space-y-5">
    <div class="flex items-start gap-4">
      <div class="min-w-0">
        <h1 class="text-xl font-semibold text-gray-900 tracking-tight">Servers</h1>
        <p class="mt-1 text-xs text-gray-500">
          A report server runs here, on another machine over SSH, or is already running somewhere you can reach.
        </p>
      </div>
      <!-- The form for a new server opens from here; the page starts on the servers it has. -->
      <button
        type="button"
        class="ml-auto shrink-0 lx-primary"
        :class="{ 'is-open': creating }"
        :aria-expanded="creating"
        :title="creating ? 'Close the form' : 'New server'"
        @click="creating = !creating"
      >
        <component :is="creating ? X : Plus" class="w-3.5 h-3.5" /> {{ creating ? 'Close' : 'New server' }}
      </button>
    </div>

    <!--
      One form for every new server. Where it runs decides which fields make
      sense; the few that are required say so, the rest sit behind "More
      options" with their defaults; starting for a look is the default,
      registering a choice.
    -->
    <section v-if="creating" class="bm-card p-5">
      <h2 class="text-sm font-semibold text-gray-900 mb-4">New server</h2>
      <server-fields v-model:where="where" :form="form" :errors="errors" />

      <div class="flex flex-wrap items-center gap-2.5 mt-5">
        <template v-if="where === 'reachable'">
          <button type="button" class="lx-primary" :disabled="!form.url" @click="register">
            Register
          </button>
          <span class="lx-help !mt-0">Nothing is started: the address is added to the servers the dashboard knows.</span>
        </template>
        <template v-else>
          <!-- Registering is the rule; starting for a look, the exception. -->
          <button
            type="button"
            class="lx-primary"
            :disabled="starting"
            title="Start it, and keep it in servers.json for next time"
            @click="launch(true)"
          >
            <Bookmark class="w-3.5 h-3.5" /> {{ starting ? 'Starting…' : 'Start and register' }}
          </button>
          <button
            type="button"
            class="lx-action"
            :disabled="starting"
            title="Start it for a look, without touching servers.json"
            @click="launch(false)"
          >
            <Play class="w-3.5 h-3.5" /> Start
          </button>
          <span class="lx-mono text-gray-400 truncate">{{ preview }}</span>
        </template>
      </div>
    </section>

    <!-- Started for a look: around only while they run -->
    <section v-if="temporary.length" class="space-y-2.5">
      <div class="flex items-center gap-2 px-1">
        <h2 class="text-sm font-semibold text-gray-900">Running, not registered</h2>
        <span class="text-xs text-gray-400">{{ temporary.length }}</span>
      </div>
      <article v-for="process in temporary" :key="process.id" class="bm-card p-4 flex flex-wrap items-center gap-x-3 gap-y-1">
        <div class="min-w-0">
          <p class="text-sm font-medium text-gray-900 truncate">{{ process.label }}</p>
          <p class="lx-mono text-gray-500 truncate">{{ process.url }}</p>
        </div>
        <div class="ml-auto flex flex-wrap items-center gap-1.5">
          <button
            type="button"
            class="lx-action"
            title="Open it in the dashboard, without registering it"
            @click="openDashboard(process.url, { newWindow: wantsNewWindow($event) })"
            @auxclick.prevent="$event.button === 1 && openDashboard(process.url, { newWindow: true })"
          >
            <LayoutDashboard class="w-3.5 h-3.5" /> Open
          </button>
          <button type="button" class="lx-action" title="Keep it in servers.json" @click="keep(process)">
            <Bookmark class="w-3.5 h-3.5" /> Register
          </button>
        </div>
      </article>
    </section>

    <!-- Registered servers -->
    <section class="space-y-2.5">
      <div class="flex items-center gap-2 px-1">
        <h2 class="text-sm font-semibold text-gray-900">Registered servers</h2>
        <span class="text-xs text-gray-400">{{ shownServers.length }}</span>
        <machine-filter v-model="machineFilter" class="ml-2" :destinations="state.servers.map(serverSsh)" />
        <button
          type="button"
          class="ml-auto text-xs text-gray-500 hover:text-gray-900 inline-flex items-center gap-1"
          @click="refreshServers"
        >
          <RefreshCw class="w-3 h-3" />
          Refresh
        </button>
      </div>

      <p v-if="!state.servers.length" class="bm-card p-5 text-xs text-gray-500">
        No server registered yet. Starting one with <em>Start and register</em>, or registering a reachable address, adds it here.
      </p>

      <article
        v-for="(server, index) in shownServers"
        :key="server.id"
        class="bm-card p-4"
        :class="{ 'lx-focus': focused(index) }"
      >
        <div class="flex flex-wrap items-center gap-x-3 gap-y-1">
          <div class="min-w-0">
            <p class="text-sm font-medium text-gray-900 truncate">{{ server.name || server.url }}</p>
            <p class="lx-mono text-gray-500 truncate">{{ server.url }}</p>
          </div>
          <!-- The pill leads to the card of this server in Running. -->
          <router-link
            v-if="processFor(server)"
            :to="{ name: 'processes', query: { process: processFor(server).id } }"
            class="px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-700 text-[11px] font-medium hover:bg-emerald-100 transition"
            title="See it in Running"
          >running</router-link>

          <div class="ml-auto flex flex-wrap items-center gap-1.5">
            <!-- Running: read it. Stopped: start it, as last time or at its address here. -->
            <button
              v-if="processFor(server)"
              type="button"
              class="lx-primary !py-1"
              title="Open it in the dashboard"
              @click="openDashboard(server.url, { newWindow: wantsNewWindow($event) })"
              @auxclick.prevent="$event.button === 1 && openDashboard(server.url, { newWindow: true })"
            >
              <LayoutDashboard class="w-3.5 h-3.5" /> Open
            </button>
            <button
              v-else
              type="button"
              class="lx-primary !py-1"
              :disabled="!launchOptionsFor(server) || starting"
              :title="launchOptionsFor(server)
                ? (recallLaunch(server.url) ? 'Start it again with the options used last time' : 'Start it here, at this address, serving the shared directory, with authentication')
                : 'Registered elsewhere: nothing to start on this machine'"
              @click="relaunch(server)"
            >
              <Play class="w-3.5 h-3.5" /> Start
            </button>
            <!-- Here, with the command line the application ships with: Nix updates it, not this. -->
            <button
              v-if="launchOptionsFor(server) && (launchOptionsFor(server).ssh || !state.environment?.bundled)"
              type="button"
              class="lx-action"
              :disabled="updating === server.id"
              :title="launchOptionsFor(server).ssh
                ? 'Bring qibocamp up to date in its environment on that machine, and start it again'
                : 'Update the launcher\'s own qibocamp, and start it again'"
              @click="update(server)"
            >
              <RefreshCw class="w-3.5 h-3.5" :class="{ 'lx-spin': updating === server.id }" /> {{ updating === server.id ? 'Updating…' : 'Update' }}
            </button>
            <button type="button" class="lx-action" title="Set an administrator's password, and sign the dashboard in as them" @click="adminServer = server">
              <KeyRound class="w-3.5 h-3.5" /> Admin access
            </button>
            <button type="button" class="lx-action" title="Edit how it is registered" @click="toggleEdit(server)">
              <Pencil class="w-3.5 h-3.5" /> Edit
            </button>
            <button
              type="button"
              class="lx-action"
              title="Open the form for a new server with this one's options, to change a few"
              @click="duplicate(server)"
            >
              <Files class="w-3.5 h-3.5" /> Duplicate
            </button>
            <button type="button" class="lx-action" title="Forget this server" @click="forget(server)">
              <Trash2 class="w-3.5 h-3.5" />
            </button>
          </div>
        </div>

        <!--
          The entry as servers.json holds it, and how it is started: the very
          form of the registration, opened on what is known of this server.
        -->
        <form v-if="editing.serverId === server.id" class="mt-3.5 pt-3.5 border-t border-gray-100" @submit.prevent="saveEdit(server)">
          <server-fields v-model:where="editing.where" :form="editing.form" :errors="editErrors" entry />
          <div class="flex flex-wrap items-center gap-2 mt-4">
            <button type="submit" class="lx-primary" :disabled="editing.busy || Object.keys(editErrors).length > 0">Save</button>
            <button type="button" class="lx-action" @click="editing.serverId = null">Cancel</button>
            <span v-if="editPreview" class="lx-mono text-gray-400 truncate">{{ editPreview }}</span>
          </div>
        </form>

      </article>

    </section>

    <admin-access-dialog :server="adminServer" @close="adminServer = null" @signed-in="signedIn" />
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Bookmark,
  Files,
  KeyRound,
  LayoutDashboard,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  Trash2,
  X
} from 'lucide-vue-next'
import AdminAccessDialog from '../components/AdminAccessDialog.vue'
import ServerFields from '../components/ServerFields.vue'
import { desktop, wantsNewWindow } from '../bridge.js'
import {
  state,
  attempt,
  fail,
  launchOptionsFor,
  notify,
  openDashboard,
  processFor,
  recallLaunch,
  refreshServers,
  registerServer,
  rewriteLaunch,
  forgetServer,
  runningServers,
  startServer,
  unregisteredServers,
  updateServer,
  updateServerInstallation,
  withQibocamp,
  machines as machineList
} from '../state.js'
import MachineFilter from '../components/MachineFilter.vue'
import { onMachine } from '../lib/machines.js'
import { blankForm, formFrom, formProblems, launchFrom, launchKeys, serverCommand, serverUrl, suggestServerName } from '../lib/servers.js'
import { usePageKeys } from '../composables/pageKeys.js'

const route = useRoute()
const router = useRouter()

const creating = ref(false)
const where = ref('local')
const form = reactive(blankForm())
/** The server whose administrator access is being restored, in its dialog. */
const adminServer = ref(null)
/** The server being edited, with the form opened on it. */
const editing = reactive({ serverId: null, where: 'local', form: blankForm(), busy: false })
const starting = ref(false)
/** The server whose qibocamp is being updated, by id. */
const updating = ref(null)

const temporary = computed(() => unregisteredServers())

/** A fresh name, as the command line draws them, avoiding the ones in use. */
function freshName() {
  return suggestServerName([
    ...state.servers.map(server => server.name),
    ...runningServers().map(process => process.label)
  ])
}

const errors = computed(() => formProblems(where.value, form))
const editErrors = computed(() => formProblems(editing.where, editing.form, { entry: true }))

const preview = computed(() => serverCommand(launchOptions(false)))
const editPreview = computed(() => {
  const options = launchFrom(editing.where, editing.form)
  return options ? serverCommand(options) : ''
})

/** Exactly what `start_server` receives, and what the preview describes. */
function launchOptions(register) {
  return launchFrom(where.value, form, { name: freshName(), register })
}

// The address of a server on this machine follows its host and port, as
// long as it was theirs: an address typed by hand is left alone.
watch(() => [editing.form.host, editing.form.port], ([host, port], [previousHost, previousPort]) => {
  if (editing.where !== 'local') return
  if (serverUrl(editing.form.url) !== serverUrl(`http://${previousHost}:${previousPort}`)) return
  editing.form.url = `http://${host || 'localhost'}:${port}`
})

async function launch(register) {
  const problem = Object.values(errors.value)[0]
  if (problem) {
    fail(problem)
    return
  }
  starting.value = true
  const options = launchOptions(register)
  try {
    await startServer(options)
    notify(
      options.ssh
        ? `Starting ${options.name} on ${options.ssh}; the tunnel address appears in Running.`
        : `Starting ${options.name} on http://${options.host}:${options.port}.`,
      'processes'
    )
    // The next server gets a name of its own.
    form.name = freshName()
    // The command line registers the instance itself, once it is up.
    if (register) setTimeout(refreshServers, 1500)
    router.push({ name: 'processes' })
  } catch (error) {
    fail(error)
  } finally {
    starting.value = false
  }
}

/** The main action of a registered server: open it while it runs, start it otherwise. */
function activate(server, event = null) {
  if (processFor(server)) return openDashboard(server.url, { newWindow: wantsNewWindow(event) })
  return relaunch(server)
}

// j and k walk the registered servers; the letters do what the buttons do.
/** The machine a registered server runs on: the destination remembered with its launch; none for this one. */
function serverSsh(server) {
  const launches = state.preferences?.launches || {}
  const key = launchKeys(server.url).find(entry => launches[entry])
  return String(launches[key]?.ssh || '').trim()
}
/** The machine the list is narrowed to, and what it shows. */
const machineFilter = ref('all')
const shownServers = computed(() => state.servers.filter(server => onMachine(serverSsh(server), machineFilter.value, machineList.value)))

const { focused } = usePageKeys({
  page: 'servers',
  count: () => shownServers.value.length,
  actions: {
    Enter: (index, event) => activate(shownServers.value[index], event),
    s: (index, event) => activate(shownServers.value[index], event),
    e: index => toggleEdit(shownServers.value[index]),
    i: index => { adminServer.value = shownServers.value[index] },
    d: index => duplicate(shownServers.value[index]),
    u: index => { if (launchOptionsFor(shownServers.value[index])) update(shownServers.value[index]) }
  }
})

/** Bring a server's qibocamp up to date and start it again, keeping its entry. */
async function update(server) {
  updating.value = server.id
  const started = await attempt(
    () => updateServerInstallation(server),
    `Updating ${server.name || server.url}; it starts again when the installation is done.`,
    'processes'
  )
  updating.value = null
  if (started) router.push({ name: 'processes' })
}

/** Start a registered server: as last time, or at its address on this machine. */
async function relaunch(server) {
  const options = launchOptionsFor(server)
  if (!options) return
  starting.value = true
  const started = await attempt(
    () => startServer(options, { url: server.url }),
    `Starting ${server.name || server.url}.`,
    'processes'
  )
  starting.value = false
  if (started) router.push({ name: 'processes' })
}

async function register() {
  const problem = errors.value.url
  if (problem) {
    fail(problem)
    return
  }
  const name = form.name.trim() || freshName()
  const added = await attempt(
    () => registerServer({ name, url: form.url, description: `Server at ${form.url}` }),
    `${name} registered.`,
    'servers'
  )
  if (added) {
    form.url = ''
    form.name = freshName()
  }
}

/** Keep a server that was started for a look. */
async function keep(process) {
  await attempt(
    () => registerServer({
      name: process.label,
      url: process.url,
      description: `Started by the launcher: ${process.detail}`
    }),
    `${process.label} registered.`,
    'servers'
  )
}

async function forget(server) {
  await attempt(() => forgetServer(server.id), `${server.name || server.url} removed.`, 'servers')
}

/** Open the form on everything known about a server: its entry, and how it starts. */
function toggleEdit(server) {
  if (editing.serverId === server.id) {
    editing.serverId = null
    return
  }
  const opened = formFrom(launchOptionsFor(server), server)
  Object.assign(editing, { serverId: server.id, where: opened.where, busy: false })
  Object.assign(editing.form, opened.form)
}

/** Save the entry, and remember — or forget — how the server is started. */
async function saveEdit(server) {
  const problem = Object.values(editErrors.value)[0]
  if (problem) {
    fail(problem)
    return
  }
  editing.busy = true
  const name = editing.form.name.trim() || server.name || ''
  // Whether the command line registers it again is as it was; a server it
  // never registered has an entry already.
  const register = recallLaunch(server.url)?.register ?? false
  const options = launchFrom(editing.where, editing.form, { name, register })
  const saved = await attempt(
    async () => {
      const updated = await updateServer(server.id, {
        name,
        url: editing.form.url,
        description: editing.form.description
      })
      await rewriteLaunch(server.url, updated.url, options)
      return updated
    },
    `${name || editing.form.url} updated.`,
    'servers'
  )
  editing.busy = false
  if (saved) editing.serverId = null
}

/** Signed in from Administrator access: the dashboard has the session at its next load. */
function signedIn({ server, user }) {
  notify(`Signed in to ${server.name || server.url} as ${user?.username || 'administrator'}; the dashboard opens on it signed in.`, 'servers')
}

/**
 * Jumping here from the system menu preselects the server to start; from
 * the `n` key, the form opens empty.
 */
/**
 * A new server with this one's options: the form opens on a copy, under a
 * name of its own, for the few things that differ — another directory,
 * another port, another host — to be changed before it is started or
 * registered. A server the launcher does not start is copied as an address.
 */
function duplicate(server) {
  const opened = formFrom(launchOptionsFor(server), server)
  Object.assign(form, opened.form, {
    name: `${server.name || 'server'} copy`,
    // Two servers cannot share a port here: the copy takes the next one.
    port: opened.where === 'local' ? Number(opened.form.port) + 1 : opened.form.port,
    description: ''
  })
  where.value = opened.where
  creating.value = true
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

function applyQuery() {
  if (route.query.new) {
    creating.value = true
    return
  }
  const server = state.servers.find(entry => entry.id === route.query.server)
  if (!server) return
  const remembered = recallLaunch(server.url)
  if (!remembered) return
  const opened = formFrom(remembered, server)
  Object.assign(form, opened.form)
  where.value = opened.where
  creating.value = true
}

onMounted(async () => {
  await refreshServers()
  applyQuery()
  // The name is drawn once the list is known, so it does not repeat one.
  if (!form.name) form.name = freshName()
})
watch(() => [route.query.server, route.query.new], applyQuery)
</script>
