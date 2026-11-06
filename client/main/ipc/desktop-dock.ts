import { randomUUID } from 'node:crypto'
import path from 'node:path'

import { type BrowserWindow, dialog, type IpcMain, type WebContents } from 'electron'
import log from 'electron-log/main'

import { type DockCatalog, type DockEntry, type DockState, IPC } from '@ipc/contracts'

import { CLIENT_LIMITS } from '../../shared/constants'
import type { ApplicationEligibility, RunningApplicationsState } from '../shared/desktop-applications'
import type { DesktopJumpListTask } from '../shared/desktop-jump-list'
import { desktopMacos } from '../shared/desktop-macos'
import { createSerialQueue, sendToWindow } from '../shared/utils'

import { type CatalogApplication, createDesktopAppCatalog } from './desktop-app-catalog'
import { catalogSnapshot } from './dock/catalog'
import { applicationIcon } from './dock/icons'
import { activateRunningApplication, launchApplication, projectTasks } from './dock/launch'
import {
  type DockSelection,
  fileSelection,
  loadSavedEntries,
  sameApplicationTarget,
  type SavedDockEntry,
  saveEntries,
  targetIdentity,
  validatedSelection,
  validateFileTarget
} from './dock/registry'
import { createRunningRegistry, type RunningDockApplication, windowsFor } from './dock/running'
import { isMacosApplicationTarget, validateMacosApplicationTarget } from './macos-application-target'
import { type InstalledApplication, isPackagedAppTarget } from './windows-installed-apps'

type DockProjection = Pick<DockEntry, 'icon' | 'status' | 'error'>

export function registerDesktopDock(options: {
  ipcMain: IpcMain
  userData: string
  isDesktopSender: (sender: WebContents) => boolean
  getDesktopWindow: () => BrowserWindow | null
  getRunningApplications: () => RunningApplicationsState
  onRunningApplicationsChanged: (listener: (state: RunningApplicationsState) => void) => () => void
  captureEligibility: (sender: WebContents, interaction?: boolean) => ApplicationEligibility
  refreshApplications: (sender: WebContents) => Promise<void>
  activateExternal: (
    sender: WebContents,
    windowId: string,
    allowRemote: boolean,
    eligible: ApplicationEligibility
  ) => Promise<void>
  launchExternal: (
    sender: WebContents,
    target: string,
    appId: string,
    eligible: ApplicationEligibility
  ) => Promise<void>
  closeExternal: (sender: WebContents, windowIds: string[], eligible: ApplicationEligibility) => Promise<void>
  getJumpListTasks: (sender: WebContents, target: string) => Promise<DesktopJumpListTask[]>
  activateJumpListTask: (
    sender: WebContents,
    target: string,
    taskId: string,
    eligible: ApplicationEligibility
  ) => Promise<void>
}): void {
  let entries = loadSavedEntries(options.userData)
  let revision = 0
  let pinnedRevision = 0
  const serial = createSerialQueue()
  const taskReadSerial = createSerialQueue()
  const catalog = createDesktopAppCatalog()
  const projections = new Map<string, { identity: string; value: DockProjection | null }>()
  const running = createRunningRegistry()

  running.apply(options.getRunningApplications())
  let runtimeCatalog: Promise<CatalogApplication[]> | null = null
  let metadataTask: Promise<void> | null = null

  const assertSender = (sender: WebContents): void => {
    if (!options.isDesktopSender(sender) || !['win32', 'darwin'].includes(process.platform)) {
      throw new Error('Dock 仅允许桌面入口操作。')
    }
  }

  function findRunningApplication(id: string): RunningDockApplication | undefined {
    const entry = entries.find(item => item.id === id)

    return entry
      ? running.findTarget(projections.get(entry.target)?.identity ?? targetIdentity(entry.target))
      : running.values().find(item => item.id === id)
  }

  function withRunningApplications(
    sender: WebContents,
    eligible: ApplicationEligibility,
    action: () => Promise<void>,
    allowTrustedLaunch = false
  ): Promise<void> {
    return serial(async () => {
      assertSender(sender)

      if (!eligible.current()) {
        throw new Error('桌面已改变，请重新选择程序。')
      }

      await options.refreshApplications(sender)

      if (!eligible.current()) {
        throw new Error('桌面已改变，请重新选择程序。')
      }

      const state = running.state()

      const launchWithoutWindows =
        allowTrustedLaunch &&
        process.platform === 'darwin' &&
        state.status === 'unavailable' &&
        !desktopMacos().accessibilityStatus()

      if (state.status !== 'ready' && !launchWithoutWindows) {
        throw new Error(state.error || '运行程序列表暂时不可用，请重试。')
      }

      await action()
    })
  }

  async function requirePackagedApplication(target: string): Promise<InstalledApplication> {
    let item: InstalledApplication | null

    try {
      item = await catalog.findPackagedApplication(target)
    } catch (cause) {
      log.warn('[Dock] apps-folder application verification failed:', cause)
      throw new Error('Windows 应用列表读取失败，请重新扫描。', { cause })
    }

    if (!item) {
      throw Object.assign(new Error('程序已卸载或不可用，请重新选择程序。'), { code: 'ENOENT' })
    }

    return item
  }

  async function validateTarget(target: string): Promise<InstalledApplication | null> {
    if (process.platform === 'darwin') {
      await validateMacosApplicationTarget(target)

      return null
    }

    if (isPackagedAppTarget(target)) {
      return requirePackagedApplication(target)
    }

    await validateFileTarget(target)

    return null
  }

  async function project(entry: SavedDockEntry): Promise<DockProjection> {
    let status: DockEntry['status'] = 'ready'
    let error: string | undefined
    let application: InstalledApplication | null = null

    try {
      application = await validateTarget(entry.target)
    } catch (cause) {
      status = (cause as NodeJS.ErrnoException).code === 'ENOENT' ? 'missing' : 'invalid'
      error = cause instanceof Error ? cause.message : '程序不可用。'
    }

    let icon: string | null = null

    if (status === 'ready') {
      try {
        icon = await applicationIcon(entry.target, application?.iconPath)
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

  function snapshot(): DockState {
    const registered: string[] = []

    const pinned = entries.map(entry => {
      const base = projection(entry)
      const identity = projections.get(entry.target)?.identity ?? targetIdentity(entry.target)
      const application = running.findTarget(identity)
      registered.push(identity)
      const windows = windowsFor(application)

      return { ...base, id: entry.id, name: entry.name, running: windows.length > 0, windows, canPin: false }
    })

    return {
      revision,
      pinnedRevision,
      entries: pinned,
      runningEntries: running
        .values()
        .filter(
          application =>
            application.windows.some(window => window.onDesktopDisplay) &&
            !registered.some(identity => sameApplicationTarget(identity, application.key))
        )
        .map(application => ({
          id: application.id,
          name: application.name,
          icon: application.icon,
          status: 'ready',
          running: true,
          windows: windowsFor(application),
          canPin:
            application.target !== null &&
            (process.platform === 'darwin'
              ? isMacosApplicationTarget(application.target)
              : isPackagedAppTarget(application.target) ||
                (path.isAbsolute(application.target) && path.extname(application.target).toLowerCase() === '.exe'))
        })),
      runningStatus: running.state().status,
      runningError: running.state().error
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

    const pending = running.values().filter(application => !application.enriched)

    if (!pending.length) {
      return
    }

    runtimeCatalog ??= catalog.get().then(scan => scan.items)
    const scan = runtimeCatalog

    for (const application of pending) {
      application.enriched = true
    }

    const isCurrent = (application: RunningDockApplication): boolean =>
      runtimeCatalog === scan && running.getByKey(application.key) === application && application.enriched

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
                  ? await applicationIcon(application.target)
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
            if (running.getByKey(application.key) === application) {
              application.enriched = false
            }
          }
        }

        metadataTask = null
        enrichRunning()
      })
  }

  options.onRunningApplicationsChanged(next => {
    if (!running.apply(next)) {
      return
    }

    if (next.status === 'inactive' || next.status === 'loading') {
      runtimeCatalog = null
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
      await saveEntries(options.userData, next)
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
    if (selections.length > CLIENT_LIMITS.maxDockSelections) {
      throw new Error(`一次最多添加 ${CLIENT_LIMITS.maxDockSelections} 个程序。`)
    }

    const next = [...entries]
    const registered = next.map(entry => targetIdentity(entry.target))

    for (const original of selections) {
      const selection = await validatedSelection(original)
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

    const result = await dialog.showOpenDialog(
      win,
      process.platform === 'darwin'
        ? {
            defaultPath: '/Applications',
            properties: multiple ? ['openFile', 'multiSelections'] : ['openFile'],
            filters: [{ name: '应用程序', extensions: ['app'] }]
          }
        : {
            properties: multiple ? ['openFile', 'multiSelections'] : ['openFile'],
            filters: [{ name: '程序与快捷方式', extensions: ['exe', 'lnk'] }]
          }
    )

    assertSender(sender)

    return result.canceled ? [] : result.filePaths
  }

  function requireCatalogApplication(id: unknown): CatalogApplication {
    const item = typeof id === 'string' ? catalog.resolve(id) : null

    if (!item) {
      throw new Error('应用列表已更新，请重新选择。')
    }

    return item
  }

  function resolveCatalogSelection(id: unknown): DockSelection {
    const item = requireCatalogApplication(id)

    return { target: item.launchTarget, name: item.name }
  }

  async function catalogState(force: boolean, expectedRevision?: number): Promise<DockCatalog> {
    const current = !force && expectedRevision !== undefined ? catalog.current(expectedRevision) : null
    const scan = current ?? (force ? await catalog.refresh() : await catalog.get())
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

    return catalogSnapshot(scan, entries)
  }

  function requireEntry(id: unknown): SavedDockEntry {
    const entry = typeof id === 'string' ? entries.find(item => item.id === id) : undefined

    if (!entry) {
      throw new Error('Dock 项目不存在。')
    }

    return entry
  }

  function taskTarget(id: string): { target: string; fallbackIcon: string | null } {
    const entry = entries.find(item => item.id === id)

    if (entry) {
      const projectionValue = projections.get(entry.target)

      return { target: projectionValue?.identity ?? targetIdentity(entry.target), fallbackIcon: projection(entry).icon }
    }

    const application = running.values().find(item => item.id === id)

    if (!application?.target || !application.windows.some(window => window.onDesktopDisplay)) {
      throw new Error('Dock 项目不存在。')
    }

    return { target: application.target, fallbackIcon: application.icon }
  }

  async function repairTo(entry: SavedDockEntry, original: DockSelection): Promise<DockState> {
    const selection = await validatedSelection(original)
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
  options.ipcMain.handle(IPC.invoke.dockCatalog, (event, force: unknown, expectedRevision: unknown) => {
    assertSender(event.sender)

    if (
      expectedRevision !== undefined &&
      (typeof expectedRevision !== 'number' || !Number.isInteger(expectedRevision) || expectedRevision <= 0)
    ) {
      throw new Error('无效应用列表版本。')
    }

    return serial(() => catalogState(force === true, expectedRevision))
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

    return withRunningApplications(
      event.sender,
      eligible,
      async () => {
        const application = findRunningApplication(id)
        const entry = entries.find(item => item.id === id)

        if (
          await activateRunningApplication({
            application,
            allowRemote: Boolean(entry),
            windowId,
            eligible,
            ready: running.state().status === 'ready',
            activate: (windowId, allowRemote, eligible) =>
              options.activateExternal(event.sender, windowId, allowRemote, eligible)
          })
        ) {
          return
        }

        if (!entry) {
          throw new Error('窗口已关闭，请重新选择程序。')
        }

        try {
          await launchApplication({
            target: entry.target,
            identity: targetIdentity(entry.target),
            eligible,
            validateTarget,
            launch: (target, appId, eligible) => options.launchExternal(event.sender, target, appId, eligible)
          })
        } catch (error) {
          // 移动或卸载后刷新状态，保留修复入口。
          if (isPackagedAppTarget(entry.target)) {
            await catalog.refresh()
          }

          projections.delete(entry.target)
          publish()
          throw error
        }
      },
      windowId === undefined && entries.some(entry => entry.id === id)
    )
  })
  options.ipcMain.handle(IPC.invoke.dockActivateCatalog, (event, id: unknown) => {
    assertSender(event.sender)
    const eligible = options.captureEligibility(event.sender)

    return withRunningApplications(
      event.sender,
      eligible,
      async () => {
        const item = requireCatalogApplication(id)
        const identity = targetIdentity(item.target)

        const assertCurrentTarget = (): void => {
          if (catalog.resolve(item.id) !== item || targetIdentity(item.launchTarget) !== identity) {
            throw new Error('应用列表已更新，请重新扫描后选择。')
          }
        }

        assertCurrentTarget()

        if (
          await activateRunningApplication({
            application: running.findTarget(identity),
            allowRemote: true,
            eligible,
            ready: running.state().status === 'ready',
            activate: (windowId, allowRemote, eligible) =>
              options.activateExternal(event.sender, windowId, allowRemote, eligible)
          })
        ) {
          return
        }

        await launchApplication({
          target: item.launchTarget,
          identity,
          eligible,
          validateTarget,
          assertCurrentTarget,
          launch: (target, appId, eligible) => options.launchExternal(event.sender, target, appId, eligible)
        })
      },
      true
    )
  })
  options.ipcMain.handle(IPC.invoke.dockTasks, (event, id: unknown) => {
    assertSender(event.sender)
    const eligible = options.captureEligibility(event.sender, false)

    if (typeof id !== 'string') {
      throw new Error('无效程序。')
    }

    if (process.platform !== 'win32') {
      return []
    }

    return taskReadSerial(async () => {
      assertSender(event.sender)

      if (!eligible.current()) {
        return []
      }

      const { target, fallbackIcon } = taskTarget(id)
      const tasks = await options.getJumpListTasks(event.sender, target)
      const projected = await projectTasks(tasks, fallbackIcon)

      return eligible.current() && taskTarget(id).target === target ? projected : []
    })
  })
  options.ipcMain.handle(IPC.invoke.dockActivateTask, (event, id: unknown, taskId: unknown) => {
    assertSender(event.sender)
    const eligible = options.captureEligibility(event.sender)

    if (typeof id !== 'string' || typeof taskId !== 'string' || taskId.length < 1 || taskId.length > 512) {
      throw new Error('无效程序任务。')
    }

    if (process.platform !== 'win32') {
      throw new Error('此系统不支持程序任务。')
    }

    return serial(async () => {
      assertSender(event.sender)

      if (!eligible.current()) {
        throw new Error('桌面已改变，请重新执行程序任务。')
      }

      const { target } = taskTarget(id)
      await options.activateJumpListTask(event.sender, target, taskId, eligible)
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
      const currentIds = new Set(application?.windows.filter(item => item.onDesktopDisplay).map(item => item.id))

      if (!windowIds.every(windowId => currentIds.has(windowId))) {
        throw new Error('窗口已关闭或程序已改变，请重新选择。')
      }

      await options.closeExternal(event.sender, windowIds, eligible)
    })
  })
  options.ipcMain.handle(IPC.invoke.dockPin, (event, runningId: unknown, beforeEntryId: unknown) => {
    assertSender(event.sender)
    const eligible = options.captureEligibility(event.sender, false)

    if (typeof runningId !== 'string' || (beforeEntryId !== undefined && typeof beforeEntryId !== 'string')) {
      throw new Error('无效程序或固定位置。')
    }

    return serial(async () => {
      assertSender(event.sender)
      await options.refreshApplications(event.sender)
      const application = running.values().find(item => item.id === runningId)
      const state = running.state()

      if (
        !application ||
        !application.target ||
        !application.windows.some(window => window.onDesktopDisplay) ||
        state.status !== 'ready'
      ) {
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

      const validated = await validatedSelection(selection)

      if (
        !eligible.current() ||
        running.getByKey(application.key) !== application ||
        !application.windows.some(window => window.onDesktopDisplay)
      ) {
        throw new Error('桌面或程序已改变，请重新选择。')
      }

      const index =
        beforeEntryId === undefined ? entries.length : entries.findIndex(entry => entry.id === beforeEntryId)

      if (index < 0) {
        throw new Error('固定项目已改变，请重新拖动。')
      }

      const next = [...entries]
      next.splice(index, 0, { id: randomUUID(), ...validated })

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
