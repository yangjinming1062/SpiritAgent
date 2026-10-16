import {
  accountStorageKey,
  persistString,
  registerCompanionStorageKey,
  storedJson,
  storedString
} from '@/shared/lib/storage'
import { type AppNavigationTarget, normalizeAppNavigation } from '@ipc/contracts'

const MIGRATION_KEY = registerCompanionStorageKey('da.app.navigation.schema')

export function migrateStoredNavigation(key: string): AppNavigationTarget | null {
  if (storedString(MIGRATION_KEY) === '3') {
    return null
  }

  const saved = storedJson<Record<string, unknown> | null>(key, null)
  const ownerKey = accountStorageKey(key)
  const original = storedString(key)
  let migrated: AppNavigationTarget | null = null

  if (saved && typeof saved === 'object') {
    migrated = normalizeAppNavigation({
      ...saved,
      companionSection: saved.companionSection === 'home' ? 'profile' : saved.companionSection,
      page: saved.page === 'posts' && saved.postsTab === 'diary' ? 'diary' : saved.page
    })
  }

  // 账户恢复期间会暂停持久写入；恢复回调结束后再落盘，并核对仍属于同一账户。
  queueMicrotask(() => {
    if (!ownerKey || accountStorageKey(key) !== ownerKey) {
      return
    }

    if (migrated && storedString(key) === original) {
      persistString(key, JSON.stringify(migrated))
    }

    persistString(MIGRATION_KEY, '3')
  })

  return migrated
}
