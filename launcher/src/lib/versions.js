/**
 * Which qibocamp goes into the launcher's own environment.
 *
 * The choice is a pip requirement, stored as `package` in `launcher.json`:
 * a release from PyPI, or the archive of a commit, branch or tag of the
 * repository — a bare URL, so no git is needed and the remote worker, which
 * splits its `--install` into words, gets one word. Without a choice, the
 * launcher installs the commit it was built from, which the native side
 * reports. The lists come from PyPI and the GitHub API, fetched by the
 * interface; everything here is pure and tested.
 */

export const PROJECT = 'qibocamp'
export const REPOSITORY = 'qiboteam/qibocamp'
export const PYPI_URL = `https://pypi.org/pypi/${PROJECT}/json`
export const BRANCHES_URL = `https://api.github.com/repos/${REPOSITORY}/branches?per_page=100`
export const TAGS_URL = `https://api.github.com/repos/${REPOSITORY}/tags?per_page=100`

const ARCHIVE = `https://github.com/${REPOSITORY}/archive`

/** A branch, tag or commit, as pip installs it: the archive of that ref. */
export function archiveSpec(kind, ref) {
  if (kind === 'commit') return `${ARCHIVE}/${ref}.tar.gz`
  const folder = kind === 'tag' ? 'tags' : 'heads'
  return `${ARCHIVE}/refs/${folder}/${encodeURIComponent(ref)}.tar.gz`
}

export function releaseSpec(version) {
  return `${PROJECT}==${version}`
}

/** The last resort, when even the native side knows no commit: the main branch. */
export const MAIN_SPEC = archiveSpec('branch', 'main')

/** What the launcher installs when nothing was chosen: this build's commit. */
export function defaultSpec(environment) {
  const spec = environment?.defaultPackage
  return typeof spec === 'string' && spec.trim() ? spec.trim() : MAIN_SPEC
}

/**
 * What this build is, for the About section: the release it was made for,
 * by its tag, or else the commit it was built from — never a branch, which
 * moves — each with its page on GitHub. A build that knows neither is a
 * development build.
 */
export function buildIdentity(environment) {
  const tag = typeof environment?.buildTag === 'string' ? environment.buildTag.trim() : ''
  if (tag) return { label: tag, url: `https://github.com/${REPOSITORY}/releases/tag/${encodeURIComponent(tag)}` }
  const commit = typeof environment?.buildCommit === 'string' ? environment.buildCommit.trim() : ''
  if (commit) return { label: `commit ${commit.slice(0, 7)}`, url: `https://github.com/${REPOSITORY}/commit/${commit}` }
  return { label: 'development build', url: null }
}

/**
 * Whether this build ships with its command line — a packaged one, as the
 * Nix flake makes: then the launcher installs and updates nothing, and its
 * package manager does. A preference may still run another environment.
 */
export function bundledCli(environment) {
  return environment?.bundled === true
}

/** Whether the launcher may install or update its own command line. */
export function installOffered(environment) {
  return Boolean(environment) && !bundledCli(environment)
}

/**
 * Whether the launcher's own installation is behind what it would install
 * now — the chosen version, or this build's commit, changed since it was
 * made — and so may lack what this launcher asks of it. Only when that
 * installation is the one that runs: another environment is its owner's.
 */
export function installationBehind(environment) {
  if (!environment || environment.source !== 'managed' || !environment.managedPackage) return false
  const wanted = typeof environment.package === 'string' && environment.package.trim()
    ? environment.package.trim()
    : defaultSpec(environment)
  return environment.managedPackage !== wanted
}

/**
 * The choice behind a requirement: `{ kind, ref }` with kind `release`,
 * `branch`, `tag`, `commit` or `other` — the last for anything typed by hand.
 */
export function parseSpec(spec) {
  const text = typeof spec === 'string' ? spec.trim() : ''
  if (!text) return parseSpec(MAIN_SPEC)
  const release = text.match(new RegExp(`^${PROJECT}\\s*==\\s*([^\\s;]+)$`))
  if (release) return { kind: 'release', ref: release[1] }
  const bare = text.replace(new RegExp(`^${PROJECT}\\s*@\\s*`), '')
  const archive = bare.match(new RegExp(`^${ARCHIVE}/(?:refs/(heads|tags)/)?(.+)\\.tar\\.gz$`))
  if (archive) {
    const kind = archive[1] === 'tags' ? 'tag' : archive[1] === 'heads' ? 'branch' : 'commit'
    return { kind, ref: decodeURIComponent(archive[2]) }
  }
  return { kind: 'other', ref: text }
}

/** A short label for a requirement, for the lists and the status line. */
export function describeSpec(spec, environment = null) {
  const { kind, ref } = parseSpec(spec)
  if (kind === 'release') return `${ref} (PyPI)`
  if (kind === 'branch') return `branch ${ref}`
  if (kind === 'tag') return `tag ${ref}`
  if (kind === 'commit') {
    const short = ref.slice(0, 7)
    return environment?.buildCommit === ref ? `commit ${short} (this build)` : `commit ${short}`
  }
  return ref
}

function compareVersions(left, right) {
  const parts = value => value.split(/[.\-+]/).map(part => (/^\d+$/.test(part) ? Number(part) : part))
  const a = parts(left)
  const b = parts(right)
  for (let i = 0; i < Math.max(a.length, b.length); i += 1) {
    if (a[i] === undefined) return -1
    if (b[i] === undefined) return 1
    if (a[i] === b[i]) continue
    if (typeof a[i] === 'number' && typeof b[i] === 'number') return a[i] - b[i]
    return String(a[i]) < String(b[i]) ? -1 : 1
  }
  return 0
}

/** The releases of the PyPI JSON document, newest first, yanked ones left out. */
export function parseReleases(document) {
  const releases = document && typeof document === 'object' ? document.releases : null
  if (!releases || typeof releases !== 'object') return []
  return Object.entries(releases)
    .filter(([, files]) => Array.isArray(files) && files.length && files.some(file => !file?.yanked))
    .map(([version]) => version)
    .sort((left, right) => compareVersions(right, left))
}

/** The names of the GitHub branches or tags, `main` first, the rest as given. */
export function parseRefs(list) {
  const names = (Array.isArray(list) ? list : [])
    .map(entry => (typeof entry?.name === 'string' ? entry.name : ''))
    .filter(Boolean)
  return [...names.filter(name => name === 'main'), ...names.filter(name => name !== 'main')]
}

/**
 * The options offered by the picker: this build first, then every release,
 * branch and tag, with the current choice kept even when the lists do not
 * know it.
 */
export function versionOptions({ releases = [], branches = [], tags = [], current = '', environment = null } = {}) {
  const label = spec => describeSpec(spec, environment)
  const options = [
    ...releases.map(version => ({ spec: releaseSpec(version), kind: 'release', label: label(releaseSpec(version)) })),
    ...branches.map(name => ({ spec: archiveSpec('branch', name), kind: 'branch', label: label(archiveSpec('branch', name)) })),
    ...tags.map(name => ({ spec: archiveSpec('tag', name), kind: 'tag', label: label(archiveSpec('tag', name)) }))
  ]
  const build = defaultSpec(environment)
  if (!options.some(option => option.spec === build)) {
    options.unshift({ spec: build, kind: parseSpec(build).kind, label: label(build) })
  }
  const chosen = typeof current === 'string' ? current.trim() : ''
  if (chosen && !options.some(option => option.spec === chosen)) {
    options.push({ spec: chosen, kind: parseSpec(chosen).kind, label: label(chosen) })
  }
  return options
}
