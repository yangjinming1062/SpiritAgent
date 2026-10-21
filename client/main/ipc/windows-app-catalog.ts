import { randomUUID } from 'node:crypto'
import type { Dirent } from 'node:fs'
import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'

import { shell } from 'electron'
import log from 'electron-log/main'

import type { DockCatalogSource, DockCatalogSourceKey } from '@ipc/contracts'

import { createSerialQueue } from '../shared/utils'

import type { CatalogApplication, CatalogScan } from './desktop-app-catalog'
import { readWindowsApplicationIcon } from './windows-application-icon'
import { type InstalledApplication, readRegisteredApplications, readShellApplications } from './windows-installed-apps'

const MAX_DEPTH = 4
const MAX_SHORTCUTS = 1000
const CACHE_TTL_MS = 60_000
const MAX_ICON_BATCH = 64

function userAppData(): string {
  return process.env.APPDATA || path.join(os.homedir(), 'AppData', 'Roaming')
}

function systemData(): string {
  return process.env.ProgramData || 'C:\\ProgramData'
}

function homeDir(): string {
  return process.env.USERPROFILE || os.homedir()
}

// 扫描顺序即去重优先级：用户开始菜单优先于 All Users，用户桌面优先于 OneDrive 重定向后的桌面。
function sourceRoots(): { key: DockCatalogSourceKey; dir: string }[] {
  const startMenu = ['Microsoft', 'Windows', 'Start Menu', 'Programs']
  const desktopUser = path.join(homeDir(), 'Desktop')
  const oneDrive = process.env.OneDrive ? path.join(process.env.OneDrive, 'Desktop') : ''

  const roots: { key: DockCatalogSourceKey; dir: string }[] = [
    { key: 'start-user', dir: path.join(userAppData(), ...startMenu) },
    { key: 'start-all', dir: path.join(systemData(), ...startMenu) },
    { key: 'desktop-user', dir: desktopUser },
    {
      key: 'desktop-onedrive',
      dir: oneDrive && path.resolve(oneDrive) !== path.resolve(desktopUser) ? oneDrive : ''
    },
    {
      key: 'desktop-public',
      dir: path.join(process.env.PUBLIC || path.join(path.dirname(homeDir()), 'Public'), 'Desktop')
    }
  ]

  return roots.filter(root => Boolean(root.dir))
}

async function isDirectory(target: string): Promise<boolean> {
  try {
    return (await fs.stat(target)).isDirectory()
  } catch {
    return false
  }
}

async function isFile(target: string): Promise<boolean> {
  try {
    return (await fs.stat(target)).isFile()
  } catch {
    return false
  }
}

async function collectShortcuts(dir: string, depth: number, out: string[]): Promise<void> {
  if (depth > MAX_DEPTH) {
    return
  }

  let entries: Dirent[]

  try {
    entries = await fs.readdir(dir, { withFileTypes: true })
  } catch {
    return
  }

  for (const entry of entries) {
    if (out.length >= MAX_SHORTCUTS) {
      return
    }

    const full = path.join(dir, entry.name)

    if (entry.isDirectory()) {
      await collectShortcuts(full, depth + 1, out)
    } else if (entry.isFile() && path.extname(entry.name).toLowerCase() === '.lnk') {
      out.push(full)
    }
  }
}

function toShortcut(file: string, rootDir: string, target: string): CatalogApplication | null {
  const name = path.basename(file, path.extname(file))

  if (!name) {
    return null
  }

  const folder = path.relative(rootDir, path.dirname(file)).split(path.sep).join(' / ')

  return {
    id: randomUUID(),
    launchTarget: file,
    target,
    name,
    detail: [folder && folder !== '.' ? folder : '', path.basename(target)].filter(Boolean).join(' · ')
  }
}

async function scanSource(
  key: DockCatalogSourceKey,
  dir: string,
  budget: { left: number }
): Promise<{ report: DockCatalogSource; items: CatalogApplication[] }> {
  const found: string[] = []
  await collectShortcuts(dir, 0, found)
  const items: CatalogApplication[] = []

  for (const file of found) {
    if (budget.left <= 0) {
      break
    }

    budget.left -= 1

    let target = ''

    try {
      target = shell.readShortcutLink(file).target
    } catch {
      // 畸形快捷方式不进目录。
      continue
    }

    if (path.extname(target).toLowerCase() !== '.exe' || !(await isFile(target))) {
      continue
    }

    const item = toShortcut(file, dir, target)

    if (item) {
      items.push(item)
    }
  }

  return { report: { key, items: items.length, ok: true }, items }
}

export function createWindowsAppCatalog(): {
  get(): Promise<CatalogScan>
  current(revision: number): CatalogScan | null
  refresh(): Promise<CatalogScan>
  resolve(id: string): CatalogApplication | null
  findPackagedApplication(target: string): Promise<InstalledApplication | null>
  icons(ids: string[]): Promise<Record<string, string | null>>
} {
  const serial = createSerialQueue()
  const iconCache = new Map<string, string | null>()
  let handles = new Map<string, CatalogApplication>()
  let cached: CatalogScan | null = null
  let scannedAt = 0
  let revision = 0
  let shellApplications: InstalledApplication[] | null = null
  let shellApplicationsScannedAt = 0
  let shellApplicationsQuery: Promise<InstalledApplication[]> | null = null

  function getShellApplications(force = false): Promise<InstalledApplication[]> {
    if (shellApplicationsQuery) {
      return shellApplicationsQuery
    }

    if (!force && shellApplications && Date.now() - shellApplicationsScannedAt < CACHE_TTL_MS) {
      return Promise.resolve(shellApplications)
    }

    // 安装状态校验独立刷新，不能废弃仍在面板中使用的目录句柄。
    shellApplicationsQuery = readShellApplications()
      .then(items => {
        shellApplications = items
        shellApplicationsScannedAt = Date.now()

        return items
      })
      .catch(error => {
        shellApplications = null
        shellApplicationsScannedAt = 0
        throw error
      })
      .finally(() => {
        shellApplicationsQuery = null
      })

    return shellApplicationsQuery
  }

  async function scan(): Promise<CatalogScan> {
    const sources: DockCatalogSource[] = []
    const collected: CatalogApplication[] = []
    const budget = { left: MAX_SHORTCUTS }
    const installed = Promise.allSettled([readRegisteredApplications(), getShellApplications(true)])

    for (const root of sourceRoots()) {
      if (budget.left <= 0) {
        break
      }

      // 目录不存在是该来源的正常状态（如未启用 OneDrive 重定向），不是失败。
      if (!(await isDirectory(root.dir))) {
        sources.push({ key: root.key, items: 0, ok: true })

        continue
      }

      try {
        const result = await scanSource(root.key, root.dir, budget)

        sources.push(result.report)
        collected.push(...result.items)
      } catch {
        sources.push({ key: root.key, items: 0, ok: false })
      }
    }

    const installedResults = await installed

    for (const [index, key] of (['app-paths', 'apps-folder'] as const).entries()) {
      const result = installedResults[index]

      if (result.status === 'rejected') {
        log.warn(`[Dock] ${key} application discovery failed:`, result.reason)
        sources.push({ key, items: 0, ok: false })

        continue
      }

      sources.push({ key, items: result.value.length, ok: true })
      collected.push(...result.value.map(item => ({ ...item, id: randomUUID(), launchTarget: item.target })))
    }

    const items: CatalogApplication[] = []
    const seen = new Set<string>()

    for (const item of collected) {
      const key = item.target.toLowerCase()

      if (!seen.has(key)) {
        seen.add(key)
        items.push(item)
      }
    }

    handles = new Map(items.map(item => [item.id, item]))
    revision += 1
    scannedAt = Date.now()
    cached = { revision, sources, items }

    iconCache.clear()

    return cached
  }

  return {
    get: () => serial(() => (cached && Date.now() - scannedAt < CACHE_TTL_MS ? cached : scan())),
    current: requestedRevision => (cached?.revision === requestedRevision ? cached : null),
    refresh: () => serial(scan),
    resolve: id => handles.get(id) ?? null,
    findPackagedApplication: async target => {
      const identity = target.toLowerCase()

      return (await getShellApplications()).find(application => application.target.toLowerCase() === identity) ?? null
    },
    icons: async ids => {
      if (ids.length > MAX_ICON_BATCH) {
        throw new Error('一次最多读取 64 个图标。')
      }

      const queue = [...new Set(ids)].filter(id => !iconCache.has(id))

      await Promise.all(
        queue.map(async id => {
          const item = handles.get(id)

          if (!item) {
            return
          }

          const icon = await readWindowsApplicationIcon(item.launchTarget, item.iconPath)

          if (handles.has(id)) {
            iconCache.set(id, icon)
          }
        })
      )

      const result: Record<string, string | null> = {}

      for (const id of ids) {
        result[id] = handles.has(id) ? (iconCache.get(id) ?? null) : null
      }

      return result
    }
  }
}
