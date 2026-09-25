import path from 'node:path'

import { atomicWriteFile, errorMessage, safeReadJson } from '../utils'

const FILENAME = 'desktop-settings.json'

// 内存数据：磁盘路径、当前镜像、首次读取懒标记。
let storePath: null | string = null
let config: Record<string, unknown> = {}
let loaded = false

// 写锁：串行化 write/patch/mutate 之间的落盘与推送。
let writeLock: null | Promise<unknown> = null

// 同步协调：由 bridge 设置的 pushTarget、config-sync.ts 的 cloudSync 委托，
// 以及 applyCloudMirror 期间抑制本地变更通知的标志（防回环）。
let pushTarget: null | ((config: Record<string, unknown>) => Promise<unknown> | void) = null
let cloudSync: null | { onLocalChange: (config: Record<string, unknown>) => void } = null
let suppressCloudSync = false

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

/** 跨调用共享同一引用；变更必须经由 ``write`` / ``patch`` / ``mutate`` 进行。*/
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

async function persistAndPush(pushRunner = true): Promise<void> {
  if (storePath) {
    const content = JSON.stringify(config, null, 2)
    await atomicWriteFile(storePath, content)
  }

  // 吞掉派发错误——bridge 在登录前可能尚未连接。
  if (pushRunner && pushTarget && config) {
    try {
      await pushTarget(config)
    } catch {
      /* runner 未连接——待下次 runner-ready 时再推送配置 */
    }
  }

  // 本地写入后通知云同步（水合写入经 suppressCloudSync 抑制，防止回环）。
  if (cloudSync && !suppressCloudSync) {
    cloudSync.onLocalChange(config)
  }
}

/**
 * 云端水合入口：sections 是已按同步节白名单合并且剔除本机键的结果，
 * 整节替换进镜像（其余节与本机机密原样保留），落盘并推 runner，
 * 不触发云同步委托。sections 为空时是 no-op。
 */
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
    suppressCloudSync = true

    try {
      for (const [section, value] of Object.entries(sections)) {
        config[section] = value
      }

      await persistAndPush()
    } finally {
      suppressCloudSync = false
    }
  })
}

export async function write(obj: unknown): Promise<{ error?: string; ok: boolean }> {
  return runLocked(async () => {
    if (!obj || typeof obj !== 'object' || Array.isArray(obj)) {
      return { error: 'config must be a plain object', ok: false }
    }

    config = obj as Record<string, unknown>
    await persistAndPush()

    return { ok: true }
  })
}

export async function patch(
  keyPath: readonly (number | string)[],
  { op = 'set', value }: { op?: 'delete' | 'set'; value?: unknown } = {}
): Promise<{ error?: string; ok: boolean }> {
  if (!Array.isArray(keyPath) || keyPath.length === 0) {
    return { error: 'path must be a non-empty array', ok: false }
  }

  return runLocked(async () => {
    load()

    if (config) {
      if (op === 'delete') {
        deleteIn(config, keyPath)
      } else {
        setIn(config, keyPath, value)
      }
    }

    await persistAndPush()

    return { ok: true }
  })
}

/** fn 在写锁内变更配置；`pushRunner: false` 时只落盘、不推送 Runner。 */
export async function mutate<T>(
  fn: (config: Record<string, unknown>) => T,
  { pushRunner = true }: { pushRunner?: boolean } = {}
): Promise<{ error?: string; mutated?: T; ok: boolean }> {
  if (typeof fn !== 'function') {
    return { error: 'mutate requires a function', ok: false }
  }

  let mutated: T | undefined

  try {
    await runLocked(async () => {
      load()
      const snapshot = JSON.parse(JSON.stringify(config ?? {}))

      try {
        if (config) {
          mutated = fn(config)
        }
      } catch (err) {
        config = snapshot
        throw err
      }

      await persistAndPush(pushRunner)
    })

    return { mutated, ok: true }
  } catch (err: unknown) {
    const msg = errorMessage(err)

    return { error: msg, ok: false }
  }
}

export function getDisabledSet(section = 'skills'): Set<string> {
  const sectionData = load()[section] as { disabled?: unknown } | undefined
  const raw = sectionData?.disabled

  if (!Array.isArray(raw)) {
    return new Set()
  }

  return new Set(raw.map(String))
}

function setIn(obj: Record<string, unknown>, keyPath: readonly (number | string)[], value: unknown): void {
  let cursor: Record<string, unknown> = obj

  for (let i = 0; i < keyPath.length - 1; i++) {
    const k = keyPath[i]

    if (cursor[k] == null || typeof cursor[k] !== 'object') {
      cursor[k] = {}
    }

    cursor = cursor[k] as Record<string, unknown>
  }

  cursor[keyPath[keyPath.length - 1]] = value
}

function deleteIn(obj: Record<string, unknown>, keyPath: readonly (number | string)[]): void {
  let cursor: Record<string, unknown> = obj

  for (let i = 0; i < keyPath.length - 1; i++) {
    const k = keyPath[i]

    if (cursor[k] == null || typeof cursor[k] !== 'object') {
      return
    }

    cursor = cursor[k] as Record<string, unknown>
  }

  delete cursor[keyPath[keyPath.length - 1]]
}
