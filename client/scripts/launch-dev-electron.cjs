'use strict'

const path = require('node:path')
const fs = require('node:fs')
const { spawn } = require('node:child_process')
const electronPath = require('electron')

delete process.env.ELECTRON_RUN_AS_NODE

// Auto-wire the local Runner so dev mode gets local tools without manually
// setting SPIRITAGENT_DESKTOP_PYTHON every session.
const repoRoot = path.resolve(__dirname, '..', '..')
const venvRoot = path.join(repoRoot, 'runner', '.venv')
const venvPython =
  process.platform === 'win32' ? path.join(venvRoot, 'Scripts', 'python.exe') : path.join(venvRoot, 'bin', 'python')

if (!process.env.SPIRITAGENT_DESKTOP_PYTHON && fs.existsSync(venvPython)) {
  process.env.SPIRITAGENT_DESKTOP_PYTHON = venvPython
}
if (!process.env.SPIRITAGENT_DESKTOP_RUNNER_REPO_ROOT && fs.existsSync(path.join(repoRoot, 'runner', 'server.py'))) {
  process.env.SPIRITAGENT_DESKTOP_RUNNER_REPO_ROOT = repoRoot
}

let child = null
let isRestarting = false
let debounceTimer = null

function spawnElectron() {
  child = spawn(electronPath, ['.'], {
    stdio: 'inherit',
    env: process.env,
    shell: true
  })

  child.on('error', err => {
    console.error('[launch-dev-electron] failed to spawn electron:', err)
    process.exit(1)
  })

  child.on('exit', (code, signal) => {
    if (isRestarting) {
      isRestarting = false
      console.log('[launch-dev-electron] restarting electron...')
      spawnElectron()
    } else {
      process.exit(code ?? (signal ? 128 : 0))
    }
  })
}

// Watch dist-electron/ for rebuilds by tsup --watch
const distElectronDir = path.join(__dirname, '..', 'dist-electron')
if (!fs.existsSync(distElectronDir)) {
  fs.mkdirSync(distElectronDir, { recursive: true })
}

try {
  fs.watch(distElectronDir, (_eventType, filename) => {
    if (filename && (filename.includes('entry.js') || filename.includes('preload.cjs'))) {
      if (debounceTimer) clearTimeout(debounceTimer)
      debounceTimer = setTimeout(() => {
        if (child && !isRestarting) {
          isRestarting = true
          console.log('[launch-dev-electron] main bundle rebuilt, restarting...')
          if (process.platform === 'win32') {
            spawn('taskkill', ['/pid', child.pid.toString(), '/f', '/t'], { shell: true })
          } else {
            child.kill('SIGTERM')
          }
        }
      }, 300)
    }
  })
} catch (err) {
  console.warn('[launch-dev-electron] watcher init error:', err.message)
}

spawnElectron()
