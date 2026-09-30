import path from 'node:path'

import log from 'electron-log/main'

import { atomicWriteFile, errorMessage, RunnerNotConnectedError, safeReadJson } from '../utils'

const FILENAME = 'desktop-settings.json'

// 内存数据：磁盘路径、当前镜像、首次读取懒标记。
let storePath: null | string = null
let config: Record<string, unknown> = {}
let loaded = false

// 写锁：串行化各写入入口的内存修改、落盘与推送。
let writeLock: null | Promise<unknown> = null

// 同步协调：由 Runner host 设置的 pushTarget，以及 config-sync.ts 的 cloudSync 委托。
let pushTarget: null | ((config: Record<string, unknown>) => Promise<unknown> | void) = null
let cloudSync: null | { onLocalChange: (config: Record<string, unknown>) => void } = null

export function init({ spiritagentHome }: { spiritagentHome: null | string }): void {
  storePath = spiritagentHome ? path.join(spiritagentHome, FILENAME) : null
  loaded = false
}

function load(): Record<string, unknown> {
  if (loaded) {
    return config
  }

  loaded = true
  config = {}

  if (!storePath) {
    return config
  }

  const parsed = safeReadJson<Record<string, unknown>>(storePath)

  if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
    config = parsed
  }

  return config
}

/** 跨调用共享同一引用；变更必须经由 `patch` / `mutate` / `applyCloudMirror` / `clearSyncedMirror` 进行。*/
export function read(): Record<string, unknown> {
  return load()
}

export function setPushTarget(fn: null | ((config: Record<string, unknown>) => Promise<unknown> | void)): void {
  pushTarget = typeof fn === 'function' ? fn : null
}

export function setCloudSync(delegate: null | { onLocalChange: (config: Record<string, unknown>) => void }): void {
  cloudSync = delegate
}

async function runLocked<T>(task: () => Promise<T>): Promise<T> {
  while (writeLock) {
    await writeLock.catch(() => {})
  }

  const inflight = task()
  writeLock = inflight

  try {
    return await inflight
  } finally {
    writeLock = null
  }
}

// notifyCloud: false 用于云端水合与账户隔离写入，不通知云同步，防止回环。
async function persistAndPush({
  notifyCloud = true,
  pushRunner = true
}: { notifyCloud?: boolean; pushRunner?: boolean } = {}): Promise<void> {
  if (storePath) {
    await atomicWriteFile(storePath, JSON.stringify(config, null, 2))
  }

  // Runner 未连接时跳过：连接后的 runner_ready 握手会推送完整配置；其他推送失败记录原因。
  if (pushRunner && pushTarget) {
    try {
      await pushTarget(config)
    } catch (error) {
      if (!(error instanceof RunnerNotConnectedError)) {
        log.warn('[runner-config] pushing config to runner failed:', error)
      }
    }
  }

  if (notifyCloud) {
    cloudSync?.onLocalChange(config)
  }
}

/** 在写锁内修改并落盘；change 抛错或落盘失败时恢复原镜像并重新抛出：调用方已收到失败，未保存的修改不能随后续写入悄悄生效。 */
function changeLocked<T>(change: (config: Record<string, unknown>) => T, pushRunner = true): Promise<T> {
  return runLocked(async () => {
    load()
    const previous = structuredClone(config)

    try {
      const result = change(config)
      await persistAndPush({ pushRunner })

      return result
    } catch (error) {
      config = previous

      throw error
    }
  })
}

/** 云端水合入口：sections 是按同步节白名单与本地合并后的整节（保留本机专属键）及归属戳，整节替换进镜像（其余节与本机机密原样保留），落盘并推 runner，不触发云同步委托。落盘失败时保留已写入内存的云端值并抛出：云端是真源，恢复本地旧值会让之后的上传用旧值覆盖云端较新的设置。 */
export async function applyCloudMirror(
  sections: Record<string, unknown>,
  isCurrent: () => boolean = () => true
): Promise<void> {
  if (!sections || typeof sections !== 'object') {
    return
  }

  await runLocked(async () => {
    if (!isCurrent()) {
      return
    }

    load()
    Object.assign(config, sections)
    await persistAndPush({ notifyCloud: false })
  })
}

export async function patch(
  keyPath: readonly (number | string)[],
  { op = 'set', value }: { op?: 'delete' | 'set'; value?: unknown } = {}
): Promise<{ error?: string; ok: boolean }> {
  if (!Array.isArray(keyPath) || keyPath.length === 0) {
    return { error: 'path must be a non-empty array', ok: false }
  }

  if (!keyPath.every(isSafeKey)) {
    return { error: 'path contains an invalid key', ok: false }
  }

  if (op !== 'delete' && !isJsonValue(value)) {
    return { error: 'value must be JSON data', ok: false }
  }

  await changeLocked(target => (op === 'delete' ? deleteIn(target, keyPath) : setIn(target, keyPath, value)))

  return { ok: true }
}

/** fn 在写锁内变更配置；fn 抛错或落盘失败时恢复原镜像并返回失败，与 `patch` 一致。`pushRunner: false` 时只落盘、不推送 Runner。 */
export async function mutate<T>(
  fn: (config: Record<string, unknown>) => T,
  { pushRunner = true }: { pushRunner?: boolean } = {}
): Promise<{ error?: string; mutated?: T; ok: boolean }> {
  if (typeof fn !== 'function') {
    return { error: 'mutate requires a function', ok: false }
  }

  try {
    return { mutated: await changeLocked(fn, pushRunner), ok: true }
  } catch (err: unknown) {
    return { error: errorMessage(err), ok: false }
  }
}

/** 账户隔离：删除 keys 对应的同步节与原始值键，并把归属戳写入 `sync` 节；不推 Runner，也不触发云同步委托。先改内存再落盘，落盘失败时保留清理结果并抛出：镜像不能因写盘失败继续持有上个账户的设置或归属，磁盘上残留的旧归属在下次启动或换号时按不匹配再次清理。 */
export async function clearSyncedMirror(
  keys: readonly string[],
  stamp: Record<string, unknown>,
  isCurrent: () => boolean = () => true
): Promise<void> {
  await runLocked(async () => {
    if (!isCurrent()) {
      return
    }

    load()

    for (const key of keys) {
      delete config[key]
    }

    config.sync = stamp
    await persistAndPush({ notifyCloud: false, pushRunner: false })
  })
}

export function getDisabledSet(section = 'skills'): Set<string> {
  const sectionData = load()[section] as { disabled?: unknown } | undefined
  const raw = sectionData?.disabled

  if (!Array.isArray(raw)) {
    return new Set()
  }

  return new Set(raw.map(String))
}

// 落盘配置只接受 JSON 数据；IPC 结构化克隆可传入 BigInt、循环引用等，写入后每次序列化都会失败。
function isJsonValue(value: unknown, ancestors = new Set<object>()): boolean {
  if (value === null || typeof value === 'string' || typeof value === 'boolean') {
    return true
  }

  if (typeof value === 'number') {
    return Number.isFinite(value)
  }

  if (typeof value !== 'object' || ancestors.has(value)) {
    return false
  }

  if (
    !Array.isArray(value) &&
    Object.getPrototypeOf(value) !== Object.prototype &&
    Object.getPrototypeOf(value) !== null
  ) {
    return false
  }

  ancestors.add(value)
  const valid = Object.values(value).every(item => isJsonValue(item, ancestors))
  ancestors.delete(value)

  return valid
}

// 路径段来自渲染层：只接受字符串键与数组下标，并拒绝能触及原型链的键，避免污染主进程对象原型。
const FORBIDDEN_KEYS: ReadonlySet<string> = new Set(['__proto__', 'constructor', 'prototype'])

function isSafeKey(key: unknown): key is number | string {
  if (typeof key === 'number') {
    return Number.isInteger(key) && key >= 0
  }

  return typeof key === 'string' && !FORBIDDEN_KEYS.has(key)
}

// 只沿自有属性下行，继承成员不当作已有的嵌套对象。
function ownChild(obj: Record<string, unknown>, key: number | string): unknown {
  return Object.hasOwn(obj, key) ? obj[key] : undefined
}

function setIn(obj: Record<string, unknown>, keyPath: readonly (number | string)[], value: unknown): void {
  let cursor: Record<string, unknown> = obj

  for (let i = 0; i < keyPath.length - 1; i++) {
    const k = keyPath[i]
    let next = ownChild(cursor, k)

    if (next == null || typeof next !== 'object') {
      next = {}
      cursor[k] = next
    }

    cursor = next as Record<string, unknown>
  }

  cursor[keyPath[keyPath.length - 1]] = value
}

function deleteIn(obj: Record<string, unknown>, keyPath: readonly (number | string)[]): void {
  let cursor: Record<string, unknown> = obj

  for (let i = 0; i < keyPath.length - 1; i++) {
    const next = ownChild(cursor, keyPath[i])

    if (next == null || typeof next !== 'object') {
      return
    }

    cursor = next as Record<string, unknown>
  }

  delete cursor[keyPath[keyPath.length - 1]]
}
