import { randomUUID } from 'node:crypto'
import { stat } from 'node:fs/promises'
import path from 'node:path'

import { type DockCatalog, type DockEntry, type DockState, IPC } from '@ipc/contracts'
import { app, type BrowserWindow, dialog, type IpcMain, shell, type WebContents } from 'electron'

import { atomicWriteFile, createSerialQueue, safeReadJson, sendToWindow } from '../shared/utils'

import { createWindowsAppCatalog } from './windows-app-catalog'

interface SavedDockEntry {
  id: string
  target: string
  name: string
}

interface SavedDock {
  version: 1
  entries: SavedDockEntry[]
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
    path.isAbsolute(item.target) &&
    ['.exe', '.lnk'].includes(path.extname(item.target).toLowerCase())
  )
}

async function validateTarget(target: string): Promise<void> {
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

/**
 * Windows 的 `getFileIcon` 不解析 .lnk，直接读快捷方式只会得到通用快捷方式图标，
 * 因此先按解析出的目标程序取图标，读不到再退回快捷方式本身。
 */
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

  async function project(entry: SavedDockEntry): Promise<DockEntry> {
    let status: DockEntry['status'] = 'ready'
    let error: string | undefined

    try {
      await validateTarget(entry.target)
    } catch (cause) {
      status = (cause as NodeJS.ErrnoException).code === 'ENOENT' ? 'missing' : 'invalid'
      error = cause instanceof Error ? cause.message : '程序不可用。'
    }

    let icon = icons.get(entry.target) ?? null

    if (icon === null && status === 'ready') {
      try {
        icon = await readTargetIcon(entry.target)
        icons.set(entry.target, icon)
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

  async function add(paths: string[]): Promise<DockState> {
    if (paths.length > 100) {
      throw new Error('一次最多添加 100 个程序。')
    }

    const next = [...entries]

    for (const target of paths) {
      await validateTarget(target)

      if (!next.some(entry => entry.target.toLowerCase() === target.toLowerCase())) {
        next.push({ id: randomUUID(), target, name: path.basename(target, path.extname(target)) })
      }
    }

    return commit(next)
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

  // 目录条目以主进程句柄下发；渲染层无法自造路径，重扫后旧句柄立即失效。
  function resolveCatalogTarget(id: unknown): string {
    const item = typeof id === 'string' ? catalog.resolve(id) : null

    if (!item) {
      throw new Error('应用列表已更新，请重新选择。')
    }

    return item.shortcutPath
  }

  async function catalogState(force: boolean): Promise<DockCatalog> {
    const scan = force ? await catalog.refresh() : await catalog.get()
    const registered = new Set(entries.map(entry => entry.target.toLowerCase()))

    return {
      revision: scan.revision,
      sources: scan.sources,
      items: scan.items.map(item => ({
        id: item.id,
        name: item.name,
        detail: item.detail,
        // 条目既可能登记为快捷方式，也可能直接登记为程序本身，两种都要识别为已在 Dock。
        inDock: registered.has(item.shortcutPath.toLowerCase()) || registered.has(item.target.toLowerCase())
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

  function commitRepair(entry: SavedDockEntry, target: string): Promise<DockState> {
    return commit(
      entries.map(item =>
        item === entry ? { ...entry, target, name: path.basename(target, path.extname(target)) } : item
      )
    )
  }

  async function repairTo(entry: SavedDockEntry, target: string): Promise<DockState> {
    await validateTarget(target)

    if (entries.some(item => item.id !== entry.id && item.target.toLowerCase() === target.toLowerCase())) {
      throw new Error('该程序已在 Dock 中。')
    }

    return commitRepair(entry, target)
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
      const targets = raw.map(resolveCatalogTarget)

      return targets.length ? add(targets) : snapshot()
    })
  })
  options.ipcMain.handle(IPC.invoke.dockAddFromFiles, event => {
    assertSender(event.sender)

    return serial(async () => {
      const paths = await pickApplications(event.sender, true)

      return paths.length ? add(paths) : snapshot()
    })
  })
  options.ipcMain.handle(IPC.invoke.dockRepairWithCatalog, (event, entryId: unknown, catalogId: unknown) => {
    assertSender(event.sender)

    return serial(async () => {
      assertSender(event.sender)

      return repairTo(requireEntry(entryId), resolveCatalogTarget(catalogId))
    })
  })
  options.ipcMain.handle(IPC.invoke.dockRepairWithFiles, (event, entryId: unknown) => {
    assertSender(event.sender)

    return serial(async () => {
      const entry = requireEntry(entryId)
      const [target] = await pickApplications(event.sender, false)

      return target ? repairTo(entry, target) : snapshot()
    })
  })
  options.ipcMain.handle(IPC.invoke.dockAddDropped, (event, raw: unknown) => {
    assertSender(event.sender)

    if (!Array.isArray(raw) || !raw.every(item => typeof item === 'string')) {
      throw new Error('无效程序文件。')
    }

    return serial(() => {
      assertSender(event.sender)

      return add(raw)
    })
  })
  options.ipcMain.handle(IPC.invoke.dockLaunch, (event, id: unknown) => {
    assertSender(event.sender)

    return serial(async () => {
      assertSender(event.sender)
      const entry = entries.find(item => item.id === id)

      if (!entry) {
        throw new Error('Dock 项目不存在。')
      }

      try {
        await validateTarget(entry.target)
        const error = await shell.openPath(entry.target)

        if (error) {
          throw new Error(error)
        }
      } catch (error) {
        // 程序可能在上次快照后被移动，刷新失效状态以提供修复入口。
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
