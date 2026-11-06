import { stat } from 'node:fs/promises'
import path from 'node:path'

import { shell } from 'electron'

import { atomicWriteFile, safeReadJson } from '../../shared/utils'
import { isMacosApplicationTarget, validateMacosApplicationTarget } from '../macos-application-target'
import { isPackagedAppTarget } from '../windows-installed-apps'

export interface SavedDockEntry {
  id: string
  target: string
  name: string
}

export interface SavedDock {
  entries: SavedDockEntry[]
}

export type DockSelection = Pick<SavedDockEntry, 'target' | 'name'>

export function fileSelection(target: string): DockSelection {
  return { target, name: path.basename(target, path.extname(target)) }
}

export function targetIdentity(target: string): string {
  if (process.platform === 'darwin') {
    return path.normalize(target)
  }

  if (path.extname(target).toLowerCase() === '.lnk') {
    try {
      return path.normalize(shell.readShortcutLink(target).target).toLowerCase()
    } catch {
      // 失效快捷方式保留原路径，仍可显示和修复。
    }
  }

  return isPackagedAppTarget(target) ? target.toLowerCase() : path.normalize(target).toLowerCase()
}

export function sameApplicationTarget(first: string, second: string): boolean {
  if (first === second) {
    return true
  }

  if (
    process.platform === 'darwin' ||
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

export function isSavedEntry(raw: unknown): raw is SavedDockEntry {
  if (!raw || typeof raw !== 'object') {
    return false
  }

  const item = raw as Partial<SavedDockEntry>

  return (
    typeof item.id === 'string' &&
    typeof item.name === 'string' &&
    typeof item.target === 'string' &&
    (process.platform === 'darwin'
      ? isMacosApplicationTarget(item.target)
      : isPackagedAppTarget(item.target) ||
        (path.isAbsolute(item.target) && ['.exe', '.lnk'].includes(path.extname(item.target).toLowerCase())))
  )
}

export async function validateFileTarget(target: string): Promise<void> {
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

export function loadSavedEntries(userData: string): SavedDockEntry[] {
  const saved = safeReadJson<SavedDock>(path.join(userData, 'desktop-dock.json'))

  return Array.isArray(saved?.entries) ? saved.entries.filter(isSavedEntry) : []
}

export async function saveEntries(userData: string, entries: SavedDockEntry[]): Promise<void> {
  await atomicWriteFile(path.join(userData, 'desktop-dock.json'), JSON.stringify({ entries }))
}

export async function validatedSelection(selection: DockSelection): Promise<DockSelection> {
  if (process.platform === 'darwin') {
    const application = await validateMacosApplicationTarget(selection.target)

    return { target: application.path, name: application.name || selection.name }
  }

  await validateFileTarget(selection.target)

  return selection
}
