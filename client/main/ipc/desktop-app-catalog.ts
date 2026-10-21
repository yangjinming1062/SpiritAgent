import type { DockCatalogSource } from '@ipc/contracts'

import { createMacosAppCatalog } from './macos-app-catalog'
import { createWindowsAppCatalog } from './windows-app-catalog'
import type { InstalledApplication } from './windows-installed-apps'

export interface CatalogApplication {
  id: string
  launchTarget: string
  target: string
  name: string
  detail: string
  iconPath?: string | null
}

export interface CatalogScan {
  revision: number
  sources: DockCatalogSource[]
  items: CatalogApplication[]
}

export interface DesktopAppCatalog {
  get(): Promise<CatalogScan>
  current(revision: number): CatalogScan | null
  refresh(): Promise<CatalogScan>
  resolve(id: string): CatalogApplication | null
  findPackagedApplication(target: string): Promise<InstalledApplication | null>
  icons(ids: string[]): Promise<Record<string, string | null>>
}

export function createDesktopAppCatalog(): DesktopAppCatalog {
  return process.platform === 'darwin' ? createMacosAppCatalog() : createWindowsAppCatalog()
}
