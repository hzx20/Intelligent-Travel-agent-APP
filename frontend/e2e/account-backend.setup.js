import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { randomBytes } from 'node:crypto'
import { mkdtemp, realpath, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { createServer } from 'node:net'
import { basename, dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { setTimeout as delay } from 'node:timers/promises'

export default async function setup(config) {
  const probe = createServer()
  await new Promise((yes, no) => { probe.once('error', no); probe.listen(18963, '127.0.0.1', yes) })
  await new Promise((yes, no) => probe.close(error => error ? no(error) : yes()))
  const tempParent = await realpath(tmpdir())
  const taskRoot = await mkdtemp(join(tempParent, 'travel-browser-accounts-'))
  const backend = fileURLToPath(new URL('../../backend/', import.meta.url))
  const python = process.env.TRAVEL_E2E_PYTHON || fileURLToPath(new URL(
    process.platform === 'win32' ? '../../../.venv-analysis/Scripts/python.exe' : '../../../.venv-analysis/bin/python',
    import.meta.url))
  const serverArgs = config?.metadata?.guideImportFixture === true
    ? [fileURLToPath(new URL('./guide-import-backend.py',import.meta.url))]
    : config?.metadata?.workspaceFixture === true
    ? [fileURLToPath(new URL('./workspace-backend.py',import.meta.url))]
    : config?.metadata?.discoveryFixture === true
    ? [fileURLToPath(new URL('./discovery-backend.py',import.meta.url))]
    : config?.metadata?.planFixture === true
    ? [fileURLToPath(new URL('./plan-backend.py',import.meta.url))]
    : config?.metadata?.seedSpots === true
    ? [fileURLToPath(new URL('./spot-backend.py',import.meta.url))]
    : ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '18963', '--log-level', 'warning']
  const server = spawn(python, serverArgs, {
    cwd: backend, windowsHide: true,
    env: { ...process.env, TRAVEL_DATA_DIR: join(taskRoot, 'data'),
      JWT_SECRET: randomBytes(32).toString('hex'), ZHIPU_API_KEY: '', AMAP_API_KEY: '',
      AMAP_JS_KEY: '', AMAP_JS_SECURITY_CODE: '', PYTHONUNBUFFERED: '1' },
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  let logs = '', spawnError = null, exited = false
  server.stdout.on('data', chunk => { logs = (logs+chunk).slice(-6000) })
  server.stderr.on('data', chunk => { logs = (logs+chunk).slice(-6000) })
  server.on('error', error => { spawnError = error })
  const done = new Promise(resolveDone => server.once('close', () => { exited = true; resolveDone() }))
  async function cleanup() {
    if (!exited) { server.kill(); await Promise.race([done, delay(5000)]) }
    if (!exited) { server.kill('SIGKILL'); await done }
    // Only remove the exact generated directory under the verified temp parent.
    assert.equal(dirname(resolve(taskRoot)), tempParent)
    assert(basename(taskRoot).startsWith('travel-browser-accounts-'))
    await rm(taskRoot, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 })
  }
  try {
    const deadline = Date.now()+30000
    while (true) {
      if (spawnError) throw spawnError
      if (exited) throw new Error(`Isolated backend exited during startup: ${logs}`)
      try {
        const response = await fetch('http://127.0.0.1:18963/api/health', { signal: AbortSignal.timeout(1000) })
        if (response.ok) {
          // Give an occupied-port failure time to surface before trusting health.
          await delay(200)
          if (exited) throw new Error(`Isolated backend failed to bind: ${logs}`)
          break
        }
      } catch (error) { if (exited) throw error }
      if (Date.now() > deadline) throw new Error(`Isolated backend startup timed out: ${logs}`)
      await delay(100)
    }
    return cleanup
  } catch (error) { await cleanup(); throw error }
}
