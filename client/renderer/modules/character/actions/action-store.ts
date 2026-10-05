/** 动作目录 store：水合激活包 catalog、解析展示 URL 与 clip 查找；账户快照先恢复再网络校准，失败保留已有目录。 */

import { sleep } from '@runtime'
import { atom } from 'nanostores'

import { apiSucceeded, authedApi } from '@/shared/lib/authed-api'
import { isRecord } from '@/shared/lib/is-record'
import { log } from '@/shared/lib/log'
import { currentClearEpoch, definePersistedAtom, registerStorageClearHandler } from '@/shared/lib/storage'
import { $auth } from '@/shared/store/auth'

import { resetActionPlayback } from './action-runtime'
import type {
  ActionCatalogManifest,
  ActionClipEntry,
  ActionPackWire,
  NormalizedRect,
  PeekGeometry
} from './action-types'

export interface ActiveActionCatalog {
  packId: number
  catalogVersion: number
  /** 服务端外观激活代次；快照未记录时为 null，网络校准前不受理播放指令。 */
  appearanceEpoch: number | null
  manifest: ActionCatalogManifest
  /** clip 标识与素材版本 → 在途读取或已就绪的展示 URL；失败后移除。 */
  clipUrls: Map<string, Promise<string | null>>
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
  /** 可缺省；未记录的快照按未知（null）恢复。 */
  appearanceEpoch?: number
  manifest: ActionCatalogManifest | null
}

const EMPTY_PERSISTED_CATALOG: PersistedActionCatalog = {
  packId: 0,
  catalogVersion: 0,
  manifest: null
}

function isActionCatalogManifest(val: unknown): val is ActionCatalogManifest {
  return (
    isRecord(val) &&
    isRecord(val.canvas) &&
    [val.canvas.width, val.canvas.height].every(isPositiveInteger) &&
    !('fps' in val.canvas) &&
    isPositiveInteger(val.pack_id) &&
    isPositiveInteger(val.catalog_version) &&
    (val.outfit_id === null || isPositiveInteger(val.outfit_id)) &&
    (val.cover_path === null || typeof val.cover_path === 'string') &&
    typeof val.default_action === 'string' &&
    val.default_action.length > 0 &&
    Array.isArray(val.clips) &&
    val.clips.every(isActionClipEntry) &&
    val.clips.some(clip => clip.system_slot === val.default_action)
  )
}

function isPositiveInteger(value: unknown): boolean {
  return typeof value === 'number' && Number.isSafeInteger(value) && value > 0
}

function isHitmaskGrid(value: unknown): value is readonly [number, number] {
  return Array.isArray(value) && value.length === 2 && value.every(isPositiveInteger) && value[0] <= 32
}

function isActionClipEntry(value: unknown): boolean {
  if (
    !isRecord(value) ||
    ![value.width, value.height, value.action_id, value.asset_revision].every(isPositiveInteger) ||
    typeof value.system_slot !== 'string' ||
    typeof value.media_ref !== 'string' ||
    !value.media_ref ||
    !(value.hitmask_ref === null || (typeof value.hitmask_ref === 'string' && value.hitmask_ref.length > 0)) ||
    !(value.hitmask_grid === null || isHitmaskGrid(value.hitmask_grid))
  ) {
    return false
  }

  if (value.media_type === 'image') {
    return ['duration_ms', 'frames', 'fps', 'loopable', 'repeat_count', 'hitmask_fps'].every(key => !(key in value))
  }

  return (
    value.media_type === 'video' &&
    typeof value.duration_ms === 'number' &&
    Number.isFinite(value.duration_ms) &&
    value.duration_ms > 0 &&
    isPositiveInteger(value.frames) &&
    typeof value.loopable === 'boolean' &&
    typeof value.hitmask_fps === 'number' &&
    Number.isFinite(value.hitmask_fps) &&
    value.hitmask_fps > 0
  )
}

function isPersistableCatalog(val: unknown): val is PersistedActionCatalog {
  return (
    isRecord(val) &&
    typeof val.packId === 'number' &&
    typeof val.catalogVersion === 'number' &&
    (val.appearanceEpoch === undefined || typeof val.appearanceEpoch === 'number') &&
    isActionCatalogManifest(val.manifest) &&
    val.packId === val.manifest.pack_id &&
    val.catalogVersion === val.manifest.catalog_version
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

  if (!initial) {
    return false
  }

  const existing = initial.clipsBySlot.get(action)

  if (existing) {
    return Boolean(existing.peek_geometry)
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

    if (!isCurrent() || !apiSucceeded(accepted, 'action-store', 'ensure-system-action request failed')) {
      return false
    }

    const deadline = Date.now() + 15 * 60_000

    while (Date.now() < deadline && isCurrent()) {
      await sleep(5000)

      if (!isCurrent()) {
        return false
      }

      // 目录事件已交付素材时，不再查询生成任务。
      const published = $actionCatalog.get()?.clipsBySlot.get(action)

      if (published) {
        terminal = !published.peek_geometry

        return !terminal
      }

      const listed = await authedApi<{ packs?: ActionPackWire[] }>({ path: '/api/companion/video-packs' })

      if (!isCurrent()) {
        return false
      }

      // 轮询在预算内重试，单次失败不记日志。
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

function createCatalog(
  manifest: ActionCatalogManifest,
  meta: Pick<ActiveActionCatalog, 'appearanceEpoch' | 'catalogVersion' | 'packId'>
): ActiveActionCatalog {
  const clipsById = new Map<number, ActionClipEntry>()
  const clipsBySlot = new Map<string, ActionClipEntry>()

  for (const clip of manifest.clips) {
    const normalized = safeClip(clip)
    clipsById.set(normalized.action_id, normalized)

    if (normalized.system_slot) {
      clipsBySlot.set(normalized.system_slot, normalized)
    }
  }

  return { ...meta, clipUrls: new Map(), clipsById, clipsBySlot, manifest }
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

function isValidPeekGeometry(clip: ActionClipEntry, expectedSide: PeekGeometry['side']): boolean {
  const geometry = clip.peek_geometry

  if (
    !geometry ||
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
    const side = clip.system_slot === 'peek_left' ? 'left' : 'right'

    return {
      ...clip,
      content_rect,
      peek_geometry: isValidPeekGeometry(clip, side) ? clip.peek_geometry : null
    }
  }

  return { ...clip, content_rect }
}

/** 本地快照先挂目录并标 ready；播放器按需读取本机缓存。 */
function restoreCachedActionCatalog(): void {
  if ($actionCatalog.get()) {
    return
  }

  const snap = catalogSnapshot.get()

  if (!snap.manifest || !snap.packId) {
    return
  }

  $actionCatalog.set(
    createCatalog(snap.manifest, {
      appearanceEpoch: snap.appearanceEpoch ?? null,
      catalogVersion: snap.catalogVersion,
      packId: snap.packId
    })
  )
  $actionCatalogStatus.set('ready')
}

function assetUrl(rawUrl: string): string {
  return rawUrl.startsWith('companion-assets/')
    ? `/api/companion/asset/${rawUrl.slice('companion-assets/'.length)}`
    : rawUrl
}

async function resolveUrl(rawUrl: string): Promise<string | null> {
  try {
    return await window.spiritagent.apiAsset({ preferCache: true, url: assetUrl(rawUrl) })
  } catch (err) {
    log.warn('action-store', 'asset resolve failed', err)

    return null
  }
}

async function readJsonAsset(rawUrl: string): Promise<unknown> {
  const buffer = await window.spiritagent.apiAssetBuffer({ preferCache: true, url: assetUrl(rawUrl) })

  return JSON.parse(new TextDecoder().decode(buffer))
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
  const stale = (): boolean => epoch !== currentClearEpoch() || revision !== hydrationRevision

  const markUnavailableIfEmpty = (): void => {
    if (!$actionCatalog.get()) {
      $actionCatalogStatus.set('unavailable')
    }
  }

  const load = (async (): Promise<void> => {
    if (!$actionCatalog.get()) {
      $actionCatalogStatus.set('loading')
    }

    try {
      const res = await authedApi<CatalogWireResponse>({ path: '/api/companion/actions/catalog' })

      if (stale()) {
        return
      }

      if (!apiSucceeded(res, 'action-store', 'catalog request failed')) {
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
      // 不返回代次的服务端，其播放指令代次恒为 0，缺省按 0 对齐。
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

      const manifest = await readJsonAsset(manifestUrl)

      if (stale()) {
        return
      }

      if (
        !isActionCatalogManifest(manifest) ||
        manifest.pack_id !== res.value.pack_id ||
        (res.value.catalog_version !== undefined && manifest.catalog_version !== res.value.catalog_version)
      ) {
        log.warn('action-store', 'invalid catalog manifest')
        markUnavailableIfEmpty()

        return
      }

      const catalogVersion = res.value.catalog_version ?? manifest.catalog_version
      const catalog = createCatalog(manifest, { appearanceEpoch, catalogVersion, packId: res.value.pack_id })

      // 预取默认动作（idle）片段；其余按需解析。
      const idleClip = pickIdleClip(manifest)
      const idleUrl = idleClip ? await resolveActionClipUrl(catalog, idleClip) : null

      if (stale()) {
        return
      }

      if (!idleUrl) {
        markUnavailableIfEmpty()

        return
      }

      // 换包或外观代次变化时清理旧播放实例；同代次目录刷新保留在播实例。
      if (!samePack || samePack.packId !== res.value.pack_id || samePack.appearanceEpoch !== appearanceEpoch) {
        resetActionPlayback()
      }

      $actionCatalog.set(catalog)
      $actionCatalogStatus.set('ready')
      persistCatalogSnapshot(res.value.pack_id, catalogVersion, appearanceEpoch, manifest)
    } catch (err) {
      log.warn('action-store', 'hydrateActionCatalog failed', err)

      if (epoch === currentClearEpoch()) {
        markUnavailableIfEmpty()
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

  const epoch = currentClearEpoch()

  const load = resolveUrl(clip.media_ref).then(url => {
    if (!url || epoch !== currentClearEpoch()) {
      if (catalog.clipUrls.get(key) === load) {
        catalog.clipUrls.delete(key)
      }

      return null
    }

    return url
  })

  catalog.clipUrls.set(key, load)

  return load
}

/** 图片保存静态行位图，视频保存逐帧行位图；hitmask_ref 指向独立 JSON。 */
export type ActionHitmask =
  | { readonly media_type: 'image'; readonly grid: readonly [number, number]; readonly rows: readonly number[] }
  | {
      readonly media_type: 'video'
      readonly grid: readonly [number, number]
      readonly fps: number
      readonly frames: readonly (readonly number[])[]
    }

const hitmaskCache = new Map<string, Promise<ActionHitmask | null>>()

registerStorageClearHandler(() => {
  hitmaskCache.clear()
})

/** 遮罩 payload 为图片的 `[row]` 或视频的 `[frame][row]`；列数须能落入 32 位行。 */
function parseHitmask(raw: unknown, clip: ActionClipEntry): ActionHitmask | null {
  const grid = clip.hitmask_grid ?? [32, 32]

  if (!isHitmaskGrid(grid) || !Array.isArray(raw)) {
    return null
  }

  const validRows = (rows: unknown): rows is number[] =>
    Array.isArray(rows) &&
    rows.length === grid[1] &&
    rows.every(row => Number.isInteger(row) && row >= 0 && row <= 0xffffffff)

  if (clip.media_type === 'image') {
    return validRows(raw) ? { media_type: 'image', rows: raw, grid: [grid[0], grid[1]] } : null
  }

  return raw.length > 0 && raw.every(validRows) && Number.isFinite(clip.hitmask_fps) && clip.hitmask_fps > 0
    ? { media_type: 'video', frames: raw, fps: clip.hitmask_fps, grid: [grid[0], grid[1]] }
    : null
}

/** 按需加载命中遮罩；非法或失败返回 null（命中降级为容器/内容边界）。 */
export async function resolveHitmask(clip: ActionClipEntry): Promise<ActionHitmask | null> {
  if (!clip.hitmask_ref) {
    return null
  }

  const ref = clip.hitmask_ref
  const key = `${clip.media_type}:${ref}:${clip.hitmask_grid?.join(',') ?? '32,32'}${clip.media_type === 'video' ? `:${clip.hitmask_fps}` : ''}`
  const cached = hitmaskCache.get(key)

  if (cached) {
    return cached
  }

  const epoch = currentClearEpoch()

  const load = (async () => {
    try {
      const raw = await readJsonAsset(ref)

      return epoch === currentClearEpoch() ? parseHitmask(raw, clip) : null
    } catch (err) {
      log.warn('action-store', 'hitmask resolve failed', err)

      return null
    }
  })().then(hitmask => {
    if (!hitmask && hitmaskCache.get(key) === load) {
      hitmaskCache.delete(key)
    }

    return hitmask
  })

  // 在途请求与结果共用缓存；清理后旧请求不会重新写入。
  hitmaskCache.set(key, load)

  return load
}

// 模块加载即恢复快照，不等窗口水合。
restoreCachedActionCatalog()
