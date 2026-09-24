/** 动作目录 store：水合当前激活包的 action catalog（spiritagent.action.pack），
 * 解析展示 URL、维护 clip 查找；登出清空，迟到水合丢弃。 */

import { atom } from 'nanostores'

import { authedApi } from '@/shared/lib/authed-api'
import { log } from '@/shared/lib/log'
import { currentClearEpoch, registerStorageClearHandler } from '@/shared/lib/storage'
import { $auth } from '@/shared/store/auth'

import { nextAppearanceEpoch } from './action-runtime'
import type { ActionCatalogManifest, ActionClipEntry } from './action-types'

export interface ActiveActionCatalog {
  packId: number
  catalogVersion: number
  manifest: ActionCatalogManifest
  /** clip 标识（system_slot 或 `id:动作ID`）→ 本地展示 URL。 */
  clipUrls: Map<string, string>
  /** action_id → clip 条目。 */
  clipsById: Map<number, ActionClipEntry>
  /** system_slot → clip 条目。 */
  clipsBySlot: Map<string, ActionClipEntry>
}

export type ActionCatalogStatus = 'idle' | 'loading' | 'ready' | 'unavailable'

interface CatalogWireResponse {
  pack_id?: number
  catalog_version?: number
  manifest_url?: string | null
}

export const $actionCatalog = atom<ActiveActionCatalog | null>(null)
export const $actionCatalogStatus = atom<ActionCatalogStatus>('idle')

let inflight: Promise<void> | null = null
let hydrationRevision = 0

/** 目录变更事件（companion.action.catalog_changed）到达时触发重新水合。 */
export function actionCatalogChanged(): void {
  hydrationRevision += 1
  void hydrateActionCatalog(true)
}

registerStorageClearHandler(() => {
  inflight = null
  hydrationRevision += 1
  nextAppearanceEpoch()
  $actionCatalog.set(null)
  $actionCatalogStatus.set('idle')
})

async function resolveUrl(rawUrl: string): Promise<string | null> {
  const url = rawUrl.startsWith('companion-assets/')
    ? `/api/companion/asset/${rawUrl.slice('companion-assets/'.length)}`
    : rawUrl

  try {
    return await window.spiritagent.apiAsset({ url, preferCache: true })
  } catch (err) {
    log.warn('action-store', 'asset resolve failed', err)

    return null
  }
}

export async function hydrateActionCatalog(refresh = false): Promise<void> {
  if ($auth.get().kind !== 'authenticated') {
    return
  }

  if (inflight) {
    if (refresh) {
      const epoch = currentClearEpoch()
      await inflight

      if (epoch === currentClearEpoch()) {
        await hydrateActionCatalog()
      }
    }

    return
  }

  const epoch = currentClearEpoch()
  const revision = hydrationRevision

  const load = (async (): Promise<void> => {
    if (!$actionCatalog.get()) {
      $actionCatalogStatus.set('loading')
    }

    try {
      const res = await authedApi<CatalogWireResponse>({ path: '/api/companion/actions/catalog' })

      if (epoch !== currentClearEpoch() || revision !== hydrationRevision) {
        return
      }

      if (!res.ok || res.value === null) {
        $actionCatalog.set(null)
        $actionCatalogStatus.set('unavailable')

        return
      }

      const manifestUrl = res.value.manifest_url

      if (!res.value.pack_id || !manifestUrl) {
        $actionCatalog.set(null)
        $actionCatalogStatus.set('unavailable')

        return
      }

      const samePack = $actionCatalog.get()

      if (samePack && samePack.packId === res.value.pack_id && samePack.catalogVersion === res.value.catalog_version) {
        $actionCatalogStatus.set('ready')

        return
      }

      const localUrl = await resolveUrl(manifestUrl)

      if (epoch !== currentClearEpoch() || revision !== hydrationRevision || !localUrl) {
        return
      }

      // localUrl 是 apiAsset 桥返回的本地资产 URL（spiritagent-media:// 或 blob），非后端相对路径。
      // eslint-disable-next-line no-restricted-syntax
      const resp = await fetch(localUrl)
      const manifest = (await resp.json()) as ActionCatalogManifest

      if (manifest.schema_version !== 'spiritagent.action.pack' || !Array.isArray(manifest.clips)) {
        log.warn('action-store', 'unsupported catalog schema')
        $actionCatalog.set(null)
        $actionCatalogStatus.set('unavailable')

        return
      }

      // 预取默认动作（idle）片段；其余按需解析。
      const idleClip = manifest.clips.find(c => c.system_slot === manifest.default_action) ?? manifest.clips[0]
      const idleUrl = idleClip ? await resolveUrl(idleClip.video_ref) : null

      if (epoch !== currentClearEpoch() || revision !== hydrationRevision) {
        return
      }

      if (!idleUrl) {
        $actionCatalog.set(null)
        $actionCatalogStatus.set('unavailable')

        return
      }

      const clipUrls = new Map<string, string>()

      if (idleClip) {
        clipUrls.set(clipKey(idleClip), idleUrl)
      }

      const clipsById = new Map<number, ActionClipEntry>()
      const clipsBySlot = new Map<string, ActionClipEntry>()

      for (const clip of manifest.clips) {
        clipsById.set(clip.action_id, clip)

        if (clip.system_slot) {
          clipsBySlot.set(clip.system_slot, clip)
        }
      }

      // 换外观（packId 变化）才推进世代：旧播放实例与迟到回调失效；
      // 同包新增动作/目录刷新不推进——在播实例仍有效，避免无故打断。
      if (!samePack || samePack.packId !== res.value.pack_id) {
        nextAppearanceEpoch()
      }

      $actionCatalog.set({
        packId: res.value.pack_id,
        catalogVersion: res.value.catalog_version ?? manifest.catalog_version,
        manifest,
        clipUrls,
        clipsById,
        clipsBySlot
      })
      $actionCatalogStatus.set('ready')
    } catch (err) {
      log.warn('action-store', 'hydrateActionCatalog failed', err)

      if (epoch === currentClearEpoch() && !$actionCatalog.get()) {
        $actionCatalogStatus.set('unavailable')
      }
    } finally {
      if (epoch === currentClearEpoch()) {
        inflight = null

        if (revision !== hydrationRevision) {
          void hydrateActionCatalog()
        }
      }
    }
  })()

  inflight = load

  return load
}

/** clip 缓存键：系统槽位优先，动态动作用 `id:<action_id>`。 */
function clipKey(clip: ActionClipEntry): string {
  return clip.system_slot || `id:${clip.action_id}`
}

/** 按需解析动作片段展示 URL；失败返回 null（渲染层回退默认动作）。 */
export async function resolveActionClipUrl(
  catalog: ActiveActionCatalog,
  clip: ActionClipEntry
): Promise<string | null> {
  const key = clipKey(clip)
  const cached = catalog.clipUrls.get(key)

  if (cached) {
    return cached
  }

  const url = await resolveUrl(clip.video_ref)

  if (url) {
    catalog.clipUrls.set(key, url)
  }

  return url
}

/** 逐帧 alpha 命中遮罩：独立可缓存资源（hitmask_ref 指向 JSON）。 */
export interface ActionHitmask {
  readonly grid: readonly [number, number]
  readonly fps: number
  readonly frames: readonly (readonly number[])[]
}

const hitmaskCache = new Map<string, ActionHitmask | null>()

registerStorageClearHandler(() => {
  hitmaskCache.clear()
})

/** 按需加载命中遮罩；无引用或失败返回 null（渲染层命中探测降级为容器矩形）。 */
export async function resolveHitmask(clip: ActionClipEntry): Promise<ActionHitmask | null> {
  if (!clip.hitmask_ref) {
    return null
  }

  if (hitmaskCache.has(clip.hitmask_ref)) {
    return hitmaskCache.get(clip.hitmask_ref) ?? null
  }

  try {
    const localUrl = await resolveUrl(clip.hitmask_ref)

    if (!localUrl) {
      hitmaskCache.set(clip.hitmask_ref, null)

      return null
    }

    // 本地资产 URL（apiAsset 桥产物），非后端相对路径。
    // eslint-disable-next-line no-restricted-syntax
    const resp = await fetch(localUrl)
    const data = (await resp.json()) as { grid?: [number, number]; fps?: number; frames?: number[][] }

    if (!data.grid || !data.frames?.length) {
      hitmaskCache.set(clip.hitmask_ref, null)

      return null
    }

    const hitmask: ActionHitmask = {
      grid: [data.grid[0], data.grid[1]],
      fps: data.fps ?? clip.hitmask_fps,
      frames: data.frames
    }

    hitmaskCache.set(clip.hitmask_ref, hitmask)

    return hitmask
  } catch (err) {
    log.warn('action-store', 'hitmask resolve failed', err)
    hitmaskCache.set(clip.hitmask_ref, null)

    return null
  }
}
