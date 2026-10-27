import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  NOTEBOOK_FIELDS,
  REPORTS_ENVIRONMENT,
  blankEditor,
  defaultNotebookOptions,
  describeConnection,
  environmentName,
  fieldApplies,
  normalizeNotebookOptions,
  notebookKey,
  placeOf,
  remoteChoices,
  reportServerOptions,
  requirementCount,
  toEditorValues
} from '../src/lib/notebooks.js'

test('the editor starts from the command-line defaults', () => {
  const values = defaultNotebookOptions()
  assert.equal(values.venv, '', 'unset: a registered connection gets its own, a session not saved qibocal')
  assert.equal(values.marimo, false)
  assert.equal(values.timeout, 300)
  assert.deepEqual(Object.keys(values).sort(), NOTEBOOK_FIELDS.map(field => field.name).sort())
})

test('only the options that differ from the defaults are passed on', () => {
  const { options, valid } = normalizeNotebookOptions({
    ...defaultNotebookOptions(),
    ssh: '  user@login  ',
    queue: 'mychip'
  })
  assert.ok(valid)
  assert.deepEqual(options, { ssh: 'user@login', queue: 'mychip' })
})

test('a stored connection fills the editor back', () => {
  const values = toEditorValues({ ssh: 'user@login', marimo: true })
  assert.equal(values.ssh, 'user@login')
  assert.equal(values.marimo, true)
  assert.equal(values.venv, '', 'unset options stay unset')
})

test('subnets with host bits set are refused, like ip_network does', () => {
  const rejected = normalizeNotebookOptions({ ...defaultNotebookOptions(), subnet: '192.168.0.1/24' })
  assert.equal(rejected.valid, false)
  assert.match(rejected.errors.subnet, /192\.168\.0\.0\/24/)

  const accepted = normalizeNotebookOptions({ ...defaultNotebookOptions(), subnet: '192.168.0.0/24' })
  assert.equal(accepted.valid, true)
  assert.equal(accepted.options.subnet, '192.168.0.0/24')
})

test('malformed subnets are refused', () => {
  for (const subnet of ['192.168.0.0', '10.0.0.0/33', '300.1.1.0/24', 'not/a/network']) {
    assert.equal(
      normalizeNotebookOptions({ ...defaultNotebookOptions(), subnet }).valid,
      false,
      `${subnet} should be refused`
    )
  }
})

test('a non-positive timeout is refused', () => {
  const { valid, errors } = normalizeNotebookOptions({ ...defaultNotebookOptions(), timeout: 0 })
  assert.equal(valid, false)
  assert.ok(errors.timeout)
})

test('srun options without a partition warn without blocking', () => {
  const { valid, warnings, options } = normalizeNotebookOptions({
    ...defaultNotebookOptions(),
    slurm: '--time 02:00:00'
  })
  assert.equal(valid, true, 'the session can still be started')
  assert.ok(warnings.slurm)
  assert.equal(options.slurm, '--time 02:00:00')
})

test('connections are described in one line', () => {
  assert.equal(describeConnection({}), 'local session · JupyterLab')
  assert.equal(
    describeConnection({ ssh: 'user@login', queue: 'mychip', marimo: true }),
    'ssh user@login · partition mychip · Marimo'
  )
})

test('requirements keep their lines, are optional, and are counted in the summary', () => {
  const text = '  numpy>=2\n# for the fits\nscipy\n\n'
  const { options, valid } = normalizeNotebookOptions({ ...defaultNotebookOptions(), requirements: text })
  assert.equal(valid, true)
  assert.equal(options.requirements, 'numpy>=2\n# for the fits\nscipy', 'only the ends are trimmed')
  assert.equal(normalizeNotebookOptions(defaultNotebookOptions()).options.requirements, undefined)
  assert.equal(requirementCount(text), 2, 'comments and blank lines do not count')
  assert.match(describeConnection({ ssh: 'lab', requirements: 'numpy\nscipy' }), /2 requirements$/)
  assert.doesNotMatch(describeConnection({ ssh: 'lab' }), /requirement/)
})

test('a notebook report server runs on the same channel and serves the working directory', () => {
  const remote = reportServerOptions({ ssh: '-p 2222 user@login', queue: 'chip', workdir: '/scratch/runs', venv: 'calib' }, { name: 'chip' })
  assert.equal(remote.ssh, '-p 2222 user@login', 'the same destination, options and all')
  assert.equal(remote.directory, '/scratch/runs')
  assert.equal(remote.workdir, '/scratch/runs')
  assert.equal(remote.venv, REPORTS_ENVIRONMENT, 'not the kernel environment, which holds qibocal, not this qibocamp')
  assert.equal(remote.name, 'chip reports')
  assert.equal(remote.register, false, 'for a look: nothing is registered')
  assert.equal(remote.queue, undefined, 'the server runs on the access node, never through SLURM')

  assert.equal(reportServerOptions({ ssh: 'user@login' }).directory, '~', 'where the login lands, without a working directory')
  assert.equal(reportServerOptions({ ssh: 'user@login' }).name, 'user@login reports')
  const local = reportServerOptions({}, { home: '/Users/me', port: 54321 })
  assert.equal(local.directory, '/Users/me')
  assert.equal(local.ssh, null)
  assert.equal(local.venv, null, 'here, the launcher\'s own installation')
  assert.equal(local.port, 54321)

  assert.equal(notebookKey({ ssh: 'a' }, 'chip'), 'chip')
  assert.equal(notebookKey({ ssh: 'a', workdir: '/w' }), 'a|/w')
  assert.equal(notebookKey({}), 'local|~')
})

test('a notebook report server serves the reports folder, relative to where the session starts', () => {
  assert.equal(reportServerOptions({ ssh: 'login', workdir: '/scratch/runs', reports: 'reports' }).directory, '/scratch/runs/reports')
  assert.equal(reportServerOptions({ ssh: 'login', reports: 'reports' }).directory, '~/reports', 'the login home without a working directory')
  assert.equal(reportServerOptions({ ssh: 'login', workdir: '/w', reports: '/data/reports' }).directory, '/data/reports')
  assert.equal(reportServerOptions({ ssh: 'login', reports: '~/calibration' }).directory, '~/calibration')
  assert.equal(reportServerOptions({ reports: 'out' }, { home: '/Users/me' }).directory, '/Users/me/out')
  assert.equal(reportServerOptions({ ssh: 'login', workdir: '/w' }).directory, '/w', 'no reports folder: where the session starts')
})

test('remote execution connections share the notebook fields and keep their own', async () => {
  const { REMOTE_FIELDS, defaultRemoteOptions, describeRemote, normalizeRemoteOptions, rebuildOptions } = await import('../src/lib/remotes.js')
  const names = REMOTE_FIELDS.map(field => field.name)
  for (const name of ['ssh', 'queue', 'slurm', 'workdir', 'venv', 'requirements', 'rebuild_on_compute', 'platform', 'platforms_dir', 'python', 'timeout', 'reports']) {
    assert.ok(names.includes(name), `${name}, as remotes.json has it`)
  }
  assert.equal(names.includes('marimo'), false, 'nothing a notebook alone has')
  assert.equal(defaultRemoteOptions().venv, '', 'no environment named: the connection\'s own')
  const { options, valid } = normalizeRemoteOptions({ ...defaultRemoteOptions(), ssh: 'me@login', queue: 'qpu', platform: 'qw11q', timeout: '600' })
  assert.equal(valid, true)
  assert.deepEqual(options, { ssh: 'me@login', queue: 'qpu', platform: 'qw11q', timeout: 600 }, 'only what differs from the defaults is stored')
  assert.equal(describeRemote({ ssh: 'me@login', queue: 'qpu', platform: 'qw11q' }, 'lab'), 'ssh me@login · partition qpu · platform qw11q · _remote/lab')
  assert.equal(REMOTE_FIELDS.find(field => field.name === 'venv').placeholderFor({ name: 'QW5Q lab' }), '_remote/QW5Q-lab')
  assert.deepEqual(rebuildOptions({ ssh: 'me@login' }, 'QW5Q lab'), { ssh: 'me@login', venv: '_remote/QW5Q-lab' },
    'its own environment, apart from a session of the same name')
  assert.equal(normalizeRemoteOptions({ ...defaultRemoteOptions(), venv: 'qibocal' }).options.venv, 'qibocal', 'sharing one is a choice, kept')
  assert.deepEqual(normalizeRemoteOptions({ ...defaultRemoteOptions(), platforms_dir: '~/platforms' }).options, { platforms_dir: '~/platforms' },
    'the platforms folder, as qibocal-widgets reads it')
  assert.deepEqual(rebuildOptions({ ssh: 'me@login', queue: 'qpu', platform: 'qw11q', platforms_dir: '~/platforms', python: '3.12', venv: 'lab', reports: 'out' }),
    { ssh: 'me@login', queue: 'qpu', venv: 'lab' }, 'what qibocamp notebook rebuild takes, and nothing else')
})

test('a session runs here, on the access node, or on a computing node, with the fields that make sense there', () => {
  assert.equal(placeOf({}), 'local')
  assert.equal(placeOf({ ssh: 'login' }), 'access')
  assert.equal(placeOf({ ssh: 'login', queue: 'chip' }), 'compute')
  assert.equal(toEditorValues({ ssh: 'login' }).where, 'access', 'a stored connection reopens where it runs')

  const filled = { ...blankEditor(), ssh: 'login', queue: 'chip', slurm: '--time 1:00:00', venv: 'calib', requirements: 'numpy' }
  assert.deepEqual(normalizeNotebookOptions({ ...filled, where: 'local' }).options, { venv: 'calib', requirements: 'numpy' },
    'here: no destination, no partition — the environment and its requirements stay')
  assert.deepEqual(normalizeNotebookOptions({ ...filled, where: 'access' }).options, { ssh: 'login', venv: 'calib', requirements: 'numpy' })
  assert.deepEqual(normalizeNotebookOptions({ ...filled, where: 'compute' }).options,
    { ssh: 'login', queue: 'chip', slurm: '--time 1:00:00', venv: 'calib', requirements: 'numpy' })

  assert.match(normalizeNotebookOptions({ ...blankEditor(), where: 'access' }).errors.ssh, /SSH destination/)
  assert.match(normalizeNotebookOptions({ ...blankEditor(), where: 'compute', ssh: 'login' }).errors.queue, /partition/)
  assert.equal(normalizeNotebookOptions(blankEditor()).valid, true, 'here, nothing is required')
  assert.equal(fieldApplies(NOTEBOOK_FIELDS.find(field => field.name === 'subnet'), 'access'), false)
})

test('a session is offered the remote connections that make sense from where it runs', () => {
  const remotes = { lab: { ssh: 'login', queue: 'qpu' }, onsite: { queue: 'qpu' }, other: { ssh: 'elsewhere' } }
  assert.deepEqual(remoteChoices(remotes, { where: 'local' }), ['lab', 'onsite', 'other'], 'from this machine, any')
  assert.deepEqual(remoteChoices(remotes, { where: 'access', ssh: 'login' }), ['onsite', 'other'],
    'from the access node, not the one that would reach it over SSH')
  assert.deepEqual(remoteChoices(remotes, { ssh: 'login', queue: 'chip' }), ['onsite', 'other'], 'the place read from the options')
  assert.match(describeConnection({ ssh: 'login', remote: 'onsite' }), /remote onsite$/)
  assert.equal(NOTEBOOK_FIELDS.find(field => field.name === 'remote').type, 'select')
})

test('a registered connection has an environment named after it, made safe for a folder', () => {
  assert.equal(environmentName('tuna'), 'tuna')
  assert.equal(environmentName('QW5Q platinum / v2'), 'QW5Q-platinum-v2')
  assert.equal(environmentName('..hidden'), 'hidden', 'never a hidden folder')
  assert.equal(environmentName('_remote'), 'remote', 'never the remote connections\' folder')
  assert.equal(environmentName('***'), 'qibocal')
  const venv = NOTEBOOK_FIELDS.find(field => field.name === 'venv')
  assert.equal(venv.placeholderFor({ name: 'my chip' }), 'my-chip')
  assert.equal(venv.placeholderFor({}), 'qibocal')
  assert.equal(normalizeNotebookOptions({ ...defaultNotebookOptions(), venv: 'qibocal' }).options.venv, 'qibocal',
    'naming qibocal is kept: it is a choice now, not the default')
})
