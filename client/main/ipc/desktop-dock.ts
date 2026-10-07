import { randomUUID } from 'node:crypto'
import { stat } from 'node:fs/promises'
import path from 'node:path'

import { app, type BrowserWindow, dialog, type IpcMain, shell, type WebContents } from 'electron'
import log from 'electron-log/main'

import { type DockCatalog, type DockEntry, type DockState, IPC } from '@ipc/contracts'

import type { RunningApplicationsState, RunningApplicationWindow } from '../shared/desktop-applications'
import { atomicWriteFile, createSerialQueue, safeReadJson, sendToWindow } from '../shared/utils'

import { type CatalogApplication, createWindowsAppCatalog } from './windows-app-catalog'
import { isPackagedAppTarget, launchPackagedApplication } from './windows-installed-apps'

interface SavedDockEntry {
  id: string
  target: string
  name: string
}

interface SavedDock {
  version: 1
  entries: SavedDockEntry[]
}

type DockSelection = Pick<SavedDockEntry, 'target' | 'name'>
type DockProjection = Pick<DockEntry, 'icon' | 'status' | 'error'>

interface RunningDockApplication {
  id: string
  key: string
  target: string | null
  name: string
  icon: string | null
  windows: RunningApplicationWindow[]
  enriched: boolean
}

function fileSelection(target: string): DockSelection {
  return { target, name: path.basename(target, path.extname(target)) }
}

function targetIdentity(target: string): string {
  if (path.extname(target).toLowerCase() === '.lnk') {
    try {
      return path.normalize(shell.readShortcutLink(target).target).toLowerCase()
    } catch {
      // 失效快捷方式保留原路径，仍可显示和修复。
    }
  }

  return isPackagedAppTarget(target) ? target.toLowerCase() : path.normalize(target).toLowerCase()
}

function sameApplicationTarget(first: string, second: string): boolean {
  if (first === second) {
    return true
  }

  if (
    !path.isAbsolute(first) ||
    !path.isAbsolute(second) ||
    path.extname(first) !== '.exe' ||
    path.basename(first) !== path.basename(second)
  ) {
    return false
  }

  // 同名启动器可能把实际窗口进程放在安装目录的子目录。
  const firstDirectory = `${path.dirname(first)}${path.sep}`
  const secondDirectory = `${path.dirname(second)}${path.sep}`

  return firstDirectory.startsWith(secondDirectory) || secondDirectory.startsWith(firstDirectory)
}

function isSavedEntry(raw: unknown): raw is SavedDockEntry {
  if (!raw || typeof raw !== 'object') {
    return false
  }

  const item = raw as Partial<SavedDockEntry>

  return (
    typeof item.id === 'string' &&
    typeof item.name === 'string' &&
    typeof item.target === 'string' &&
    (isPackagedAppTarget(item.target) ||
      (path.isAbsolute(item.target) && ['.exe', '.lnk'].includes(path.extname(item.target).toLowerCase())))
  )
}

async function validateFileTarget(target: string): Promise<void> {
  if (!path.isAbsolute(target) || !['.exe', '.lnk'].includes(path.extname(target).toLowerCase())) {
    throw new Error('请选择程序或程序快捷方式。')
  }

  if (!(await stat(target)).isFile()) {
    throw new Error('程序路径不是文件。')
  }

  if (path.extname(target).toLowerCase() === '.lnk') {
    const shortcut = shell.readShortcutLink(target)

    if (path.extname(shortcut.target).toLowerCase() !== '.exe' || !(await stat(shortcut.target)).isFile()) {
      throw new Error('快捷方式没有指向可用程序。')
    }
  }
}

// Windows 不解析 .lnk 图标，先读取目标程序，失败再回退。
async function readTargetIcon(target: string): Promise<string> {
  if (path.extname(target).toLowerCase() === '.lnk') {
    try {
      const shortcut = shell.readShortcutLink(target)

      if (path.extname(shortcut.target).toLowerCase() === '.exe') {
        return (await app.getFileIcon(shortcut.target, { size: 'large' })).toDataURL()
      }
    } catch {
      // 目标不可读时退回快捷方式本身。
    }
  }

  return (await app.getFileIcon(target, { size: 'large' })).toDataURL()
}

export function registerDesktopDock(options: {
  ipcMain: IpcMain
  userData: string
  isDesktopSender: (sender: WebContents) => boolean
  getDesktopWindow: () => BrowserWindow | null
  getRunningApplications: () => RunningApplicationsState
  onRunningApplicationsChanged: (listener: (state: RunningApplicationsState) => void) => () => void
  captureEligibility: (sender: WebContents) => () => boolean
  refreshApplications: (sender: WebContents) => Promise<void>
  activateExternal: (sender: WebContents, windowId: string) => Promise<void>
  closeExternal: (sender: WebContents, windowIds: string[]) => Promise<void>
}): void {
  const filename = path.join(options.userData, 'desktop-dock.json')
  const saved = safeReadJson<SavedDock>(filename)
  let entries = saved?.version === 1 && Array.isArray(saved.entries) ? saved.entries.filter(isSavedEntry) : []
  let revision = 0
  let pinnedRevision = 0
  const serial = createSerialQueue()
  const catalog = createWindowsAppCatalog()
  const projections = new Map<string, { identity: string; value: DockProjection | null }>()
  const running = new Map<string, RunningDockApplication>()
  let runtime = options.getRunningApplications()
  let runtimeSignature = ''
  let runtimeCatalog: Promise<CatalogApplication[]> | null = null
  let metadataTask: Promise<void> | null = null

  const assertSender = (sender: WebContents): void => {
    if (!options.isDesktopSender(sender) || process.platform !== 'win32') {
      throw new Error('Dock 仅允许桌面入口操作。')
    }
  }

  function findRunningTarget(identity: string): RunningDockApplication | undefined {
    const applications = [...running.values()].filter(item => sameApplicationTarget(identity, item.key))
    const application = applications[0]

    if (!application || applications.length === 1) {
      return application
    }

    return {
      ...application,
      windows: applications.flatMap(item => item.windows).sort((a, b) => b.lastActive - a.lastActive)
    }
  }

  function findRunningApplication(id: string): RunningDockApplication | undefined {
    const entry = entries.find(item => item.id === id)

    return entry
      ? findRunningTarget(projections.get(entry.target)?.identity ?? targetIdentity(entry.target))
      : [...running.values()].find(item => item.id === id)
  }

  function withRunningApplications(
    sender: WebContents,
    eligible: () => boolean,
    action: () => Promise<void>
  ): Promise<void> {
    return serial(async () => {
      assertSender(sender)

      if (!eligible()) {
        throw new Error('桌面已改变，请重新选择程序。')
      }

      await options.refreshApplications(sender)

      if (!eligible()) {
        throw new Error('桌面已改变，请重新选择程序。')
      }

      if (runtime.status !== 'ready') {
        throw new Error(runtime.error || '运行程序列表暂时不可用，请重试。')
      }

      await action()
    })
  }

  async function requirePackagedApplication(target: string): Promise<CatalogApplication> {
    const scan = await catalog.get()
    const identity = target.toLowerCase()
    const item = scan.items.find(application => application.target.toLowerCase() === identity)

    if (!item) {
      if (scan.sources.some(source => source.key === 'apps-folder' && !source.ok)) {
        throw new Error('Windows 应用列表读取失败，请重新扫描。')
      }

      throw Object.assign(new Error('程序已卸载或不可用，请重新选择程序。'), { code: 'ENOENT' })
    }

    return item
  }

  async function validateTarget(target: string): Promise<CatalogApplication | null> {
    if (isPackagedAppTarget(target)) {
      return requirePackagedApplication(target)
    }

    await validateFileTarget(target)

    return null
  }

  async function project(entry: SavedDockEntry): Promise<DockProjection> {
    let status: DockEntry['status'] = 'ready'
    let error: string | undefined
    let application: CatalogApplication | null = null

    try {
      application = await validateTarget(entry.target)
    } catch (cause) {
      status = (cause as NodeJS.ErrnoException).code === 'ENOENT' ? 'missing' : 'invalid'
      error = cause instanceof Error ? cause.message : '程序不可用。'
    }

    let icon: string | null = null

    if (status === 'ready') {
      try {
        icon = application
          ? ((await catalog.icons([application.id]))[application.id] ?? null)
          : await readTargetIcon(entry.target)
      } catch {
        /* 图标失败保留程序启动能力。 */
      }
    }

    return { icon, status, ...(error ? { error } : {}) }
  }

  function projection(entry: SavedDockEntry): DockProjection {
    let cached = projections.get(entry.target)

    if (!cached) {
      cached = { identity: targetIdentity(entry.target), value: null }
      projections.set(entry.target, cached)
      const current = cached
      void project(entry)
        .then(value => {
          if (projections.get(entry.target) === current) {
            current.value = value
            publish()
          }
        })
        .catch(error => log.warn('[Dock] application projection failed:', error))
    }

    return cached.value ?? { status: 'loading', icon: null }
  }

  function windowsFor(application: RunningDockApplication | undefined): DockEntry['windows'] {
    return (application?.windows ?? []).map(window => ({
      id: window.id,
      title: window.title || application?.name || '',
      minimized: window.minimized
    }))
  }

  function snapshot(): DockState {
    const registered: string[] = []

    const pinned = entries.map(entry => {
      const base = projection(entry)
      const identity = projections.get(entry.target)?.identity ?? entry.target.toLowerCase()
      const application = findRunningTarget(identity)
      registered.push(identity)
      const windows = windowsFor(application)

      return { ...base, id: entry.id, name: entry.name, running: windows.length > 0, windows, canPin: false }
    })

    return {
      revision,
      pinnedRevision,
      entries: pinned,
      runningEntries: [...running.values()]
        .filter(application => !registered.some(identity => sameApplicationTarget(identity, application.key)))
        .map(application => ({
          id: application.id,
          name: application.name,
          icon: application.icon,
          status: 'ready',
          running: true,
          windows: windowsFor(application),
          canPin:
            application.target !== null &&
            (isPackagedAppTarget(application.target) ||
              (path.isAbsolute(application.target) && path.extname(application.target).toLowerCase() === '.exe'))
        })),
      runningStatus: runtime.status,
      runningError: runtime.error
    }
  }

  function publish(): DockState {
    revision += 1
    const state = snapshot()
    sendToWindow(options.getDesktopWindow(), IPC.event.dockChanged, state)

    return state
  }

  function enrichRunning(): void {
    if (metadataTask) {
      return
    }

    const pending = [...running.values()].filter(application => !application.enriched)

    if (!pending.length) {
      return
    }

    runtimeCatalog ??= catalog.get().then(scan => scan.items)
    const scan = runtimeCatalog

    for (const application of pending) {
      application.enriched = true
    }

    const isCurrent = (application: RunningDockApplication): boolean =>
      runtimeCatalog === scan && running.get(application.key) === application && application.enriched

    metadataTask = (async () => {
      const items = new Map((await scan).map(item => [targetIdentity(item.target), item]))
      const catalogEntries = [...items]

      for (let index = 0; index < pending.length; index += 4) {
        let changed = false

        await Promise.all(
          pending.slice(index, index + 4).map(async application => {
            if (!isCurrent(application)) {
              return
            }

            const item =
              items.get(application.key) ??
              catalogEntries.find(([identity]) => sameApplicationTarget(identity, application.key))?.[1]

            let icon: string | null = null

            try {
              icon = item
                ? ((await catalog.icons([item.id]))[item.id] ?? null)
                : application.target && !isPackagedAppTarget(application.target)
                  ? await readTargetIcon(application.target)
                  : null
            } catch {
              /* 图标失败不影响窗口切换。 */
            }

            if (isCurrent(application)) {
              const name = item?.name || application.name

              if (application.name !== name || application.icon !== icon) {
                application.name = name
                application.icon = icon
                changed = true
              }
            }
          })
        )

        if (changed) {
          publish()
        }
      }
    })()
      .catch(error => log.warn('[Dock] running application metadata failed:', error))
      .finally(() => {
        if (runtimeCatalog !== scan) {
          for (const application of pending) {
            if (running.get(application.key) === application) {
              application.enriched = false
            }
          }
        }

        metadataTask = null
        enrichRunning()
      })
  }

  options.onRunningApplicationsChanged(next => {
    const signature = JSON.stringify(next)

    if (signature === runtimeSignature) {
      return
    }

    runtimeSignature = signature
    runtime = next

    if (next.status === 'inactive' || next.status === 'loading') {
      running.clear()
      runtimeCatalog = null
    } else {
      const seen = new Set<string>()

      for (const window of next.windows) {
        let application =
          running.get(window.appId) ?? [...running.values()].find(item => sameApplicationTarget(window.appId, item.key))

        if (!application) {
          application = {
            id: randomUUID(),
            key: window.appId,
            target: window.target,
            name: isPackagedAppTarget(window.target ?? '') ? window.title || window.name : window.name,
            icon: null,
            windows: [],
            enriched: false
          }
          running.set(window.appId, application)
        }

        if (!seen.has(application.key)) {
          application.windows = []
          seen.add(application.key)
        }

        application.windows.push(window)
      }

      for (const [key, application] of running) {
        if (!seen.has(key)) {
          running.delete(key)
        } else {
          application.windows.sort((a, b) => b.lastActive - a.lastActive)

          if (!application.windows.some(window => window.target === application.target)) {
            application.target = application.windows[0]?.target ?? null
            application.enriched = false
          }
        }
      }
    }

    publish()

    if (next.status === 'ready') {
      enrichRunning()
    }
  })

  async function commit(next: SavedDockEntry[], refreshTarget?: string): Promise<DockState> {
    const changed =
      next.length !== entries.length ||
      next.some((entry, index) => {
        const previous = entries[index]

        return !previous || entry.id !== previous.id || entry.name !== previous.name || entry.target !== previous.target
      })

    if (!changed && !refreshTarget) {
      return snapshot()
    }

    if (changed) {
      await atomicWriteFile(filename, JSON.stringify({ version: 1, entries: next }))
    }

    entries = next
    const targets = new Set(next.map(entry => entry.target))

    for (const target of projections.keys()) {
      if (!targets.has(target) || target === refreshTarget) {
        projections.delete(target)
      }
    }

    if (changed) {
      pinnedRevision += 1
    }

    return publish()
  }

  async function add(selections: DockSelection[]): Promise<DockState> {
    if (selections.length > 100) {
      throw new Error('一次最多添加 100 个程序。')
    }

    const next = [...entries]
    const registered = next.map(entry => targetIdentity(entry.target))

    for (const selection of selections) {
      await validateTarget(selection.target)
      const identity = targetIdentity(selection.target)

      if (!registered.some(target => sameApplicationTarget(target, identity))) {
        registered.push(identity)
        next.push({ id: randomUUID(), ...selection })
      }
    }

    return next.length === entries.length ? snapshot() : commit(next)
  }

  async function pickApplications(sender: WebContents, multiple: boolean): Promise<string[]> {
    assertSender(sender)
    const win = options.getDesktopWindow()

    if (!win) {
      throw new Error('桌面入口已关闭。')
    }

    const result = await dialog.showOpenDialog(win, {
      properties: multiple ? ['openFile', 'multiSelections'] : ['openFile'],
      filters: [{ name: '程序与快捷方式', extensions: ['exe', 'lnk'] }]
    })

    assertSender(sender)

    return result.canceled ? [] : result.filePaths
  }

  function resolveCatalogSelection(id: unknown): DockSelection {
    const item = typeof id === 'string' ? catalog.resolve(id) : null

    if (!item) {
      throw new Error('应用列表已更新，请重新选择。')
    }

    return { target: item.launchTarget, name: item.name }
  }

  async function catalogState(force: boolean): Promise<DockCatalog> {
    const scan = force ? await catalog.refresh() : await catalog.get()
    runtimeCatalog = Promise.resolve(scan.items)

    if (force) {
      for (const entry of entries) {
        projections.delete(entry.target)
      }

      for (const application of running.values()) {
        application.enriched = false
      }

      publish()
      enrichRunning()
    }

    const registered = entries.map(entry => targetIdentity(entry.target))

    return {
      revision: scan.revision,
      sources: scan.sources,
      // 仅限制下发列表；保存条目的校验仍使用完整扫描结果。
      items: scan.items.slice(0, 1000).map(item => {
        const identity = targetIdentity(item.target)

        return {
          id: item.id,
          name: item.name,
          detail: item.detail,
          inDock: registered.some(target => sameApplicationTarget(target, identity))
        }
      })
    }
  }

  function requireEntry(id: unknown): SavedDockEntry {
    const entry = typeof id === 'string' ? entries.find(item => item.id === id) : undefined

    if (!entry) {
      throw new Error('Dock 项目不存在。')
    }

    return entry
  }

  async function repairTo(entry: SavedDockEntry, selection: DockSelection): Promise<DockState> {
    await validateTarget(selection.target)
    const identity = targetIdentity(selection.target)

    if (entries.some(item => item.id !== entry.id && sameApplicationTarget(targetIdentity(item.target), identity))) {
      throw new Error('该程序已在 Dock 中。')
    }

    return commit(
      entries.map(item => (item === entry ? { ...entry, ...selection } : item)),
      selection.target
    )
  }

  options.ipcMain.handle(IPC.invoke.dockGetState, event => {
    assertSender(event.sender)

    return serial(snapshot)
  })
  options.ipcMain.handle(IPC.invoke.dockCatalog, (event, force: unknown) => {
    assertSender(event.sender)

    return serial(() => catalogState(force === true))
  })
  // 图标读取不进串行队列：批量读取耗时长，不应阻塞启动与增删排；期间重扫只会让个别条目回落默认图标。
  options.ipcMain.handle(IPC.invoke.dockCatalogIcons, (event, raw: unknown) => {
    assertSender(event.sender)

    if (!Array.isArray(raw) || !raw.every(item => typeof item === 'string')) {
      throw new Error('无效应用列表。')
    }

    return catalog.icons(raw)
  })
  options.ipcMain.handle(IPC.invoke.dockAddFromCatalog, (event, raw: unknown) => {
    assertSender(event.sender)

    if (!Array.isArray(raw) || !raw.every(item => typeof item === 'string')) {
      throw new Error('无效应用列表。')
    }

    return serial(async () => {
      assertSender(event.sender)
      const selections = raw.map(resolveCatalogSelection)

      return selections.length ? add(selections) : snapshot()
    })
  })
  options.ipcMain.handle(IPC.invoke.dockAddFromFiles, event => {
    assertSender(event.sender)

    return serial(async () => {
      const paths = await pickApplications(event.sender, true)

      return paths.length ? add(paths.map(fileSelection)) : snapshot()
    })
  })
  options.ipcMain.handle(IPC.invoke.dockRepairWithCatalog, (event, entryId: unknown, catalogId: unknown) => {
    assertSender(event.sender)

    return serial(async () => {
      assertSender(event.sender)

      return repairTo(requireEntry(entryId), resolveCatalogSelection(catalogId))
    })
  })
  options.ipcMain.handle(IPC.invoke.dockRepairWithFiles, (event, entryId: unknown) => {
    assertSender(event.sender)

    return serial(async () => {
      const entry = requireEntry(entryId)
      const [target] = await pickApplications(event.sender, false)

      return target ? repairTo(entry, fileSelection(target)) : snapshot()
    })
  })
  options.ipcMain.handle(IPC.invoke.dockAddDropped, (event, raw: unknown) => {
    assertSender(event.sender)

    if (!Array.isArray(raw) || !raw.every(item => typeof item === 'string' && !isPackagedAppTarget(item))) {
      throw new Error('无效程序文件。')
    }

    return serial(() => {
      assertSender(event.sender)

      return add(raw.map(fileSelection))
    })
  })
  options.ipcMain.handle(IPC.invoke.dockActivate, (event, id: unknown, windowId: unknown) => {
    assertSender(event.sender)
    const eligible = options.captureEligibility(event.sender)

    if (typeof id !== 'string' || (windowId !== undefined && typeof windowId !== 'string')) {
      throw new Error('无效程序或窗口。')
    }

    return withRunningApplications(event.sender, eligible, async () => {
      const application = findRunningApplication(id)

      const window =
        windowId === undefined ? application?.windows[0] : application?.windows.find(item => item.id === windowId)

      if (window) {
        await options.activateExternal(event.sender, window.id)

        return
      }

      const entry = entries.find(item => item.id === id)

      if (windowId !== undefined || !entry) {
        throw new Error('窗口已关闭，请重新选择程序。')
      }

      try {
        await validateTarget(entry.target)

        if (!eligible()) {
          throw new Error('桌面已改变，请重新选择程序。')
        }

        if (isPackagedAppTarget(entry.target)) {
          await launchPackagedApplication(entry.target)
        } else {
          const error = await shell.openPath(entry.target)

          if (error) {
            throw new Error(error)
          }
        }
      } catch (error) {
        // 移动或卸载后刷新状态，保留修复入口。
        if (isPackagedAppTarget(entry.target)) {
          await catalog.refresh()
        }

        projections.delete(entry.target)
        publish()
        throw error
      }
    })
  })
  options.ipcMain.handle(IPC.invoke.dockCloseWindows, (event, id: unknown, windowIds: unknown) => {
    assertSender(event.sender)
    const eligible = options.captureEligibility(event.sender)

    if (
      typeof id !== 'string' ||
      !Array.isArray(windowIds) ||
      !windowIds.length ||
      !windowIds.every(item => typeof item === 'string') ||
      new Set(windowIds).size !== windowIds.length
    ) {
      throw new Error('无效程序或窗口。')
    }

    return withRunningApplications(event.sender, eligible, async () => {
      const application = findRunningApplication(id)
      const currentIds = new Set(application?.windows.map(item => item.id))

      if (!windowIds.every(windowId => currentIds.has(windowId))) {
        throw new Error('窗口已关闭或程序已改变，请重新选择。')
      }

      await options.closeExternal(event.sender, windowIds)
    })
  })
  options.ipcMain.handle(IPC.invoke.dockPin, (event, runningId: unknown, beforeEntryId: unknown) => {
    assertSender(event.sender)
    const eligible = options.captureEligibility(event.sender)

    if (typeof runningId !== 'string' || (beforeEntryId !== undefined && typeof beforeEntryId !== 'string')) {
      throw new Error('无效程序或固定位置。')
    }

    return serial(async () => {
      assertSender(event.sender)
      await options.refreshApplications(event.sender)
      const application = [...running.values()].find(item => item.id === runningId)

      if (!application || !application.target || runtime.status !== 'ready') {
        throw new Error('程序已关闭或无法识别启动目标，请从应用列表选择。')
      }

      if (entries.some(entry => sameApplicationTarget(targetIdentity(entry.target), application.key))) {
        return snapshot()
      }

      const scan = await catalog.get()
      const item = scan.items.find(item => sameApplicationTarget(targetIdentity(item.target), application.key))

      const selection = item
        ? { target: item.launchTarget, name: item.name }
        : { target: application.target, name: application.name }

      await validateTarget(selection.target)

      if (!eligible() || running.get(application.key) !== application) {
        throw new Error('桌面或程序已改变，请重新选择。')
      }

      const index =
        beforeEntryId === undefined ? entries.length : entries.findIndex(entry => entry.id === beforeEntryId)

      if (index < 0) {
        throw new Error('固定项目已改变，请重新拖动。')
      }

      const next = [...entries]
      next.splice(index, 0, { id: randomUUID(), ...selection })

      return commit(next)
    })
  })
  options.ipcMain.handle(IPC.invoke.dockReorder, (event, raw: unknown) => {
    assertSender(event.sender)

    return serial(() => {
      assertSender(event.sender)

      if (!Array.isArray(raw) || raw.length !== entries.length || new Set(raw).size !== raw.length) {
        throw new Error('无效排序。')
      }

      const next = raw.map(id => entries.find(entry => entry.id === id))

      if (next.some(item => item === undefined)) {
        throw new Error('Dock 项目已改变。')
      }

      return commit(next.filter((item): item is SavedDockEntry => item !== undefined))
    })
  })
  options.ipcMain.handle(IPC.invoke.dockRemove, (event, id: unknown) => {
    assertSender(event.sender)

    return serial(() => {
      assertSender(event.sender)

      return commit(entries.filter(entry => entry.id !== id))
    })
  })
}
