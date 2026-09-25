import { type DesktopPrefsHydrated, normalizeUiTheme, type SpiritAgentUiTheme } from '@ipc/contracts'

import type { BackendClientPort } from '../backend-port'
import { errorMessage } from '../utils'

import * as store from './runner-config-store'

// 云端 ⇄ 本地镜像的同步节白名单（desktop-settings.json 顶层节）。
// 不在名单内的节（terminal、spiritagent 等机密/设备相关节与未知节）永不离开本机（PROTOCOL §2.4/§5.3）。
// "language" 是顶层原始值键（与后端 user_settings.setting_key 对齐），由 hydrate/pick 单独按原始值处理。
const SYNCED_SECTIONS = [
  'skills',
  'toolsets',
  'browser',
  'security',
  'debug',
  'tool_output',
  'computer_use',
  'file_state',
  'companion',
  'shortcuts',
  'ui'
] as const

// 顶层原始值同步键：值为字符串/数字/布尔等非对象类型时直接透传。
const SYNCED_PRIMITIVES = ['language'] as const

// 同步节内的本机专属键：上传时剔除；云端永不包含这些键，水合合并时本地值自然保留。
const LOCAL_ONLY_KEYS: Record<string, readonly string[]> = {
  browser: ['profile_dir']
}

// 镜像归属不匹配时清空同步节，避免跨账户上传。
interface MirrorStamp {
  account_id?: null | string
}

const FLUSH_DEBOUNCE_MS = 1500
const RETRY_BACKOFF_INITIAL_MS = 5000
const RETRY_BACKOFF_MAX_MS = 60000

interface ConfigSyncConnection {
  baseUrl: string
  token: null | string
}

export interface ConfigSyncDeps {
  createBackendClient: (options: { baseUrl: string }) => BackendClientPort
  ensureBackend: () => Promise<ConfigSyncConnection>
  /** 网络/5xx 等可重试错误；由 entry 注入 BackendRequestError 判定，shared 不依赖 backend。 */
  isRetryableError: (error: unknown) => boolean
  log: (chunk: string) => void
  onHydrated: (payload: DesktopPrefsHydrated) => void
}

export interface ConfigSync {
  /** runner-config-store 的本地变更委托；在 store 写锁内同步调用，必须非阻塞。 */
  onLocalChange: (config: Record<string, unknown>) => void
  /** 用户身份变化（登录/登出/换号）；跨后端同号也须视为不同身份。 */
  handleAuthUserChanged: (accountId: null | string) => Promise<void>
  flush: () => Promise<void>
}

/** 从 store 镜像（或任意 config 记录）取出单个对象节，非对象 / 数组一律回落 {}。 */
function objectSection(config: Record<string, unknown>, section: string): Record<string, unknown> {
  const value = config[section]

  return value != null && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {}
}

/** 从配置镜像构造渲染层实际消费的 prefs-hydrated 载荷。 */
export function buildPrefsHydratedFromConfig(config: Record<string, unknown>): DesktopPrefsHydrated {
  return {
    companion: objectSection(config, 'companion'),
    language: typeof config.language === 'string' && config.language.length > 0 ? config.language : null
  }
}

/** 主进程水合后的主题投影；与渲染层偏好广播分开，仅供主进程副作用使用。 */
export function uiThemeFromConfig(config: Record<string, unknown>): SpiritAgentUiTheme | undefined {
  const theme = objectSection(config, 'ui').theme

  return typeof theme === 'string' && theme.length > 0 ? normalizeUiTheme(theme) : undefined
}

export function createConfigSync(deps: ConfigSyncDeps): ConfigSync {
  let client: null | BackendClientPort = null
  let clientBaseUrl = ''
  let dirty = false
  let hydratedAccountId: null | string = null
  let hydrating = false
  let flushing = false
  let flushTimer: null | NodeJS.Timeout = null
  let retryTimer: null | NodeJS.Timeout = null
  let backoffMs = RETRY_BACKOFF_INITIAL_MS
  let lastFlushedJson = '{}'
  // 换号/登出时递增：在途 flush/hydrate 完成前若 epoch 已变则丢弃结果。
  let authEpoch = 0

  function backendClient(baseUrl: string): BackendClientPort {
    if (!client || clientBaseUrl !== baseUrl) {
      client = deps.createBackendClient({ baseUrl })
      clientBaseUrl = baseUrl
    }

    return client
  }

  function clearTimers(): void {
    if (flushTimer !== null) {
      clearTimeout(flushTimer)
      flushTimer = null
    }

    if (retryTimer !== null) {
      clearTimeout(retryTimer)
      retryTimer = null
    }
  }

  function scheduleRetry(task: () => void): void {
    if (retryTimer !== null) {
      return
    }

    retryTimer = setTimeout(() => {
      retryTimer = null
      task()
    }, backoffMs)
    backoffMs = Math.min(backoffMs * 2, RETRY_BACKOFF_MAX_MS)
  }

  function stripLocalOnly(section: string, value: Record<string, unknown>): Record<string, unknown> {
    const drop = LOCAL_ONLY_KEYS[section]

    if (!drop) {
      return value
    }

    const out = { ...value }

    for (const key of drop) {
      delete out[key]
    }

    return out
  }

  /** 从完整配置挑出要上云的节（剔除本机专属键与空节；顶层原始值键原样透传）。 */
  function pickSyncedSections(config: Record<string, unknown>): Record<string, unknown> {
    const out: Record<string, unknown> = {}

    for (const section of SYNCED_SECTIONS) {
      const value = objectSection(config, section)

      if (Object.keys(value).length === 0) {
        continue
      }

      const stripped = stripLocalOnly(section, value)

      if (Object.keys(stripped).length > 0) {
        out[section] = stripped
      }
    }

    for (const key of SYNCED_PRIMITIVES) {
      const value = config[key]

      if (value != null) {
        out[key] = value
      }
    }

    return out
  }

  function onLocalChange(config: Record<string, unknown>): void {
    const json = JSON.stringify(pickSyncedSections(config))

    // 与最近一次成功上云的内容一致（如仅机密节或本机键变动）——无需再排一次上传。
    if (json === lastFlushedJson) {
      return
    }

    dirty = true

    if (flushTimer === null) {
      flushTimer = setTimeout(() => {
        flushTimer = null
        void flush()
      }, FLUSH_DEBOUNCE_MS)
    }
  }

  async function flush(): Promise<void> {
    if (flushing || !dirty) {
      return
    }

    flushing = true
    const epoch = authEpoch

    try {
      const conn = await deps.ensureBackend()

      // 未登录或身份已切换：挂起，避免旧账号配置写入新账号。
      if (epoch !== authEpoch || !conn.token) {
        return
      }

      const payload = pickSyncedSections(store.read())

      if (Object.keys(payload).length === 0) {
        dirty = false
        lastFlushedJson = '{}'

        return
      }

      await backendClient(conn.baseUrl).put('/api/config', { body: { config: payload }, token: conn.token })

      if (epoch !== authEpoch) {
        return
      }

      dirty = false
      backoffMs = RETRY_BACKOFF_INITIAL_MS
      lastFlushedJson = JSON.stringify(payload)
    } catch (error) {
      if (epoch !== authEpoch) {
        return
      }

      if (deps.isRetryableError(error)) {
        scheduleRetry(() => void flush())
      } else {
        // 鉴权失败（等 authChanged）或 4xx（载荷被拒，重试无意义）：挂起并保留 dirty。
        deps.log(`[config-sync] flush parked: ${errorMessage(error)}`)
      }
    } finally {
      flushing = false
    }
  }

  /**
   * 云端 → 本地镜像水合：GET /api/config 后按同步节白名单逐键 upsert 合并
   * （云端值覆盖同名键；云端缺失的键保留本地，本机专属键因此存活）。
   * 本地存在而云端缺失的键（首跑播种、退出时未及上云的编辑）会后置一次 flush 上传。
   */
  async function hydrate(): Promise<void> {
    if (hydrating) {
      return
    }

    hydrating = true
    const epoch = authEpoch
    const accountId = hydratedAccountId

    try {
      // 未上云的本地编辑先落云，避免被云端旧值覆盖；失败（离线）则保留本地下次再试。
      if (dirty) {
        await flush()

        if (dirty || epoch !== authEpoch) {
          return
        }
      }

      const conn = await deps.ensureBackend()

      if (epoch !== authEpoch || !conn.token || accountId === null) {
        return
      }

      const res = await backendClient(conn.baseUrl).get<{ config: Record<string, unknown> }>('/api/config', {
        token: conn.token
      })

      if (epoch !== authEpoch) {
        return
      }

      const cloud = pickSyncedSections(res.config ?? {})
      const local = store.read()
      const stamp = objectSection(local, 'sync') as MirrorStamp
      const trusted = stamp.account_id === accountId

      if (!trusted) {
        await store.mutate(
          config => {
            if (epoch !== authEpoch) {
              return
            }

            for (const section of SYNCED_SECTIONS) {
              delete config[section]
            }

            for (const key of SYNCED_PRIMITIVES) {
              delete config[key]
            }
          },
          { pushRunner: false }
        )
      }

      if (epoch !== authEpoch) {
        return
      }

      const fresh = store.read()
      const changed: Record<string, unknown> = {}
      let seed = false

      for (const section of SYNCED_SECTIONS) {
        const localSec = objectSection(fresh, section)
        const cloudSecRaw = cloud[section]

        if (cloudSecRaw == null) {
          if (trusted && Object.keys(stripLocalOnly(section, localSec)).length > 0) {
            seed = true
          }

          continue
        }

        const cloudSec = objectSection(cloud, section)

        for (const key of Object.keys(stripLocalOnly(section, localSec))) {
          if (!(key in cloudSec) && trusted) {
            seed = true
          }
        }

        const merged = { ...localSec, ...cloudSec }

        if (JSON.stringify(merged) !== JSON.stringify(localSec)) {
          changed[section] = merged
        }
      }

      // 顶层原始值同步键（如 "language"）：last-write-wins，云端覆盖本地。
      for (const key of SYNCED_PRIMITIVES) {
        const localVal = fresh[key]
        const cloudVal = cloud[key]

        if (cloudVal == null) {
          if (localVal != null && trusted) {
            seed = true
          }

          continue
        }

        if (localVal !== cloudVal) {
          changed[key] = cloudVal
        }
      }

      // 节有变化或归属戳缺失/过期时落盘（含戳），否则零写入。
      if (Object.keys(changed).length > 0 || !trusted) {
        changed.sync = { account_id: accountId }
        await store.applyCloudMirror(changed, () => epoch === authEpoch)
      }

      if (epoch !== authEpoch) {
        return
      }

      deps.onHydrated(buildPrefsHydratedFromConfig(store.read()))

      if (seed && !dirty) {
        dirty = true
        void flush()
      }
    } catch (error) {
      if (epoch === authEpoch) {
        deps.log(`[config-sync] hydrate failed: ${errorMessage(error)}`)

        if (deps.isRetryableError(error)) {
          scheduleRetry(() => void hydrate())
        }
      }
    } finally {
      hydrating = false

      // 在途 hydrate 期间换号：补跑当前用户水合，避免被吞掉。
      if (epoch !== authEpoch && hydratedAccountId !== null) {
        void hydrate()
      }
    }
  }

  async function handleAuthUserChanged(accountId: null | string): Promise<void> {
    if (accountId === hydratedAccountId) {
      return
    }

    clearTimers()
    dirty = false
    lastFlushedJson = '{}'
    hydratedAccountId = accountId
    authEpoch++

    if (accountId !== null) {
      const epoch = authEpoch
      const stamp = objectSection(store.read(), 'sync') as MirrorStamp

      if (stamp.account_id !== accountId) {
        const result = await store.mutate(
          config => {
            if (epoch !== authEpoch) {
              return
            }

            for (const section of SYNCED_SECTIONS) {
              delete config[section]
            }

            for (const key of SYNCED_PRIMITIVES) {
              delete config[key]
            }

            config.sync = { account_id: accountId }
          },
          { pushRunner: false }
        )

        if (!result.ok) {
          deps.log(`[config-sync] account isolation persistence failed: ${result.error || 'unknown'}`)
        }
      }

      if (epoch === authEpoch) {
        void hydrate()
      }
    }
  }

  return { flush, handleAuthUserChanged, onLocalChange }
}
