import { randomUUID } from 'node:crypto'
import type { Dirent } from 'node:fs'
import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'

import log from 'electron-log/main'

import type { DockCatalogSource, DockCatalogSourceKey } from '@ipc/contracts'

import { desktopMacos } from '../shared/desktop-macos'
import type { MacosApplicationInfo } from '../shared/desktop-macos'
import { createSerialQueue } from '../shared/utils'

import type { CatalogApplication, CatalogScan, DesktopAppCatalog } from './desktop-app-catalog'
import { isCompleteMacosBundle, isMacosApplicationTarget, readMacosApplicationIcon } from './macos-application-target'

const CACHE_TTL_MS = 60_000
const MAX_APPLICATIONS = 1000
const MAX_DEPTH = 2
const MAX_ICON_BATCH = 64
const SCAN_BUDGET_MS = 15_000

function sourceRoots(): { key: DockCatalogSourceKey; dir: string }[] {
  return [
    { key: 'mac-applications', dir: '/Applications' },
    { key: 'mac-system', dir: '/System/Applications' },
    { key: 'mac-user', dir: path.join(os.homedir(), 'Applications') }
  ]
}

export function createMacosAppCatalog(): DesktopAppCatalog {
  const serial = createSerialQueue()
  let handles = new Map<string, CatalogApplication>()
  const iconCache = new Map<string, string | null>()
  let cached: CatalogScan | null = null
  let scannedAt = 0
  let revision = 0

  async function scan(): Promise<CatalogScan> {
    const deadline = Date.now() + SCAN_BUDGET_MS
    const seen = new Set<string>()
    const items: CatalogApplication[] = []
    const sources: DockCatalogSource[] = []
    let remaining = MAX_APPLICATIONS

    const withinBudget = async <T>(task: Promise<T>): Promise<T> => {
      let timer: ReturnType<typeof setTimeout> | undefined

      try {
        return await Promise.race([
          task,
          new Promise<never>((_resolve, reject) => {
            timer = setTimeout(() => reject(new Error('应用目录读取超时。')), Math.max(1, deadline - Date.now()))
          })
        ])
      } finally {
        clearTimeout(timer)
      }
    }

    for (const root of sourceRoots()) {
      const candidates: string[] = []
      let ok = true

      const collect = async (directory: string, depth: number): Promise<void> => {
        if (Date.now() >= deadline || remaining <= 0) {
          ok = false

          return
        }

        let children: Dirent[]

        try {
          children = await withinBudget(fs.readdir(directory, { withFileTypes: true }))
        } catch (cause) {
          if ((cause as NodeJS.ErrnoException).code !== 'ENOENT') {
            ok = false
            log.warn('[Dock] macOS application source read failed:', directory, cause)
          }

          return
        }

        for (const child of children) {
          if (Date.now() >= deadline || remaining <= 0) {
            ok = false

            break
          }

          if (child.name.startsWith('.')) {
            continue
          }

          const target = path.join(directory, child.name)

          if (isMacosApplicationTarget(target) && (child.isDirectory() || child.isSymbolicLink())) {
            remaining -= 1

            try {
              const canonical = await withinBudget(fs.realpath(target))

              if (
                isMacosApplicationTarget(canonical) &&
                !seen.has(canonical) &&
                (await withinBudget(isCompleteMacosBundle(canonical)))
              ) {
                seen.add(canonical)
                candidates.push(canonical)
              }
            } catch (cause) {
              // 已卸载或不完整的 Bundle 不进入目录；权限失败保留来源失败状态。
              if ((cause as NodeJS.ErrnoException).code !== 'ENOENT') {
                ok = false
                log.warn('[Dock] macOS application verification failed:', target, cause)
              }
            }
          } else if (child.isDirectory() && depth < MAX_DEPTH) {
            await collect(target, depth + 1)
          }
        }
      }

      await collect(root.dir, 0)
      const before = items.length

      const append = (applications: MacosApplicationInfo[], allowed: Set<string>): void => {
        for (const application of applications) {
          if (allowed.has(application.path)) {
            items.push({
              id: randomUUID(),
              launchTarget: application.path,
              target: application.path,
              name: application.name || path.basename(application.path, '.app'),
              detail: path.dirname(application.path)
            })
          }
        }
      }

      for (let index = 0; index < candidates.length; index += 32) {
        if (Date.now() >= deadline) {
          ok = false

          break
        }

        const batch = candidates.slice(index, index + 32)

        try {
          const applications = await withinBudget(desktopMacos().applicationInfo(batch, false))
          append(applications, new Set(batch))
        } catch (cause) {
          ok = false
          log.warn('[Dock] macOS application metadata failed:', root.dir, cause)

          // 单个损坏 Bundle 不应使同批其他应用从目录消失。
          for (const target of batch) {
            if (Date.now() >= deadline) {
              break
            }

            try {
              append(await withinBudget(desktopMacos().applicationInfo([target], false)), new Set([target]))
            } catch (error) {
              log.warn('[Dock] macOS application metadata failed:', target, error)
            }
          }
        }
      }

      sources.push({ key: root.key, items: items.length - before, ok })
    }

    items.sort((first, second) => first.name.localeCompare(second.name))
    handles = new Map(items.map(item => [item.id, item]))
    iconCache.clear()
    revision += 1
    scannedAt = Date.now()
    cached = { revision, sources, items }

    return cached
  }

  return {
    get: () => serial(() => (cached && Date.now() - scannedAt < CACHE_TTL_MS ? cached : scan())),
    current: requestedRevision => (cached?.revision === requestedRevision ? cached : null),
    refresh: () => serial(scan),
    resolve: id => handles.get(id) ?? null,
    findPackagedApplication: () => Promise.resolve(null),
    icons: async ids => {
      if (ids.length > MAX_ICON_BATCH) {
        throw new Error('一次最多读取 64 个图标。')
      }

      const currentHandles = handles
      const queue = [...new Set(ids)].filter(id => !iconCache.has(id))

      for (let index = 0; index < queue.length; index += 4) {
        await Promise.all(
          queue.slice(index, index + 4).map(async id => {
            const item = currentHandles.get(id)

            if (!item || handles !== currentHandles) {
              return
            }

            const icon = await readMacosApplicationIcon(item.target)

            if (handles === currentHandles) {
              iconCache.set(id, icon)
            }
          })
        )
      }

      return Object.fromEntries(ids.map(id => [id, handles === currentHandles ? (iconCache.get(id) ?? null) : null]))
    }
  }
}
