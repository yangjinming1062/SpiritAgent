import type { DockCatalog } from '@ipc/contracts'

import type { CatalogScan } from '../desktop-app-catalog'

import { sameApplicationTarget, type SavedDockEntry, targetIdentity } from './registry'

/** 应用目录投影：只标注是否已在 Dock，不改变目录本身。 */
export function catalogSnapshot(scan: CatalogScan, entries: SavedDockEntry[]): DockCatalog {
  const registered = entries.map(entry => targetIdentity(entry.target))

  return {
    revision: scan.revision,
    sources: scan.sources,
    items: scan.items.map(item => {
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
