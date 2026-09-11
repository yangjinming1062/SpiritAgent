import { atom, type WritableAtom } from 'nanostores'

import { safeJsonParse } from './safe-json'

interface StorageKeyConfig {
  preserveOnLogout?: boolean
}

const REGISTERED_STORAGE_KEYS = new Map<string, StorageKeyConfig>()
const CLEAR_HANDLERS = new Set<() => void | Promise<void>>()

// clearCompanionStorage 单调递增的 epoch 计数器。OPFS 写操作在 commit 前对照 epoch：
// 不一致就视为过期登出，丢弃写入——避免上一位用户的 in-flight write 把新用户的
// 文件写到刚清空的 OPFS 目录里（典型场景：登出与新登入间隔 < OPFS clear I/O 时间）。
let clearEpoch = 0

/** 当前 clearCompanionStorage epoch；异步写操作在 commit 前用它判活。 */
export function currentClearEpoch(): number {
  return clearEpoch
}

export function registerCompanionStorageKey(key: string, config: StorageKeyConfig = {}): string {
  REGISTERED_STORAGE_KEYS.set(key, config)

  return key
}

export function registerStorageClearHandler(handler: () => void | Promise<void>): () => void {
  CLEAR_HANDLERS.add(handler)

  return () => {
    CLEAR_HANDLERS.delete(handler)
  }
}

export function storedBoolean(key: string, fallback: boolean): boolean {
  try {
    const value = window.localStorage.getItem(key)

    return value === null ? fallback : value === 'true'
  } catch {
    return fallback
  }
}

export function persistBoolean(key: string, value: boolean): void {
  try {
    window.localStorage.setItem(key, String(value))
  } catch {
    // 尽力而为：受限上下文可能抛出异常。
  }
}

export function storedString(key: string): null | string {
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}

export function persistString(key: string, value: null | string): void {
  try {
    if (value === null) {
      window.localStorage.removeItem(key)
    } else {
      window.localStorage.setItem(key, value)
    }
  } catch {
    // 尽力而为。
  }
}

function storedJson<T>(key: string, fallback: T, validate?: (val: unknown) => val is T): T {
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

function storedEnum<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  const raw = storedString(key)

  if (raw !== null && (allowed as readonly string[]).includes(raw)) {
    return raw as T
  }

  return fallback
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
  get: () => T
}

/** preserveOnLogout=true 的 key 不挂 clear handler：跨登出保留偏好（窗口尺寸、面板偏移等）。
 * 想做「登出时清缓存但保留偏好」的复合语义：另起一个 key，不要复用本 helper。 */
function createPersisted<T>(opts: {
  key: string
  fallback: T
  preserveOnLogout: boolean
  load: () => T
  /** merge：把 next 当 Partial<T> 合进 current；replace：直接覆盖。 */
  apply: (current: T, next: T) => T
  /** isPersistable 守门：返回 false 时不落 localStorage（瞬态值保留在内存）。 */
  persist: (val: T) => void
}): { $atom: WritableAtom<T>; get: () => T; set: (next: T) => void; reset: () => void } {
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

  if (!preserveOnLogout) {
    registerStorageClearHandler(reset)
  }

  return {
    $atom,
    get: () => $atom.get(),
    reset,
    set
  }
}

/** 统一定义持久化 Atom：自动加载本地缓存、瞬态不冲刷持久层、登出自动重置内存与持久化。 */
export function definePersistedAtom<T extends object>(options: PersistedAtomOptions<T>): PersistedAtomResult<T> {
  const { fallback, isPersistable, key, preserveOnLogout = false } = options

  const base = createPersisted<T>({
    // T extends object 兼容 array / 类数组：!Array.isArray 守卫保证 next 是数组时走 replace
    // 分支（数组被解构成 {0:'a',1:'b'} 对象是隐式 bug）。
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

  const base = createPersisted<T>({
    apply: (_current, next) => next,
    fallback,
    key,
    load: () => storedEnum<T>(key, allowed, fallback),
    persist: val => persistString(key, val),
    preserveOnLogout
  })

  return base
}

export async function clearCompanionStorage(): Promise<void> {
  clearEpoch += 1

  try {
    for (const [key, config] of REGISTERED_STORAGE_KEYS.entries()) {
      if (!config.preserveOnLogout) {
        window.localStorage.removeItem(key)
      }
    }
  } catch {}

  // 触发所有上层模块注册的清理处理器（包含 OPFS 缓存清空与内存 Atom 状态重置）
  const tasks: Array<Promise<unknown> | void> = []

  for (const handler of CLEAR_HANDLERS) {
    try {
      const r = handler()

      if (r) {
        tasks.push(r)
      }
    } catch {}
  }

  // 真等所有 handler（包括异步 OPFS 清理）落地——调用方需在 $auth.set 前 await 此函数，
  // 避免 React 在 clear 完成前用陈旧 localStorage 值重渲染。
  await Promise.allSettled(tasks)
}
