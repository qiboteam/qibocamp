/**
 * The only place that talks to the native side.
 *
 * Every call degrades to a clear error when the interface is opened in a plain
 * browser (`pnpm run dev` without the window), so the pages can still be
 * styled and reviewed outside the desktop application.
 */
import { invoke } from '@tauri-apps/api/core'
import { listen } from '@tauri-apps/api/event'
import { confirm as confirmDialog, open as openFileDialog } from '@tauri-apps/plugin-dialog'
import { openUrl, revealItemInDir } from '@tauri-apps/plugin-opener'

export const isDesktop = typeof window !== 'undefined' &&
  Boolean(window.__TAURI_INTERNALS__ || window.__TAURI__)

const UNAVAILABLE = 'This action needs the Qibocamp desktop application window.'

function call(command, args) {
  if (!isDesktop) return Promise.reject(new Error(UNAVAILABLE))
  return invoke(command, args)
}

export const desktop = {
  environment: () => call('environment'),
  setExecutable: path => call('set_executable', { path }),
  installCli: upgrade => call('install_cli', { upgrade }),
  freePort: () => call('free_port'),
  platform: () => call('platform_summary'),

  configEntries: () => call('config_entries'),
  configDirectory: () => call('config_directory'),
  readConfigFile: name => call('read_config_file', { name }),
  writeConfigFile: (name, text) => call('write_config_file', { name, text }),
  removeConfigEntries: names => call('remove_config_entries', { names }),
  preferences: () => call('preferences'),
  savePreferences: settings => call('save_preferences', { settings }),

  listServers: () => call('list_servers'),
  saveServers: servers => call('save_servers', { servers }),

  listNotebooks: () => call('list_notebooks'),
  saveNotebook: (name, options) => call('save_notebook', { name, options }),
  removeNotebook: name => call('remove_notebook', { name }),

  /** qibocal-agent's agents.json, as it is, and the configurations kept here for other machines. */
  readAgents: () => call('read_agents'),
  writeAgents: document => call('write_agents', { document }),
  listAgentHosts: () => call('list_agent_hosts'),
  /** The machines, by name: `{ name: { ssh } }`, in machines.json. */
  listMachines: () => call('list_machines'),
  saveMachines: machines => call('save_machines', { machines }),
  saveAgentHosts: hosts => call('save_agent_hosts', { hosts }),
  pushAgents: (ssh, agents, remove = []) => call('push_agents', { ssh, agents, remove }),
  /** The qibocal-agent the Agents page uses, and installing it where the launcher keeps it. */
  agentTools: () => call('agent_tools'),
  installAgentTools: reference => call('install_agent_tools', { reference }),
  /** An autonomous agent run, `{ name, objective }`, as a process in Running. */
  startAgent: request => call('start_agent', { request }),

  /** qibocal-widgets' remote execution connections, in remotes.json. */
  listRemotes: () => call('list_remotes'),
  saveRemote: (name, options) => call('save_remote', { name, options }),
  removeRemote: name => call('remove_remote', { name }),

  startServer: options => call('start_server', { options }),
  /** `qibocamp report server upgrade` on another machine, as a process of its own. */
  upgradeServer: options => call('upgrade_server', { options }),
  startNotebook: options => call('start_notebook', { options }),
  /** `qibocamp notebook rebuild`: the kernel environment destroyed and made again. */
  rebuildNotebook: options => call('rebuild_notebook', { options }),
  processes: () => call('processes'),
  setProcessLaunch: (id, launch) => call('set_process_launch', { id, launch }),
  processLog: id => call('process_log', { id }),
  stopProcess: id => call('stop_process', { id }),
  forgetProcess: id => call('forget_process', { id }),
  /** Close the launcher now; `force` kills what is still running first. */
  quit: (force = false) => call('quit', { force }),
  /** Quit the way the menu does: the windows back, the processes stopped and waited for. */
  requestQuit: () => call('request_quit'),
  /** A system notification, for what happens while the user is not looking. */
  systemNotify: (title, body) => call('system_notify', { title, body }),

  /** The users of a server: on this machine, or on its own over SSH (`{ ssh, venv }`). */
  administrators: (machine = null) => call('administrators', { machine }),
  /** Ask a SLURM queue once: `{ ssh, partition, user, states, timeout }`. */
  /** A machine's queues at once: the partitions' jobs, and a line for every partition there. */
  slurmMachine: request => call('slurm_machine', { request }),
  reissueInvite: request => call('reissue_invite', { request })
}

/**
 * Whether a click, or a key, asks for a new window: a modifier held — the
 * command or control key, or shift — or the middle button.
 */
export function wantsNewWindow(event) {
  return Boolean(event && (event.metaKey || event.ctrlKey || event.shiftKey || event.button === 1))
}

/**
 * Open one of the application's pages in a new window. The native side
 * answers `window.open` with a window of its own for the pages that are
 * ours; in a browser this is a new tab.
 */
export function openInNewWindow(href) {
  const target = new URL(href, window.location.href).href
  window.open(target, '_blank', 'noopener')
}

/** Subscribe to a launcher event, returning an unsubscribe function. */
export async function subscribe(event, handler) {
  if (!isDesktop) return () => {}
  return listen(event, message => handler(message.payload))
}

export async function chooseDirectory(title, defaultPath = null) {
  if (!isDesktop) throw new Error(UNAVAILABLE)
  const selected = await openFileDialog({ directory: true, multiple: false, title, defaultPath })
  return typeof selected === 'string' ? selected : null
}

export async function chooseFile(title, defaultPath = null) {
  if (!isDesktop) throw new Error(UNAVAILABLE)
  const selected = await openFileDialog({ directory: false, multiple: false, title, defaultPath })
  return typeof selected === 'string' ? selected : null
}

/** Open an address in the user's own browser, not inside the application. */
export async function openExternal(url) {
  if (!isDesktop) {
    window.open(url, '_blank', 'noopener')
    return
  }
  await openUrl(url)
}

export async function revealInFileManager(path) {
  if (!isDesktop) throw new Error(UNAVAILABLE)
  await revealItemInDir(path)
}

/**
 * Ask before something that cannot be undone: the system's own dialog in
 * the application, the browser's in a plain page. True when confirmed.
 */
export async function confirmAction(message, { title = 'Qibocamp', okLabel = 'OK' } = {}) {
  if (!isDesktop) return typeof window !== 'undefined' && window.confirm(message)
  return confirmDialog(message, { title, kind: 'warning', okLabel, cancelLabel: 'Cancel' })
}
