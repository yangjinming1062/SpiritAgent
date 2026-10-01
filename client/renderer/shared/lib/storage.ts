import { atom, type WritableAtom } from 'nanostores'

import { log } from './log'
import { safeJsonParse } from './safe-json'

interface StorageKeyConfig {
  preserveOnLogout?: boolean
}

const REGISTERED_STORAGE_KEYS = new Map<string, StorageKeyConfig>()
const CLEAR_HANDLERS = new Set<() => void | Promise<void>>()
const RESTORE_HANDLERS = new Set<() => void>()
const ACCOUNT_CACHE_OWNER_KEY = 'da.auth.accountCacheOwner'
let storageAccountId = storedString(ACCOUNT_CACHE_OWNER_KEY)
let legacyAccountId = storageAccountId
let switchingAccount = false

// 切换账户时递增；异步回写必须核对，不能把旧账户结果写入当前命名空间。
let clearEpoch = 0

/** 当前账户状态代次；异步写操作在 commit 前用它判活。 */
export function currentClearEpoch(): number {
  return clearEpoch
}

export function registerCompanionStorageKey(key: string, config: StorageKeyConfig = {}): string {
  REGISTERED_STORAGE_KEYS.set(key, config)

  // 已有未分账户的索引归入其记录的账户；没有归属时不读取旧值。
  if (legacyAccountId && !config.preserveOnLogout) {
    try {
      const legacy = window.localStorage.getItem(key)
      const target = `da.accounts.${legacyAccountId}:${key}`

      if (legacy !== null) {
        if (window.localStorage.getItem(target) === null) {
          window.localStorage.setItem(target, legacy)
        }

        window.localStorage.removeItem(key)
      }
    } catch (error) {
      log.warn('storage', 'account cache migration failed', error)
    }
  }

  return key
}

export function accountStorageKey(key: string): string | null {
  const config = REGISTERED_STORAGE_KEYS.get(key)

  if (!config || config.preserveOnLogout) {
    return key
  }

  return storageAccountId ? `da.accounts.${storageAccountId}:${key}` : null
}

export function registerStorageClearHandler(handler: () => void | Promise<void>): () => void {
  CLEAR_HANDLERS.add(handler)

  return () => {
    CLEAR_HANDLERS.delete(handler)
  }
}

export function registerStorageRestoreHandler(handler: () => void): () => void {
  RESTORE_HANDLERS.add(handler)

  return () => {
    RESTORE_HANDLERS.delete(handler)
  }
}

export function storedBoolean(key: string, fallback: boolean): boolean {
  const value = storedString(key)

  return value === null ? fallback : value === 'true'
}

export function persistBoolean(key: string, value: boolean): void {
  persistString(key, String(value))
}

export function storedString(key: string): null | string {
  const target = accountStorageKey(key)

  try {
    return target ? window.localStorage.getItem(target) : null
  } catch {
    return null
  }
}

export function persistString(key: string, value: null | string): void {
  const target = accountStorageKey(key)

  if (!target || (switchingAccount && REGISTERED_STORAGE_KEYS.has(key))) {
    return
  }

  try {
    if (value === null) {
      window.localStorage.removeItem(target)
    } else {
      window.localStorage.setItem(target, value)
    }
  } catch (error) {
    log.warn('storage', `persist failed: ${key}`, error)
  }
}

export function storedJson<T>(key: string, fallback: T, validate?: (val: unknown) => val is T): T {
  const raw = storedString(key)

  if (!raw) {
    return fallback
  }

  const parsed = safeJsonParse<unknown>(raw, undefined)

  if (parsed === undefined) {
    return fallback
  }

  if (validate && !validate(parsed)) {
    return fallback
  }

  return parsed as T
}

interface PersistedAtomOptions<T> {
  key: string
  fallback: T
  isPersistable?: (val: unknown) => val is T
  preserveOnLogout?: boolean
}

interface PersistedAtomResult<T> {
  $atom: WritableAtom<T>
  set: (next: T | Partial<T>) => void
  reset: () => void
  get: () => T
}

interface PersistedEnumOptions<T extends string> {
  key: string
  allowed: readonly T[]
  fallback: T
  preserveOnLogout?: boolean
}

interface PersistedEnumResult<T extends string> {
  $atom: WritableAtom<T>
  set: (next: T) => void
  reset: () => void
  /** 按本地存储重读并写入 atom（不回写存储）：其他窗口改动后同步用。 */
  reload: () => void
  get: () => T
}

/** preserveOnLogout 的本机偏好跨账户共享，其余按账户加载与重置内存。 */
function createPersisted<T>(opts: {
  key: string
  fallback: T
  preserveOnLogout: boolean
  load: () => T
  /** merge：把 next 当 Partial<T> 合进 current；replace：直接覆盖。 */
  apply: (current: T, next: T) => T
  /** isPersistable 守门：返回 false 时不落 localStorage（瞬态值保留在内存）。 */
  persist: (val: T) => void
}): { $atom: WritableAtom<T>; get: () => T; set: (next: T) => void; reset: () => void; reload: () => void } {
  const { apply, fallback, key, load, persist, preserveOnLogout } = opts
  registerCompanionStorageKey(key, { preserveOnLogout })

  const initial = load()
  const $atom = atom<T>(initial)

  function set(next: T): void {
    const updated = apply($atom.get(), next)

    $atom.set(updated)
    persist(updated)
  }

  function reset(): void {
    $atom.set(fallback)
    persistString(key, null)
  }

  function reload(): void {
    $atom.set(load())
  }

  if (!preserveOnLogout) {
    registerStorageClearHandler(() => {
      $atom.set(fallback)
    })
    registerStorageRestoreHandler(reload)
  }

  return {
    $atom,
    get: () => $atom.get(),
    reload,
    reset,
    set
  }
}

/** 账户持久化 Atom：加载本地缓存，瞬态不冲刷持久层。 */
export function definePersistedAtom<T extends object>(options: PersistedAtomOptions<T>): PersistedAtomResult<T> {
  const { fallback, isPersistable, key, preserveOnLogout = false } = options

  const base = createPersisted<T>({
    // T extends object 兼容 array/类数组：!Array.isArray 守卫保证 next 是数组时走 replace 分支（数组被解构成对象是隐式 bug）。
    apply: (current, next) => (!Array.isArray(next) ? { ...current, ...next } : next),
    fallback,
    key,
    load: () => storedJson<T>(key, fallback, isPersistable),
    persist: val => {
      if (!isPersistable || isPersistable(val)) {
        persistString(key, JSON.stringify(val))
      }
    },
    preserveOnLogout
  })

  // 入口签名差异只在 set：Atom 接受 Partial<T>，内部仍规约为 T 后交给 base.set。
  return {
    $atom: base.$atom,
    get: base.get,
    reset: base.reset,
    set: next => base.set(next as T)
  }
}

/** 统一定义持久化枚举：严格字面量类型校验、单一来源注册与登出生命周期绑定。 */
export function definePersistedEnum<T extends string>(options: PersistedEnumOptions<T>): PersistedEnumResult<T> {
  const { allowed, fallback, key, preserveOnLogout = false } = options

  const load = (): T => {
    const raw = storedString(key)

    if (raw !== null && (allowed as readonly string[]).includes(raw)) {
      return raw as T
    }

    return fallback
  }

  const base = createPersisted<T>({
    apply: (_current, next) => next,
    fallback,
    key,
    load,
    persist: val => persistString(key, val),
    preserveOnLogout
  })

  return base
}

export async function setStorageAccount(accountId: string | null, removedAccountId?: string): Promise<void> {
  if (removedAccountId) {
    const prefix = `da.accounts.${removedAccountId}:`
    const removeLegacy = legacyAccountId === removedAccountId

    if (removeLegacy) {
      legacyAccountId = null
    }

    try {
      for (const key of Object.keys(window.localStorage)) {
        const config = REGISTERED_STORAGE_KEYS.get(key)

        if (key.startsWith(prefix) || (removeLegacy && config && !config.preserveOnLogout)) {
          try {
            window.localStorage.removeItem(key)
          } catch (error) {
            log.warn('storage', `removeItem failed: ${key}`, error)
          }
        }
      }
    } catch (error) {
      log.warn('storage', 'account cache enumeration failed', error)
    }
  }

  if (storageAccountId === accountId && removedAccountId !== accountId) {
    return
  }

  clearEpoch += 1
  switchingAccount = true

  // 只释放旧账户的内存、监听与在途工作；重置产生的写入不触及持久缓存。
  const tasks: Array<Promise<unknown> | void> = []

  for (const handler of CLEAR_HANDLERS) {
    try {
      const r = handler()

      if (r) {
        tasks.push(r)
      }
    } catch (error) {
      log.warn('storage', 'clear handler failed', error)
    }
  }

  for (const result of await Promise.allSettled(tasks)) {
    if (result.status === 'rejected') {
      log.warn('storage', 'async clear handler failed', result.reason)
    }
  }

  storageAccountId = accountId
  persistString(ACCOUNT_CACHE_OWNER_KEY, accountId)

  for (const restore of RESTORE_HANDLERS) {
    try {
      restore()
    } catch (error) {
      log.warn('storage', 'restore handler failed', error)
    }
  }

  switchingAccount = false
}
