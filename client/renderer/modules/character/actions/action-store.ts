/** 动作目录 store：水合当前激活包的 action catalog（spiritagent.action.pack），
 * 解析展示 URL、维护 clip 查找；登出清空，迟到水合丢弃。
 * 启动从本地快照先恢复再网络校准；失败保留已有目录。 */

import { atom } from 'nanostores'

import { authedApi } from '@/shared/lib/authed-api'
import { log } from '@/shared/lib/log'
import { currentClearEpoch, definePersistedAtom, registerStorageClearHandler } from '@/shared/lib/storage'
import { $auth } from '@/shared/store/auth'

import { resetActionPlayback } from './action-runtime'
import type { ActionCatalogManifest, ActionClipEntry, NormalizedRect } from './action-types'

export interface ActiveActionCatalog {
  packId: number
  catalogVersion: number
  /** 服务端外观激活代次；旧版快照未记录时为 null，网络校准前不受理播放指令。 */
  appearanceEpoch: number | null
  manifest: ActionCatalogManifest
  /** clip 标识与素材版本 → 本地展示 URL。 */
  clipUrls: Map<string, string>
  clipsById: Map<number, ActionClipEntry>
  clipsBySlot: Map<string, ActionClipEntry>
}

export type ActionCatalogStatus = 'idle' | 'loading' | 'ready' | 'unavailable'

interface CatalogWireResponse {
  pack_id?: number
  catalog_version?: number
  appearance_epoch?: number
  manifest_url?: string | null
}

interface PersistedActionCatalog {
  packId: number
  catalogVersion: number
  /** 旧版快照没有该字段。 */
  appearanceEpoch?: number
  manifest: ActionCatalogManifest | null
}

const EMPTY_PERSISTED_CATALOG: PersistedActionCatalog = {
  packId: 0,
  catalogVersion: 0,
  manifest: null
}

function isActionCatalogManifest(val: unknown): val is ActionCatalogManifest {
  if (typeof val !== 'object' || val === null) {
    return false
  }

  const m = val as Partial<ActionCatalogManifest>

  return m.schema_version === 'spiritagent.action.pack' && Array.isArray(m.clips) && typeof m.pack_id === 'number'
}

function isPersistableCatalog(val: unknown): val is PersistedActionCatalog {
  if (typeof val !== 'object' || val === null) {
    return false
  }

  const v = val as Partial<PersistedActionCatalog>

  return (
    typeof v.packId === 'number' &&
    typeof v.catalogVersion === 'number' &&
    (v.appearanceEpoch === undefined || typeof v.appearanceEpoch === 'number') &&
    isActionCatalogManifest(v.manifest)
  )
}

const catalogSnapshot = definePersistedAtom<PersistedActionCatalog>({
  fallback: EMPTY_PERSISTED_CATALOG,
  isPersistable: isPersistableCatalog,
  key: 'da.companion.actionCatalog'
})

export const $actionCatalog = atom<ActiveActionCatalog | null>(null)
export const $actionCatalogStatus = atom<ActionCatalogStatus>('idle')

let inflight: Promise<void> | null = null
let hydrationRevision = 0
const peekEnsures = new Map<string, Promise<boolean>>()
const attemptedPeekEnsures = new Set<string>()

/** 目录变更事件（companion.action.catalog_changed）到达时触发重新水合。 */
export function actionCatalogChanged(): void {
  hydrationRevision += 1
  void hydrateActionCatalog(true)
}

export async function ensurePeekAction(action: 'peek_left' | 'peek_right'): Promise<boolean> {
  if ($auth.get().kind !== 'authenticated') {
    return false
  }

  const initial = $actionCatalog.get()
  const existing = initial?.clipsBySlot.get(action)

  if (existing) {
    return Boolean(existing.peek_geometry)
  }

  if (!initial) {
    return false
  }

  const key = `${initial.packId}:${action}`
  const pending = peekEnsures.get(key)

  if (pending) {
    return pending
  }

  if (attemptedPeekEnsures.has(key)) {
    return false
  }

  const epoch = currentClearEpoch()

  const isCurrent = (): boolean =>
    epoch === currentClearEpoch() &&
    initial.packId === $actionCatalog.get()?.packId &&
    $auth.get().kind === 'authenticated'

  attemptedPeekEnsures.add(key)
  let terminal = false

  const ensure = (async (): Promise<boolean> => {
    const accepted = await authedApi<unknown>({
      body: { action },
      method: 'POST',
      path: `/api/companion/video-packs/${initial.packId}/ensure-system-action`
    })

    if (!accepted.ok || !isCurrent()) {
      return false
    }

    const deadline = Date.now() + 15 * 60_000

    while (Date.now() < deadline && isCurrent()) {
      await new Promise<void>(resolve => window.setTimeout(resolve, 5000))

      if (!isCurrent()) {
        return false
      }

      // 目录事件已交付素材时，不再查询生成任务。
      const published = $actionCatalog.get()?.clipsBySlot.get(action)

      if (published) {
        terminal = !published.peek_geometry

        return !terminal
      }

      const listed = await authedApi<{
        packs?: Array<{
          actions?: Array<{
            action: string
            peek_geometry?: ActionClipEntry['peek_geometry']
            status: string
          }>
          id: number
        }>
      }>({ path: '/api/companion/video-packs' })

      if (!isCurrent()) {
        return false
      }

      if (!listed.ok || !listed.value) {
        continue
      }

      const actionState = listed.value.packs
        ?.find(pack => pack.id === initial.packId)
        ?.actions?.find(entry => entry.action === action)

      if (
        actionState?.status === 'failed' ||
        actionState?.status === 'review' ||
        actionState?.status === 'result_unknown'
      ) {
        terminal = true

        return false
      }

      if (actionState?.status === 'succeeded') {
        if (!actionState.peek_geometry) {
          terminal = true

          return false
        }

        await hydrateActionCatalog(true)

        if (isCurrent() && $actionCatalog.get()?.clipsBySlot.get(action)?.peek_geometry) {
          return true
        }
      }
    }

    return false
  })()
    .catch(error => {
      log.warn('action-store', 'Could not ensure peek action', error)

      return false
    })
    .finally(() => {
      if (peekEnsures.get(key) === ensure) {
        peekEnsures.delete(key)
      }

      // 终态由衣柜显式处理；网络失败或目录尚未发布可复查同一服务端任务。
      if (!terminal && epoch === currentClearEpoch()) {
        attemptedPeekEnsures.delete(key)
      }
    })

  peekEnsures.set(key, ensure)

  return ensure
}

registerStorageClearHandler(() => {
  inflight = null
  peekEnsures.clear()
  attemptedPeekEnsures.clear()
  hydrationRevision += 1
  resetActionPlayback()
  $actionCatalog.set(null)
  $actionCatalogStatus.set('idle')
})

function buildCatalogIndexes(manifest: ActionCatalogManifest): {
  clipsById: Map<number, ActionClipEntry>
  clipsBySlot: Map<string, ActionClipEntry>
} {
  const clipsById = new Map<number, ActionClipEntry>()
  const clipsBySlot = new Map<string, ActionClipEntry>()

  for (const clip of manifest.clips) {
    const normalized = safeClip(clip)
    clipsById.set(normalized.action_id, normalized)

    if (normalized.system_slot) {
      clipsBySlot.set(normalized.system_slot, normalized)
    }
  }

  return { clipsById, clipsBySlot }
}

function persistCatalogSnapshot(
  packId: number,
  catalogVersion: number,
  appearanceEpoch: number,
  manifest: ActionCatalogManifest
): void {
  catalogSnapshot.reset()
  catalogSnapshot.set({ appearanceEpoch, catalogVersion, manifest, packId })
}

/** 版本参与缓存键，避免目录刷新后把新素材交给已受理的旧实例。 */
function clipKey(clip: ActionClipEntry): string {
  return `${clip.system_slot || `id:${clip.action_id}`}@${clip.asset_revision}`
}

function pickIdleClip(manifest: ActionCatalogManifest): ActionClipEntry | null {
  return manifest.clips.find(c => c.system_slot === manifest.default_action) ?? manifest.clips[0] ?? null
}

function isNormalizedRect(value: unknown): value is NormalizedRect {
  return (
    Array.isArray(value) &&
    value.length === 4 &&
    value.every(coordinate => Number.isFinite(coordinate) && coordinate >= 0 && coordinate <= 1) &&
    value[0] < value[2] &&
    value[1] < value[3]
  )
}

function isValidPeekGeometry(clip: ActionClipEntry): boolean {
  const geometry = clip.peek_geometry
  const expectedSide = clip.system_slot === 'peek_left' ? 'left' : clip.system_slot === 'peek_right' ? 'right' : null

  if (
    !geometry ||
    !expectedSide ||
    geometry.side !== expectedSide ||
    !Number.isFinite(geometry.cut_x) ||
    geometry.cut_x <= 0.1 ||
    geometry.cut_x >= 0.9 ||
    !isNormalizedRect(geometry.focus_rect)
  ) {
    return false
  }

  const [left, , right] = geometry.focus_rect

  return expectedSide === 'left' ? right < geometry.cut_x : left > geometry.cut_x
}

function safeClip(clip: ActionClipEntry): ActionClipEntry {
  const content_rect = isNormalizedRect(clip.content_rect) ? clip.content_rect : null

  if (clip.system_slot === 'peek_left' || clip.system_slot === 'peek_right') {
    return {
      ...clip,
      content_rect,
      peek_geometry: isValidPeekGeometry(clip) ? clip.peek_geometry : null
    }
  }

  return { ...clip, content_rect }
}

/** 本地快照先挂目录并标 ready；idle 预取磁盘缓存。 */
function restoreCachedActionCatalog(): void {
  if ($actionCatalog.get()) {
    return
  }

  const snap = catalogSnapshot.get()

  if (!snap.manifest || !snap.packId) {
    return
  }

  const manifest = snap.manifest
  const { clipsById, clipsBySlot } = buildCatalogIndexes(manifest)

  const catalog: ActiveActionCatalog = {
    appearanceEpoch: snap.appearanceEpoch ?? null,
    catalogVersion: snap.catalogVersion,
    clipUrls: new Map(),
    clipsById,
    clipsBySlot,
    manifest,
    packId: snap.packId
  }

  $actionCatalog.set(catalog)
  $actionCatalogStatus.set('ready')

  const idleClip = pickIdleClip(manifest)

  if (idleClip) {
    void resolveUrl(idleClip.video_ref, { cacheOnly: true }).then(url => {
      if (url && $actionCatalog.get() === catalog) {
        catalog.clipUrls.set(clipKey(idleClip), url)
      }
    })
  }
}

async function resolveUrl(rawUrl: string, opts?: { cacheOnly?: boolean }): Promise<string | null> {
  const url = rawUrl.startsWith('companion-assets/')
    ? `/api/companion/asset/${rawUrl.slice('companion-assets/'.length)}`
    : rawUrl

  if (!opts?.cacheOnly) {
    try {
      const cached = await window.spiritagent.apiAsset({ cacheOnly: true, url })

      if (cached) {
        return cached
      }
    } catch {
      // 落回 preferCache。
    }
  }

  try {
    return await window.spiritagent.apiAsset({ cacheOnly: opts?.cacheOnly, preferCache: true, url })
  } catch (err) {
    log.warn('action-store', 'asset resolve failed', err)

    return null
  }
}

export async function hydrateActionCatalog(refresh = false): Promise<void> {
  if ($auth.get().kind !== 'authenticated') {
    return
  }

  // 本地快照先顶上，网络结果只做校准。
  restoreCachedActionCatalog()

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

      if (!res.ok) {
        // 失败保留已有目录；无本地时才标 unavailable。
        if (!$actionCatalog.get()) {
          $actionCatalogStatus.set(res.reason === 'unauth' ? 'idle' : 'unavailable')
        }

        return
      }

      const manifestUrl = res.value?.manifest_url

      if (!res.value?.pack_id || !manifestUrl) {
        // 服务端确认无包：以真源清空。
        catalogSnapshot.reset()
        $actionCatalog.set(null)
        $actionCatalogStatus.set('unavailable')

        return
      }

      const samePack = $actionCatalog.get()
      // 旧版服务端不返回代次，其播放指令代次恒为 0。
      const appearanceEpoch = res.value.appearance_epoch ?? 0

      if (samePack && samePack.packId === res.value.pack_id && samePack.catalogVersion === res.value.catalog_version) {
        // 同一目录仅代次变化（重新激活或旧快照校准）：只更新代次，并作废旧代次的播放实例。
        if (samePack.appearanceEpoch !== appearanceEpoch) {
          resetActionPlayback()
          $actionCatalog.set({ ...samePack, appearanceEpoch })
          persistCatalogSnapshot(samePack.packId, samePack.catalogVersion, appearanceEpoch, samePack.manifest)
        }

        $actionCatalogStatus.set('ready')

        return
      }

      const localUrl = await resolveUrl(manifestUrl)

      if (epoch !== currentClearEpoch() || revision !== hydrationRevision) {
        return
      }

      if (!localUrl) {
        if (!$actionCatalog.get()) {
          $actionCatalogStatus.set('unavailable')
        }

        return
      }

      // localUrl 是 apiAsset 桥返回的 data URL，非后端相对路径。
      // eslint-disable-next-line no-restricted-syntax
      const resp = await fetch(localUrl)
      const manifest: unknown = await resp.json()

      if (!resp.ok || !isActionCatalogManifest(manifest)) {
        log.warn('action-store', 'invalid catalog manifest')

        if (!$actionCatalog.get()) {
          $actionCatalog.set(null)
          $actionCatalogStatus.set('unavailable')
        }

        return
      }

      // 预取默认动作（idle）片段；其余按需解析。
      const idleClip = pickIdleClip(manifest)
      const idleUrl = idleClip ? await resolveUrl(idleClip.video_ref) : null

      if (epoch !== currentClearEpoch() || revision !== hydrationRevision) {
        return
      }

      if (!idleUrl) {
        if (!$actionCatalog.get()) {
          $actionCatalog.set(null)
          $actionCatalogStatus.set('unavailable')
        }

        return
      }

      const clipUrls = new Map<string, string>()

      if (idleClip) {
        clipUrls.set(clipKey(idleClip), idleUrl)
      }

      const { clipsById, clipsBySlot } = buildCatalogIndexes(manifest)

      // 换包或外观代次变化时清理旧播放实例；同代次目录刷新保留在播实例。
      if (!samePack || samePack.packId !== res.value.pack_id || samePack.appearanceEpoch !== appearanceEpoch) {
        resetActionPlayback()
      }

      const catalogVersion = res.value.catalog_version ?? manifest.catalog_version

      $actionCatalog.set({
        appearanceEpoch,
        catalogVersion,
        clipUrls,
        clipsById,
        clipsBySlot,
        manifest,
        packId: res.value.pack_id
      })
      $actionCatalogStatus.set('ready')
      persistCatalogSnapshot(res.value.pack_id, catalogVersion, appearanceEpoch, manifest)
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

/** 遮罩 payload 为 `[frame][row]` 列位行；网格与帧率取自目录元数据，列数须能落入 32 位行。 */
function parseHitmask(raw: unknown, clip: ActionClipEntry): ActionHitmask | null {
  const grid = clip.hitmask_grid ?? [32, 32]
  const frameRate = clip.hitmask_fps

  if (
    !Number.isInteger(grid[0]) ||
    grid[0] < 1 ||
    grid[0] > 32 ||
    !Number.isInteger(grid[1]) ||
    grid[1] < 1 ||
    !Number.isFinite(frameRate) ||
    frameRate <= 0 ||
    !Array.isArray(raw) ||
    raw.length === 0 ||
    !raw.every(
      frame =>
        Array.isArray(frame) &&
        frame.length === grid[1] &&
        frame.every(row => Number.isInteger(row) && row >= 0 && row <= 0xffffffff)
    )
  ) {
    return null
  }

  return { frames: raw as number[][], fps: frameRate, grid: [grid[0], grid[1]] }
}

/** 按需加载命中遮罩；非法或失败返回 null（命中降级为容器/内容边界）。 */
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
    const hitmask = parseHitmask(await resp.json(), clip)

    if (!hitmask) {
      hitmaskCache.set(clip.hitmask_ref, null)

      return null
    }

    hitmaskCache.set(clip.hitmask_ref, hitmask)

    return hitmask
  } catch (err) {
    log.warn('action-store', 'hitmask resolve failed', err)
    hitmaskCache.set(clip.hitmask_ref, null)

    return null
  }
}

// 模块加载即恢复快照，不等窗口水合。
restoreCachedActionCatalog()
