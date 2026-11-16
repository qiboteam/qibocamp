/**
 * The documentation's table of contents, as the dashboard shows it — the
 * pages it carries itself, whatever server it reads — and the launcher's
 * Help, which shows the Desktop Launcher section of it. No Vue here: the launcher imports it on a
 * page where the dashboard's store does not run.
 */
export const DEFAULT_DOCS_NAV = [
  {
    section: 'Overview',
    path: 'index',
    items: [
      {
        id: 'index',
        path: 'index',
        title: 'Overview & Ecosystem',
        description: 'Introduction to Qibo ecosystem, projects, and Qibocamp architecture'
      }
    ]
  },
  {
    section: 'User Guide',
    path: 'user-guide',
    items: [
      {
        id: 'user-guide/quickstart',
        path: 'user-guide/quickstart',
        title: 'Getting Started & Quickstart',
        description: 'Installation, CLI commands, and report directory structure'
      },
      {
        id: 'user-guide/dashboard',
        path: 'user-guide/dashboard',
        title: 'Dashboard & Server Management',
        description: 'Managing servers, search, smart facets, dual views, and bulk actions'
      },
      {
        id: 'user-guide/reports',
        path: 'user-guide/reports',
        title: 'Reports, Protocols & Exports',
        description: 'Interactive charts, protocol timings, downloads, and PDF printing'
      }
    ]
  },
  {
    section: 'Desktop Launcher',
    path: 'user-guide/launcher',
    items: [
      {
        id: 'user-guide/launcher/installation',
        path: 'user-guide/launcher/installation',
        title: 'Installation',
        description: 'Installers for each platform, opening an unsigned build, and the Qibocamp Server the launcher drives'
      },
      {
        id: 'user-guide/launcher/sections',
        path: 'user-guide/launcher/sections',
        title: 'Sections & Windows',
        description: 'The sections, several windows, running in the background, and zoom'
      },
      {
        id: 'user-guide/launcher/keyboard',
        path: 'user-guide/launcher/keyboard',
        title: 'Keyboard Shortcuts',
        description: 'GitHub-style keys, the menu shortcuts, and the keys of each page'
      },
      {
        id: 'user-guide/launcher/qibocamp',
        path: 'user-guide/launcher/qibocamp',
        title: 'Installing Qibocamp',
        description: "The launcher's own environment, its version, and other environments"
      },
      {
        id: 'user-guide/launcher/servers',
        path: 'user-guide/launcher/servers',
        title: 'Report Servers',
        description: 'Starting and registering servers, here or over SSH, and handling them while they run'
      },
      {
        id: 'user-guide/launcher/notebooks',
        path: 'user-guide/launcher/notebooks',
        title: 'Notebooks & Queues',
        description: 'JupyterLab and Marimo sessions, locally, over SSH or through SLURM, and the queues'
      },
      {
        id: 'user-guide/launcher/remote',
        path: 'user-guide/launcher/remote',
        title: 'Remote Execution',
        description: "Connections for qibocal-widgets' remote executor, and their reports"
      },
      {
        id: 'user-guide/launcher/agents',
        path: 'user-guide/launcher/agents',
        title: 'Agents',
        description: 'Configurations of the Qibocal MCP servers, here and on access nodes, and autonomous runs'
      },
      {
        id: 'user-guide/launcher/machines',
        path: 'user-guide/launcher/machines',
        title: 'Machines',
        description: 'The machines reached over SSH, named once for every page, and what each one serves'
      },
      {
        id: 'user-guide/launcher/configuration',
        path: 'user-guide/launcher/configuration',
        title: 'Configuration',
        description: 'The files shared with the command line, their formats, and the web client'
      }
    ]
  },
  {
    section: 'Admin Guide',
    path: 'admin-guide',
    items: [
      {
        id: 'admin-guide/index',
        path: 'admin-guide',
        title: 'Server Administration & Security',
        description: 'Enabling authentication (--auth), initial bootstrap, and server administration'
      },
      {
        id: 'admin-guide/roles',
        path: 'admin-guide/roles',
        title: 'Roles & Permissions Matrix',
        description: 'Viewer, Editor, and Admin capabilities, restrictions, and enforcement'
      },
      {
        id: 'admin-guide/invitations',
        path: 'admin-guide/invitations',
        title: 'Invitations & Team Onboarding',
        description: 'Generating tokens, expiration policies, paste-box flows, and revocation'
      }
    ]
  },
  {
    section: 'Developer & Architecture',
    path: 'developer',
    items: [
      {
        id: 'developer/architecture',
        path: 'developer/architecture',
        title: 'System Architecture',
        description: 'FastAPI backend, Vue 3 SPA frontend, and execution modes'
      },
      {
        id: 'developer/workflow',
        path: 'developer/workflow',
        title: 'Development Workflow',
        description: 'devenv environment, live Vite HMR, testing, and packaging'
      },
      {
        id: 'developer/design-system',
        path: 'developer/design-system',
        title: 'Design System & Aesthetics',
        description: 'BackMarket design palette, typography, and print media CSS'
      },
      {
        id: 'developer/nix',
        path: 'developer/nix',
        title: 'The Nix Package',
        description: 'The flake, its hashes, its CI, and the binary cache'
      }
    ]
  },
  {
    section: 'Reference',
    path: 'reference',
    items: [
      {
        id: 'reference/cli',
        path: 'reference/cli',
        title: 'CLI Reference',
        description: 'Complete command line options and environment variables'
      },
      {
        id: 'reference/api',
        path: 'reference/api',
        title: 'REST API & WebSockets',
        description: 'REST endpoints specification, schemas, and WebSocket events'
      }
    ]
  }
]

/** The section a page belongs to, by its path; null for none. */
export function docsSection(path, nav = DEFAULT_DOCS_NAV) {
  return nav.find(section => section.path === path || section.items.some(item => item.path === path)) || null
}

/** The deployed dashboard's documentation: a link to it is to this very documentation. */
const DEPLOYED_DOCS = /^https?:\/\/qibo\.science\/qibocamp\/?#\/docs\/?(.*)$/i

/** Every page of the documentation, by path: the sections' own and their items. */
export function docPages(nav = DEFAULT_DOCS_NAV) {
  const pages = new Set(['index'])
  for (const section of nav) {
    if (section.path) pages.add(section.path)
    for (const item of section.items || []) pages.add(item.path)
  }
  return pages
}

/**
 * Where a link on page `current` leads: `{ kind: 'anchor', id }` within it,
 * `{ kind: 'doc', page, id }` to a page of this documentation — a path from
 * its root, as the dashboard writes them, or one relative to the page's own
 * file, as on GitHub; a link to the deployed documentation is one too — or
 * `{ kind: 'url', url }` anywhere else.
 */
export function resolveDocLink(href, current = 'index', nav = DEFAULT_DOCS_NAV) {
  let text = String(href || '').trim()
  if (!text) return { kind: 'url', url: null }
  if (text.startsWith('#')) return { kind: 'anchor', id: text.slice(1) }
  const deployed = text.match(DEPLOYED_DOCS)
  if (deployed) text = `/${deployed[1]}`
  else if (/^[a-z][a-z0-9+.-]*:|^\/\//i.test(text)) return { kind: 'url', url: text }
  const [path, id = null] = text.split('#')
  const page = value => value.replace(/\.md$/, '').replace(/\/index$/, '').replace(/^index$/, '').replace(/\/$/, '') || 'index'
  const pages = docPages(nav)
  const rooted = page(path.replace(/^\/+/, '').replace(/^docs\//, ''))
  if (!path.startsWith('.') && pages.has(rooted)) return { kind: 'doc', page: rooted, id }
  // Relative to the page's own file: a section's page is its index.
  const sections = new Set(nav.map(section => section.path).filter(path => path && path !== 'index'))
  const file = current === 'index' ? 'index.md' : sections.has(current) ? `${current}/index.md` : `${current}.md`
  const resolved = new URL(path, `https://docs.invalid/${file}`).pathname.replace(/^\/+/, '')
  return { kind: 'doc', page: page(decodeURIComponent(resolved)), id }
}
