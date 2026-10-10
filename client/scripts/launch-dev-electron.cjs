'use strict'

const path = require('node:path')
const fs = require('node:fs')
const { spawn, execSync } = require('node:child_process')
const { createConcurrently } = require('concurrently')
const waitOn = require('wait-on')
let electronPath = require('electron')
const { buildDesktopHost, readWindowsExecutableArch, targets } = require('./build-desktop-host.cjs')
const { prepareDevelopmentElectron } = require('./windows-dpi-manifest.cjs')

const DEV_SERVER_URL = 'http://127.0.0.1:5174'
delete process.env.ELECTRON_RUN_AS_NODE
process.env.XCURSOR_SIZE ||= '24'
process.env.SPIRITAGENT_DESKTOP_DEV_SERVER = DEV_SERVER_URL
const clientRoot = path.resolve(__dirname, '..')

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

// Windows 控制台默认代码页 936(GBK)，会把 UTF-8 中文显示成乱码；
// chcp 输出随系统语言本地化，不解析，无条件执行幂等切换。
// 必须共享当前控制台：windowsHide/CREATE_NO_WINDOW 会让 chcp 改不到真正用于显示的代码页。
function ensureUtf8Console() {
  if (process.platform !== 'win32') return
  try {
    execSync('chcp 65001 >nul', { shell: 'cmd.exe', stdio: ['ignore', 'ignore', 'ignore'], windowsHide: false })
  } catch {
    // 无法切换时保持现状，仅影响显示。
  }
}
ensureUtf8Console()

let child = null
let exitIntent = null
let debounceTimer = null
let restartTimer = null
let restartReady = false
let restartSent = false
let watcher = null
let tools = null
let stoppingTools = false
let shutdownCode = 0

async function finishShutdown() {
  if (stoppingTools) return
  stoppingTools = true
  clearTimeout(restartTimer)
  if (tools) {
    tools.commands.forEach(command => command.kill())
    const forceTimer = setTimeout(() => {
      tools.commands.forEach(command => command.kill('SIGKILL'))
    }, 5000)
    let stopTimer
    try {
      await Promise.race([
        // 工具主动停止的退出码不覆盖引发关闭的原因。
        tools.result.catch(() => {}),
        new Promise(resolve => {
          stopTimer = setTimeout(() => {
            console.error('[launch-dev-electron] build tools did not stop within 10s; processes may remain.')
            shutdownCode ||= 1
            resolve()
          }, 10_000)
        })
      ])
    } finally {
      clearTimeout(forceTimer)
      clearTimeout(stopTimer)
    }
  }
  process.exit(shutdownCode)
}

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
      const killer = spawn('taskkill', ['/pid', exitingChild.pid.toString(), '/f'], {
        stdio: 'inherit',
        windowsHide: true
      })
      killer.on('error', error => {
        console.warn('[launch-dev-electron] taskkill failed:', error.message)
        exitingChild.kill('SIGKILL')
      })
      killer.on('exit', code => {
        if (code !== 0 && child === exitingChild) exitingChild.kill('SIGKILL')
      })
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

function requestShutdown(reason, code = 0) {
  if (code !== 0) shutdownCode = code
  if (exitIntent === 'shutdown') {
    if (!child) void finishShutdown()
    return
  }
  exitIntent = 'shutdown'
  clearTimeout(debounceTimer)
  watcher?.close()
  console.log(`[launch-dev-electron] ${reason}: waiting for desktop restoration and normal exit...`)
  if (child) requestExit()
  else void finishShutdown()
}

process.on('SIGINT', () => requestShutdown('SIGINT'))
process.on('SIGTERM', () => requestShutdown('SIGTERM'))
process.on('SIGHUP', () => requestShutdown('SIGHUP'))

function spawnElectron() {
  if (exitIntent === 'shutdown') return
  child = spawn(electronPath, ['.'], {
    stdio: ['inherit', 'inherit', 'inherit', 'ipc'],
    env: process.env,
    cwd: clientRoot,
    shell: false,
    // 隔离终端取消信号；正常退出由 launcher 的 IPC 驱动。
    detached: true,
    windowsHide: true
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
    if (child !== runningChild) return
    console.error('[launch-dev-electron] failed to spawn electron:', err)
    if (!runningChild.pid) child = null
    requestShutdown('Electron failure', 1)
  })

  child.on('exit', (code, signal) => {
    if (child !== runningChild) return
    child = null
    clearTimeout(restartTimer)
    restartTimer = null
    if (exitIntent === 'restart') {
      exitIntent = null
      console.log('[launch-dev-electron] restarting electron...')
      spawnElectron()
    } else {
      requestShutdown('Electron exited', exitIntent === 'shutdown' ? 0 : (code ?? (signal ? 128 : 0)))
    }
  })
}

async function startDevelopment() {
  delete process.env.SPIRITAGENT_DESKTOP_DEV_PREPARATION_ERROR
  if (process.platform === 'win32') {
    try {
      const electronArch = readWindowsExecutableArch(electronPath)
      buildDesktopHost(targets[electronArch], { ifNeeded: true })
      electronPath = prepareDevelopmentElectron(electronPath)
    } catch (error) {
      process.env.SPIRITAGENT_DESKTOP_DEV_PREPARATION_ERROR = error.message
      console.warn(`[launch-dev-electron] 桌面模式尚未准备：${error.message}`)
      console.warn(
        '[launch-dev-electron] 继续启动窗口模式。准备好工具链后，在 client 目录运行 pnpm build:native，再重新启动 pnpm dev。'
      )
    }
  }

  if (exitIntent === 'shutdown') return
  const distElectronDir = path.join(clientRoot, 'dist-electron')
  fs.mkdirSync(distElectronDir, { recursive: true })
  watcher = fs.watch(distElectronDir, (_eventType, filename) => {
    if (exitIntent === 'shutdown') return
    if (['entry.js', 'preload.cjs', 'preload-background.cjs'].includes(filename)) {
      clearTimeout(debounceTimer)
      debounceTimer = setTimeout(requestRestart, 300)
    }
  })
  watcher.on('error', error => {
    console.error('[launch-dev-electron] watcher failed:', error.message)
    requestShutdown('watcher failure', 1)
  })

  // 不安装 concurrently 的 KillOnSignal：Electron 恢复完成后才停止构建工具。
  tools = createConcurrently(
    [
      { name: 'vite', command: 'vite --host 127.0.0.1 --port 5174 --strictPort' },
      { name: 'tsup', command: 'tsup --watch main --watch shared' }
    ],
    { raw: true, controllers: [], cwd: clientRoot }
  )
  void tools.result.catch(() => {})
  for (const command of tools.commands) {
    command.error.subscribe(error => {
      console.error(`[launch-dev-electron] ${command.name} failed:`, error)
      requestShutdown(`${command.name} failure`, 1)
    })
    command.close.subscribe(({ exitCode }) => {
      if (exitIntent !== 'shutdown') requestShutdown(`${command.name} exited (${exitCode})`, 1)
    })
  }

  await waitOn({
    resources: [path.join(distElectronDir, 'entry.js'), `${DEV_SERVER_URL.replace('http:', 'http-get:')}/sprite.html`],
    timeout: 60_000,
    httpTimeout: 5000
  })
  if (exitIntent !== 'shutdown') spawnElectron()
}

void startDevelopment().catch(error => {
  if (exitIntent === 'shutdown') return
  console.error('[launch-dev-electron] development startup failed:', error.message)
  requestShutdown('startup failure', 1)
})
