import { randomUUID } from 'node:crypto'
import type { Dirent } from 'node:fs'
import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'

import type { DockCatalogSourceKey } from '@ipc/contracts'
import { app, shell } from 'electron'

import { createSerialQueue } from '../shared/utils'

/** 开始菜单下最多递归的层数；再深的分组对挑选没有帮助。 */
const MAX_DEPTH = 4
/** 单次扫描收集的快捷方式上限，触发即停止收集，避免异常目录拖垮主进程。 */
const MAX_SHORTCUTS = 1000
/** 目录缓存时长：装完新程序后短时间内复用旧结果，逾期由下次读取重扫。 */
const CACHE_TTL_MS = 60_000
/** 单次图标请求的条目上限，与渲染层每批请求的条数对应。 */
const MAX_ICON_BATCH = 64
const ICON_CONCURRENCY = 4
const ICON_CACHE_LIMIT = 512

export interface CatalogShortcut {
  id: string
  shortcutPath: string
  target: string
  name: string
  detail: string
}

export interface CatalogSourceReport {
  key: DockCatalogSourceKey
  items: number
  ok: boolean
}

export interface CatalogScan {
  revision: number
  sources: CatalogSourceReport[]
  items: CatalogShortcut[]
}

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
    { key: 'desktop-public', dir: process.env.PUBLIC || path.join(systemData(), 'Public Desktop') }
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

/** 递归收集 `.lnk`；单个目录读不到不影响其余分支，触顶即停止。 */
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

/**
 * Windows 的 `getFileIcon` 不解析 .lnk，直接读快捷方式只会得到通用快捷方式图标，
 * 因此优先读目标程序，失败才退回快捷方式本身。
 */
async function readShortcutIcon(item: CatalogShortcut): Promise<string | null> {
  for (const source of [item.target, item.shortcutPath]) {
    try {
      return (await app.getFileIcon(source, { size: 'normal' })).toDataURL()
    } catch {
      // 目标图标读不到时继续尝试下一个来源。
    }
  }

  return null
}

function toShortcut(file: string, rootDir: string, target: string): CatalogShortcut | null {
  const name = path.basename(file, path.extname(file))

  if (!name) {
    return null
  }

  const folder = path.relative(rootDir, path.dirname(file)).split(path.sep).join(' / ')

  return {
    id: randomUUID(),
    shortcutPath: file,
    target,
    name,
    detail: [folder && folder !== '.' ? folder : '', path.basename(target)].filter(Boolean).join(' · ')
  }
}

async function scanSource(
  key: DockCatalogSourceKey,
  dir: string,
  budget: { left: number }
): Promise<{ report: CatalogSourceReport; items: CatalogShortcut[] }> {
  const found: string[] = []
  await collectShortcuts(dir, 0, found)
  const items: CatalogShortcut[] = []

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

    // 只收录目标为现存 `.exe` 的条目：网页快捷方式、管理工具与「此电脑」这类虚拟 shell 项自然被排除。
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

/**
 * 开始菜单与桌面的快捷方式目录。条目在主进程持有随机句柄与真实路径，
 * 渲染层只能拿句柄回传，无法自造路径。
 */
export function createWindowsAppCatalog(): {
  get(): Promise<CatalogScan>
  refresh(): Promise<CatalogScan>
  resolve(id: string): CatalogShortcut | null
  icons(ids: string[]): Promise<Record<string, string | null>>
} {
  const serial = createSerialQueue()
  const iconCache = new Map<string, string | null>()
  let handles = new Map<string, CatalogShortcut>()
  let cached: CatalogScan | null = null
  let scannedAt = 0
  let revision = 0

  async function scan(): Promise<CatalogScan> {
    const sources: CatalogSourceReport[] = []
    const collected: CatalogShortcut[][] = []
    const budget = { left: MAX_SHORTCUTS }

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
        collected.push(result.items)
      } catch {
        sources.push({ key: root.key, items: 0, ok: false })
      }
    }

    const items: CatalogShortcut[] = []
    const seen = new Set<string>()

    for (const group of collected) {
      for (const item of group) {
        const key = item.target.toLowerCase()

        if (seen.has(key)) {
          continue
        }

        seen.add(key)
        items.push(item)
      }
    }

    handles = new Map(items.map(item => [item.id, item]))
    revision += 1
    scannedAt = Date.now()
    cached = { revision, sources, items }

    if (iconCache.size > ICON_CACHE_LIMIT) {
      iconCache.clear()
    }

    return cached
  }

  return {
    get: () => serial(() => (cached && Date.now() - scannedAt < CACHE_TTL_MS ? cached : scan())),
    refresh: () => serial(scan),
    resolve: id => handles.get(id) ?? null,
    icons: async ids => {
      if (ids.length > MAX_ICON_BATCH) {
        throw new Error('一次最多读取 64 个图标。')
      }

      const queue = [...new Set(ids)].filter(id => !iconCache.has(id))

      for (let index = 0; index < queue.length; index += ICON_CONCURRENCY) {
        await Promise.all(
          queue.slice(index, index + ICON_CONCURRENCY).map(async id => {
            const item = handles.get(id)

            if (!item) {
              return
            }

            try {
              iconCache.set(id, await readShortcutIcon(item))
            } catch {
              // 图标失败保留默认占位，不影响条目本身可选。
              iconCache.set(id, null)
            }
          })
        )
      }

      const result: Record<string, string | null> = {}

      for (const id of ids) {
        result[id] = iconCache.get(id) ?? null
      }

      return result
    }
  }
}
