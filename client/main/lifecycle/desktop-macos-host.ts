import { execFile } from 'node:child_process'
import { realpath, stat } from 'node:fs/promises'
import path from 'node:path'
import { promisify } from 'node:util'

import { app, BrowserWindow, screen } from 'electron'

import type { RunningApplicationWindow } from '../shared/desktop-applications'
import type { DesktopHost, DesktopHostOptions } from '../shared/desktop-host'
import { desktopMacos, desktopMacosAvailability } from '../shared/desktop-macos'

const runFile = promisify(execFile)

export function createMacosDesktopHost(options: DesktopHostOptions): DesktopHost {
  let generation = 0
  let interaction = 0
  let bounds: Electron.Rectangle | null = null
  let overlay: BrowserWindow | null = null
  let timer: ReturnType<typeof setInterval> | undefined
  let querying: Promise<void> | null = null
  let stateSignature = ''
  let areaSignature = ''
  let appsSignature = ''
  let hiddenForFullscreen = false
  let lastPermission: boolean | undefined
  let lastWindows: RunningApplicationWindow[] = []

  const availability = (): ReturnType<DesktopHost['availability']> => {
    const status = desktopMacosAvailability()

    return { supported: status.available, reason: status.reason }
  }

  const accessibility = (): ReturnType<DesktopHost['accessibility']> => ({
    granted: availability().supported && desktopMacos().accessibilityStatus(),
    requestable: availability().supported
  })

  function windowForHandle(handle: Buffer): BrowserWindow | null {
    return (
      BrowserWindow.getAllWindows().find(win => !win.isDestroyed() && win.getNativeWindowHandle().equals(handle)) ??
      null
    )
  }

  function currentArea(): Electron.Rectangle {
    if (!bounds) {
      throw new Error('桌面模式已结束。')
    }

    return screen.getDisplayMatching(bounds).workArea
  }

  function reportApplications(windows: RunningApplicationWindow[], error: string | null = null): void {
    if (!error) {
      lastWindows = windows
    }

    const state = { status: error ? ('unavailable' as const) : ('ready' as const), error, windows }
    const signature = JSON.stringify(state)

    if (signature !== appsSignature) {
      appsSignature = signature
      options.onApplicationsChanged?.(state)
    }
  }

  function refreshApplications(eligible: () => boolean): Promise<void> {
    if (querying) {
      return querying
    }

    const epoch = generation
    const target = bounds

    if (!target || !eligible()) {
      return Promise.resolve()
    }

    if (!accessibility().granted) {
      lastWindows = []
      reportApplications([], '需要在系统设置的“隐私与安全性 → 辅助功能”中允许唤生，才能查看和操作运行窗口。')

      return Promise.resolve()
    }

    const task = desktopMacos()
      .listWindows(target)
      .then(windows => {
        if (epoch === generation && bounds === target && eligible()) {
          reportApplications(windows)
        }
      })
      .catch(error => {
        if (epoch === generation && bounds === target && eligible()) {
          reportApplications(lastWindows, error instanceof Error ? error.message : String(error))
        }
      })
      .finally(() => {
        if (querying === task) {
          querying = null
        }
      })

    querying = task

    return task
  }

  function poll(): void {
    if (!bounds || !overlay || overlay.isDestroyed()) {
      return
    }

    try {
      const state = desktopMacos().surfaceState(bounds)
      const signature = JSON.stringify(state)

      if (state.fullscreen && overlay.isVisible()) {
        hiddenForFullscreen = true
        overlay.hide()
      } else if (!state.fullscreen && hiddenForFullscreen) {
        hiddenForFullscreen = false
        overlay.showInactive()
      }

      if (stateSignature !== signature) {
        stateSignature = signature
        options.onForegroundChanged?.(state)
      }

      const area = currentArea()
      const nextAreaSignature = JSON.stringify(area)

      if (areaSignature !== nextAreaSignature) {
        areaSignature = nextAreaSignature
        options.onWorkAreaChanged?.(area)
      }

      const granted = accessibility().granted

      if (lastPermission !== granted) {
        lastPermission = granted
        // 状态变化也触发呈现快照，使系统设置中授予/撤销权限立即反映到界面。
        options.onForegroundChanged?.(state)
      }

      void refreshApplications(() => bounds !== null)
    } catch (error) {
      options.onFailure(`macOS 桌面状态读取失败：${error instanceof Error ? error.message : String(error)}`)
    }
  }

  function stop(): Promise<void> {
    generation += 1
    interaction += 1
    clearInterval(timer)
    timer = undefined
    bounds = null
    overlay = null
    querying = null
    hiddenForFullscreen = false
    stateSignature = ''
    areaSignature = ''
    appsSignature = ''
    lastPermission = undefined
    lastWindows = []

    if (desktopMacosAvailability().available) {
      desktopMacos().reset()
    }

    app.removeListener('activate', poll)
    app.removeListener('browser-window-focus', poll)

    return Promise.resolve()
  }

  function assertOperation(epoch: number, eligible: () => boolean): Electron.Rectangle {
    if (epoch !== generation || !bounds || !eligible()) {
      throw new Error('桌面已改变，请重新选择程序。')
    }

    if (!accessibility().granted) {
      throw new Error('请先在系统设置中授予唤生辅助功能权限。')
    }

    return bounds
  }

  return {
    availability,
    accessibility,
    requestAccessibility: () => {
      desktopMacos().requestAccessibility()
      poll()
    },
    start: async input => {
      if (bounds || input.parentPid !== process.pid || input.windows.length !== 2) {
        throw new Error('桌面窗口状态无效。')
      }

      const background = input.windows.find(win => win.role === 'background')
      const interactive = input.windows.find(win => win.role === 'overlay')

      if (!background || !interactive || JSON.stringify(background.bounds) !== JSON.stringify(interactive.bounds)) {
        throw new Error('桌面模式需要同一显示器上的背景与交互窗口。')
      }

      overlay = windowForHandle(interactive.handle)

      if (!overlay || !windowForHandle(background.handle)) {
        throw new Error('桌面窗口已失效。')
      }

      desktopMacos().configureSurfaces(background.handle, interactive.handle)
      generation += 1
      bounds = interactive.bounds
      options.onSystemTrayChanged?.(null)
      poll()
      timer = setInterval(poll, 1_000)
      timer.unref()
      app.on('activate', poll)
      app.on('browser-window-focus', poll)
      await refreshApplications(() => bounds !== null)
    },
    stop,
    recover: async () => false,
    setPalette: async () => {},
    setTrayLayout: async () => {},
    focus: async (handle, eligible = () => true) => {
      const win = windowForHandle(handle)

      if (!bounds || !win || win !== overlay || !eligible()) {
        return false
      }

      app.focus({ steal: true })
      win.focus()
      win.moveTop()
      poll()

      return win.isFocused()
    },
    refreshApplications,
    cancelExternalInteraction: async () => {
      interaction += 1
      // 已进入系统的 AX 请求不能撤回；取消只隔离后续步骤并保留当前窗口身份。
      desktopMacos().cancelOperations()
    },
    activateExternal: async (id, allowRemote, eligible, continuation = eligible) => {
      const epoch = generation
      const target = assertOperation(epoch, eligible)
      const windows = await desktopMacos().listWindows(target)
      const win = windows.find(window => window.id === id)

      assertOperation(epoch, eligible)

      if (!win || (!win.onDesktopDisplay && !allowRemote)) {
        throw new Error('窗口已关闭、切换空间或不在交互屏，请重新选择。')
      }

      await desktopMacos().activateWindow(id, win.onDesktopDisplay ? null : currentArea())

      if (epoch === generation && continuation()) {
        await refreshApplications(continuation)
      }
    },
    launchExternal: async (target, _appId, eligible, continuation = eligible) => {
      const epoch = generation
      const operation = interaction

      const current = (): boolean =>
        epoch === generation && operation === interaction && bounds !== null && continuation()

      const canonical = await realpath(target)

      if (path.extname(canonical).toLowerCase() !== '.app' || !(await stat(canonical)).isDirectory()) {
        throw new Error('请选择可用的 macOS 应用。')
      }

      await desktopMacos().applicationInfo([canonical])

      if (!current() || !eligible()) {
        throw new Error('桌面已改变，请重新启动程序。')
      }

      await runFile('/usr/bin/open', ['-a', canonical], { timeout: 5_000, maxBuffer: 65_536 })

      if (!current() || !accessibility().granted) {
        return
      }

      // 启动回执不代表窗口已经出现；有界等待且在用户继续操作后停止定位。
      const deadline = Date.now() + 5_000

      while (current() && Date.now() < deadline) {
        const windows = await desktopMacos().listWindows(bounds!)

        const win = windows
          .filter(window => window.target === canonical)
          .sort((a, b) => Number(b.onDesktopDisplay) - Number(a.onDesktopDisplay) || b.lastActive - a.lastActive)[0]

        if (win && current()) {
          await desktopMacos().activateWindow(win.id, win.onDesktopDisplay ? null : currentArea())

          return
        }

        await new Promise(resolve => setTimeout(resolve, 200))
      }

      if (current()) {
        throw new Error('启动请求已提交，但未能确认应用窗口及其位置。')
      }
    },
    closeExternal: async (ids, eligible) => {
      const epoch = generation
      const target = assertOperation(epoch, eligible)
      const windows = await desktopMacos().listWindows(target)

      assertOperation(epoch, eligible)

      if (!ids.length || ids.some(id => !windows.some(win => win.id === id && win.onDesktopDisplay))) {
        throw new Error('只能关闭交互屏中仍然存在的窗口。')
      }

      await desktopMacos().closeWindows(ids)
      await refreshApplications(() => epoch === generation && eligible())
    },
    getJumpListTasks: async () => [],
    activateJumpListTask: async () => {
      throw new Error('当前系统不提供程序任务菜单。')
    }
  }
}
