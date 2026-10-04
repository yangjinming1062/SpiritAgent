import { randomUUID } from 'node:crypto'
import { stat } from 'node:fs/promises'
import path from 'node:path'

import { type DockCatalog, type DockEntry, type DockState, IPC } from '@ipc/contracts'
import { app, type BrowserWindow, dialog, type IpcMain, shell, type WebContents } from 'electron'

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

function fileSelection(target: string): DockSelection {
  return { target, name: path.basename(target, path.extname(target)) }
}

function targetIdentity(target: string): string {
  if (path.extname(target).toLowerCase() === '.lnk') {
    try {
      return shell.readShortcutLink(target).target.toLowerCase()
    } catch {
      // 失效快捷方式保留原路径，仍可显示和修复。
    }
  }

  return target.toLowerCase()
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
}): void {
  const filename = path.join(options.userData, 'desktop-dock.json')
  const saved = safeReadJson<SavedDock>(filename)
  let entries = saved?.version === 1 && Array.isArray(saved.entries) ? saved.entries.filter(isSavedEntry) : []
  let revision = 0
  const serial = createSerialQueue()
  const icons = new Map<string, string>()
  const catalog = createWindowsAppCatalog()

  const assertSender = (sender: WebContents): void => {
    if (!options.isDesktopSender(sender) || process.platform !== 'win32') {
      throw new Error('Dock 仅允许桌面入口操作。')
    }
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

  async function project(entry: SavedDockEntry): Promise<DockEntry> {
    let status: DockEntry['status'] = 'ready'
    let error: string | undefined
    let application: CatalogApplication | null = null

    try {
      application = await validateTarget(entry.target)
    } catch (cause) {
      status = (cause as NodeJS.ErrnoException).code === 'ENOENT' ? 'missing' : 'invalid'
      error = cause instanceof Error ? cause.message : '程序不可用。'
    }

    let icon = icons.get(entry.target) ?? null

    if (icon === null && status === 'ready') {
      try {
        icon = application
          ? (await catalog.icons([application.id]))[application.id]
          : await readTargetIcon(entry.target)

        if (icon !== null) {
          icons.set(entry.target, icon)
        }
      } catch {
        /* 图标失败保留程序启动能力。 */
      }
    }

    return { id: entry.id, name: entry.name, icon, status, ...(error ? { error } : {}) }
  }

  async function snapshot(): Promise<DockState> {
    return { revision, entries: await Promise.all(entries.map(project)) }
  }

  async function commit(next: SavedDockEntry[]): Promise<DockState> {
    await atomicWriteFile(filename, JSON.stringify({ version: 1, entries: next }))
    entries = next
    const targets = new Set(next.map(entry => entry.target))

    for (const target of icons.keys()) {
      if (!targets.has(target)) {
        icons.delete(target)
      }
    }

    revision += 1
    const state = await snapshot()
    sendToWindow(options.getDesktopWindow(), IPC.event.dockChanged, state)

    return state
  }

  async function add(selections: DockSelection[]): Promise<DockState> {
    if (selections.length > 100) {
      throw new Error('一次最多添加 100 个程序。')
    }

    const next = [...entries]
    const registered = new Set(next.map(entry => targetIdentity(entry.target)))

    for (const selection of selections) {
      await validateTarget(selection.target)
      const identity = targetIdentity(selection.target)

      if (!registered.has(identity)) {
        registered.add(identity)
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
    const registered = new Set(entries.map(entry => targetIdentity(entry.target)))

    return {
      revision: scan.revision,
      sources: scan.sources,
      // 仅限制下发列表；保存条目的校验仍使用完整扫描结果。
      items: scan.items.slice(0, 1000).map(item => ({
        id: item.id,
        name: item.name,
        detail: item.detail,
        inDock: registered.has(item.target.toLowerCase())
      }))
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

    if (entries.some(item => item.id !== entry.id && targetIdentity(item.target) === identity)) {
      throw new Error('该程序已在 Dock 中。')
    }

    return commit(entries.map(item => (item === entry ? { ...entry, ...selection } : item)))
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
  options.ipcMain.handle(IPC.invoke.dockLaunch, (event, id: unknown) => {
    assertSender(event.sender)

    return serial(async () => {
      assertSender(event.sender)
      const entry = requireEntry(id)

      try {
        await validateTarget(entry.target)

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

        revision += 1
        sendToWindow(options.getDesktopWindow(), IPC.event.dockChanged, await snapshot())
        throw error
      }
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
