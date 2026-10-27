/**
 * The system menu, built from the application side.
 *
 * Items carry ids, and the actions are the very functions the interface
 * calls, kept by each page: the native side hands a chosen item to the window
 * in front, whose page runs its action. The menu is installed again when its
 * content changes — the servers, the zoom level — and never because a window
 * took the focus: on Linux that replaces every window's menu bar, which moves
 * the focus, which installed it again, endlessly.
 */
import { isDesktop } from './bridge.js'
import { sections } from './router.js'
import { describeZoom, zoom, zoomLevel } from './zoom.js'

const MAX_QUICK_SERVERS = 8

/** The event `background::route_menu_event` sends, on the native side. */
const MENU_EVENT = 'qibocamp://menu'

/** What each item does in this page, by id; filled by `build`. */
let actions = new Map()

async function build({ goTo, openDashboard, launchServer, servers, quit }) {
  const { Menu, Submenu, MenuItem: NativeItem, PredefinedMenuItem } = await import('@tauri-apps/api/menu')
  const next = new Map()
  // An item and its action: the action stays here, the item only names it.
  const MenuItem = {
    new: ({ action, ...options }) => {
      if (action) next.set(options.id, action)
      return NativeItem.new(options)
    }
  }

  const separator = () => PredefinedMenuItem.new({ item: 'Separator' })
  const items = []

  items.push(await Submenu.new({
    text: 'Qibocamp',
    items: [
      await MenuItem.new({
        id: 'menu-dashboard',
        text: 'Open Dashboard',
        accelerator: 'CmdOrCtrl+D',
        action: () => openDashboard()
      }),
      await separator(),
      await PredefinedMenuItem.new({ item: 'Hide' }),
      // Quitting is explicit: the window's close leaves the launcher in the
      // background; this stops what runs, waits for it, and goes.
      await MenuItem.new({
        id: 'menu-quit',
        text: 'Quit Qibocamp',
        accelerator: 'CmdOrCtrl+Q',
        action: () => quit?.()
      })
    ]
  }))

  items.push(await Submenu.new({
    text: 'Edit',
    items: [
      await PredefinedMenuItem.new({ item: 'Undo' }),
      await PredefinedMenuItem.new({ item: 'Redo' }),
      await separator(),
      await PredefinedMenuItem.new({ item: 'Cut' }),
      await PredefinedMenuItem.new({ item: 'Copy' }),
      await PredefinedMenuItem.new({ item: 'Paste' }),
      await PredefinedMenuItem.new({ item: 'SelectAll' })
    ]
  }))

  const go = []
  for (const section of sections) {
    go.push(await MenuItem.new({
      id: `menu-go-${section.name}`,
      text: section.title,
      accelerator: section.accelerator,
      action: () => goTo(section.name)
    }))
  }
  go.push(await separator())
  go.push(await MenuItem.new({
    id: 'menu-go-dashboard',
    text: 'Dashboard',
    accelerator: 'CmdOrCtrl+D',
    action: () => openDashboard()
  }))
  items.push(await Submenu.new({ text: 'Go', items: go }))

  const quick = []
  for (const server of servers.slice(0, MAX_QUICK_SERVERS)) {
    quick.push(await MenuItem.new({
      id: `menu-server-${server.id}`,
      text: server.name || server.url,
      action: () => launchServer(server)
    }))
  }
  if (!quick.length) {
    quick.push(await MenuItem.new({
      id: 'menu-server-empty',
      text: 'No registered server',
      enabled: false
    }))
  }
  items.push(await Submenu.new({
    text: 'Servers',
    items: [
      ...quick,
      await separator(),
      await MenuItem.new({
        id: 'menu-server-register',
        text: 'Register a Server…',
        accelerator: 'CmdOrCtrl+Shift+N',
        action: () => goTo('servers')
      })
    ]
  }))

  // The zoom, as every browser has it; the level is shown on the reset item.
  items.push(await Submenu.new({
    text: 'View',
    items: [
      await MenuItem.new({
        id: 'menu-zoom-in',
        text: 'Zoom In',
        accelerator: 'CmdOrCtrl+Plus',
        action: () => zoom('in')
      }),
      await MenuItem.new({
        id: 'menu-zoom-out',
        text: 'Zoom Out',
        accelerator: 'CmdOrCtrl+-',
        action: () => zoom('out')
      }),
      await MenuItem.new({
        id: 'menu-zoom-reset',
        text: `Actual Size${zoomLevel() === 1 ? '' : ` (now ${describeZoom(zoomLevel())})`}`,
        accelerator: 'CmdOrCtrl+0',
        action: () => zoom('reset')
      })
    ]
  }))

  items.push(await Submenu.new({
    text: 'Window',
    items: [
      await MenuItem.new({
        id: 'menu-window-new',
        text: 'New Window',
        accelerator: 'CmdOrCtrl+N',
        // The page one is on, again, in a window of its own: to compare two.
        action: () => window.open(window.location.href, '_blank', 'noopener')
      }),
      await separator(),
      await PredefinedMenuItem.new({ item: 'Minimize' }),
      await PredefinedMenuItem.new({ item: 'Fullscreen' }),
      // The last window closed is hidden, the launcher left in the background.
      await PredefinedMenuItem.new({ item: 'CloseWindow' })
    ]
  }))

  const menu = await Menu.new({ items })
  actions = next
  return menu
}

/**
 * Install (or rebuild) the system menu.
 *
 * Rebuilding is how the quick actions stay in step with the registered
 * servers; failures are ignored on purpose, a missing menu must never keep
 * the interface itself from starting.
 */
let listening = false

/** Run the actions the native side routes to this window. */
async function listenToMenu() {
  if (listening) return
  listening = true
  const { getCurrentWebviewWindow } = await import('@tauri-apps/api/webviewWindow')
  await getCurrentWebviewWindow().listen(MENU_EVENT, ({ payload }) => actions.get(payload)?.())
}

export async function installMenu(handlers) {
  if (!isDesktop) return false
  try {
    await listenToMenu()
    const menu = await build(handlers)
    await menu.setAsAppMenu()
    return true
  } catch {
    return false
  }
}
