import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  MAIN_SPEC,
  archiveSpec,
  defaultSpec,
  buildIdentity,
  bundledCli,
  describeSpec,
  installOffered,
  installationBehind,
  parseRefs,
  parseReleases,
  parseSpec,
  releaseSpec,
  versionOptions
} from '../src/lib/versions.js'

const COMMIT = 'abc1234def5678abc1234def5678abc1234def56'
const build = { defaultPackage: archiveSpec('commit', COMMIT), buildCommit: COMMIT }

test('the default is the commit the launcher was built from, a bare archive URL', () => {
  assert.equal(defaultSpec(build), `https://github.com/qiboteam/qibocamp/archive/${COMMIT}.tar.gz`)
  assert.ok(!defaultSpec(build).includes(' '), 'one word, for the remote worker')
  assert.deepEqual(parseSpec(defaultSpec(build)), { kind: 'commit', ref: COMMIT })
  assert.equal(defaultSpec(null), MAIN_SPEC, 'without a known commit, the main branch')
  assert.deepEqual(parseSpec(''), { kind: 'branch', ref: 'main' })
})

test('releases, branches, tags and hand-typed requirements are told apart', () => {
  assert.deepEqual(parseSpec(releaseSpec('0.2.0')), { kind: 'release', ref: '0.2.0' })
  assert.deepEqual(parseSpec(archiveSpec('tag', 'v0.1.0')), { kind: 'tag', ref: 'v0.1.0' })
  assert.deepEqual(parseSpec(archiveSpec('branch', 'feature/x')), { kind: 'branch', ref: 'feature/x' })
  assert.deepEqual(parseSpec(`qibocamp @ ${archiveSpec('branch', 'main')}`), { kind: 'branch', ref: 'main' }, 'the named form is read too')
  assert.deepEqual(parseSpec('/home/me/checkout'), { kind: 'other', ref: '/home/me/checkout' })
})

test('a requirement is described briefly', () => {
  assert.equal(describeSpec(releaseSpec('0.2.0')), '0.2.0 (PyPI)')
  assert.equal(describeSpec(MAIN_SPEC), 'branch main')
  assert.equal(describeSpec(archiveSpec('tag', 'v1')), 'tag v1')
  assert.equal(describeSpec(archiveSpec('commit', COMMIT)), 'commit abc1234')
  assert.equal(describeSpec(archiveSpec('commit', COMMIT), build), 'commit abc1234 (this build)')
  assert.equal(describeSpec('  something==1 '), 'something==1')
})

test('releases come newest first, yanked ones left out', () => {
  const document = {
    releases: {
      '0.1.0': [{ yanked: false }],
      '0.10.0': [{ yanked: false }],
      '0.2.0': [{ yanked: true }],
      '0.3.0rc1': [{}],
      '0.0.1': []
    }
  }
  assert.deepEqual(parseReleases(document), ['0.10.0', '0.3.0rc1', '0.1.0'])
  assert.deepEqual(parseReleases(null), [])
})

test('main leads the branches, the rest keep their order', () => {
  assert.deepEqual(parseRefs([{ name: 'feature/b' }, { name: 'main' }, { name: 'dev' }, {}]), ['main', 'feature/b', 'dev'])
})

test('a build is named by its release tag, else its commit, with its page on GitHub', () => {
  assert.deepEqual(buildIdentity({ buildTag: 'v1.2.0', buildCommit: 'abc1234def' }), {
    label: 'v1.2.0', url: 'https://github.com/qiboteam/qibocamp/releases/tag/v1.2.0'
  })
  assert.deepEqual(buildIdentity({ buildCommit: 'abc1234def5678' }), {
    label: 'commit abc1234', url: 'https://github.com/qiboteam/qibocamp/commit/abc1234def5678'
  })
  assert.deepEqual(buildIdentity({}), { label: 'development build', url: null })
  assert.deepEqual(buildIdentity(null).url, null)
})

test('the installation is behind when it is not what would be installed now', () => {
  const managed = { source: 'managed', managedPackage: archiveSpec('commit', 'old0000'), defaultPackage: archiveSpec('commit', 'abc1234') }
  assert.equal(installationBehind(managed), true, 'a new build, an installation of the old one')
  assert.equal(installationBehind({ ...managed, managedPackage: archiveSpec('commit', 'abc1234') }), false)
  assert.equal(installationBehind({ ...managed, package: archiveSpec('commit', 'old0000') }), false, 'pinned to what it holds')
  assert.equal(installationBehind({ ...managed, package: archiveSpec('branch', 'main') }), true, 'pinned to something else')
  assert.equal(installationBehind({ ...managed, source: 'preference' }), false, 'another environment is not the launcher\'s to update')
  assert.equal(installationBehind({ ...managed, managedPackage: null }), false)
  assert.equal(installationBehind(null), false)
})

test('a build that ships with its command line installs and updates nothing', () => {
  const bundled = { source: 'bundled', bundled: true, executable: '/nix/store/x-qibocamp/bin/qibocamp', defaultPackage: archiveSpec('commit', 'abc1234') }
  assert.equal(bundledCli(bundled), true)
  assert.equal(installOffered(bundled), false)
  assert.equal(installationBehind(bundled), false, 'never behind: Nix updates it')
  // A preference still runs another environment, and the build still installs nothing.
  assert.equal(installOffered({ ...bundled, source: 'preference' }), false)
  assert.equal(bundledCli({ source: 'managed', bundled: false }), false)
  assert.equal(installOffered({ source: 'managed', bundled: false }), true, 'the installers, as before')
  assert.equal(installOffered({ source: 'managed' }), true, 'a launcher that does not say: as before')
  assert.equal(installOffered(null), false, 'nothing known yet')
})

test('the picker leads with this build and keeps the current choice', () => {
  const options = versionOptions({
    releases: ['0.1.0'],
    branches: ['main'],
    current: archiveSpec('tag', 'v9'),
    environment: build
  })
  assert.deepEqual(
    options.map(option => option.label),
    ['commit abc1234 (this build)', '0.1.0 (PyPI)', 'branch main', 'tag v9']
  )
  const known = versionOptions({ branches: ['main'], current: MAIN_SPEC })
  assert.equal(known.length, 1, 'a choice the lists know is not repeated, nor is the default')
})
