'use strict'

const path = require('node:path')
const fs = require('node:fs')
const { spawn } = require('node:child_process')
let electronPath = require('electron')
const { buildDesktopHost, readWindowsExecutableArch, targets } = require('./build-desktop-host.cjs')
const { prepareDevelopmentElectron } = require('./windows-dpi-manifest.cjs')

delete process.env.ELECTRON_RUN_AS_NODE

if (process.platform === 'win32') {
  try {
    const electronArch = readWindowsExecutableArch(electronPath)
    buildDesktopHost(targets[electronArch], { ifNeeded: true })
    electronPath = prepareDevelopmentElectron(electronPath)
  } catch (error) {
    console.warn(`[launch-dev-electron] 桌面模式尚未准备：${error.message}`)
    console.warn(
      '[launch-dev-electron] 继续启动窗口模式。准备好工具链后，在 client 目录运行 pnpm build:native，再重新启动 pnpm dev。'
    )
  }
}

// 开发时优先使用仓库 Runner，保留显式环境配置。
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
let exitIntent = null
let debounceTimer = null
let restartTimer = null
let restartReady = false
let restartSent = false
let watcher = null

function sendRestartRequest() {
  if (!child || !exitIntent || !restartReady || restartSent) return
  restartSent = true
  child.send({ command: 'spiritagent:dev-restart' }, error => {
    if (error) console.warn('[launch-dev-electron] graceful restart request failed:', error.message)
  })
}

function requestExit() {
  const exitingChild = child
  clearTimeout(restartTimer)
  restartTimer = setTimeout(() => {
    if (child !== exitingChild || !exitIntent) return
    console.warn(
      '[launch-dev-electron] normal exit exceeded 60s; forcing only Electron main to stop. Desktop recovery remains with guardian.'
    )
    if (process.platform === 'win32') {
      spawn('taskkill', ['/pid', exitingChild.pid.toString(), '/f'], { stdio: 'inherit', windowsHide: true })
    } else {
      exitingChild.kill('SIGKILL')
    }
  }, 60_000)
  sendRestartRequest()
}

function requestRestart() {
  if (!child || exitIntent) return
  exitIntent = 'restart'
  console.log('[launch-dev-electron] main bundle rebuilt, waiting for desktop restoration and normal exit...')
  requestExit()
}

function requestShutdown(signal) {
  if (exitIntent === 'shutdown') return
  exitIntent = 'shutdown'
  clearTimeout(debounceTimer)
  watcher?.close()
  console.log(`[launch-dev-electron] ${signal}: waiting for desktop restoration and normal exit...`)
  if (child) requestExit()
  else process.exit(0)
}

process.once('SIGINT', () => requestShutdown('SIGINT'))
process.once('SIGTERM', () => requestShutdown('SIGTERM'))

function spawnElectron() {
  child = spawn(electronPath, ['.'], {
    stdio: ['inherit', 'inherit', 'inherit', 'ipc'],
    env: process.env,
    shell: false
  })
  const runningChild = child
  restartReady = false
  restartSent = false

  child.on('message', message => {
    if (child !== runningChild) return
    if (message?.event === 'spiritagent:dev-ready') {
      restartReady = true
      sendRestartRequest()
    }
  })

  child.on('error', err => {
    clearTimeout(restartTimer)
    console.error('[launch-dev-electron] failed to spawn electron:', err)
    process.exit(1)
  })

  child.on('exit', (code, signal) => {
    clearTimeout(restartTimer)
    restartTimer = null
    if (exitIntent === 'restart') {
      exitIntent = null
      console.log('[launch-dev-electron] restarting electron...')
      spawnElectron()
    } else {
      process.exit(exitIntent === 'shutdown' ? 0 : (code ?? (signal ? 128 : 0)))
    }
  })
}

const distElectronDir = path.join(__dirname, '..', 'dist-electron')
if (!fs.existsSync(distElectronDir)) {
  fs.mkdirSync(distElectronDir, { recursive: true })
}

try {
  watcher = fs.watch(distElectronDir, (_eventType, filename) => {
    if (exitIntent === 'shutdown') return
    if (['entry.js', 'preload.cjs', 'preload-background.cjs'].includes(filename)) {
      if (debounceTimer) clearTimeout(debounceTimer)
      debounceTimer = setTimeout(requestRestart, 300)
    }
  })
} catch (err) {
  console.warn('[launch-dev-electron] watcher init error:', err.message)
}

spawnElectron()
