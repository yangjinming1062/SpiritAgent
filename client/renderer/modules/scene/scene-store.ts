import { atom } from 'nanostores'

import { apiSucceeded, captureAuthScope } from '@/shared/lib/authed-api'
import { backendDetailMessage } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'
import { authedResource, type ResourceArguments } from '@/shared/lib/resource-request'
import { currentClearEpoch, registerStorageClearHandler } from '@/shared/lib/storage'
import { trimOldest } from '@/shared/lib/trim-oldest'
import { backendApi } from '@/shared/platform/desktop'
import { notify } from '@/shared/store/notifications'
import { getStrings } from '@/shared/strings'
import type { DesktopApiOperationKey } from '@ipc/contracts'

import type { ApiOperation } from '../../../../shared/api'
import type { ApiSchemas } from '../../../../shared/api.generated'

type SceneStatus = ApiSchemas['SceneStatus']
export type ScenePolicy = ApiSchemas['ScenePolicy']
type SceneImageSize = ApiSchemas['SceneImageDimensions']
export const SCENE_IMAGE_SIZE: Readonly<SceneImageSize> = { width: 2560, height: 1440 }
type SceneRegeneration = ApiSchemas['SceneRegenerationResponse']
export interface ActiveScene {
  id: string
  title: string
  description: string
  requirements: string
  prompt: string
  url: string
  /** 后端原始资产 URL，供受限媒体桥取字节；url 是解析后的可显示 src。 */
  assetUrl: string
  thumbnailUrl: string
  status: SceneStatus
  stage: string
  source: string
  origin: string
  target_size: SceneImageSize | null
  source_size: SceneImageSize | null
  image_size: SceneImageSize | null
  error: string | null
  regeneration: SceneRegeneration | null
}
export type SceneAsset = ActiveScene
type SceneWire = ApiSchemas['SceneResponse']
interface SceneGenerationInput {
  notes?: string
  image?: string
  content_type?: string
}

export const $activeScene = atom<ActiveScene | null>(null)
export const $pendingScene = atom<ActiveScene | null>(null)
export const $sceneRegenerating = atom<ActiveScene | null>(null)
export const $sceneLibrary = atom<SceneAsset[]>([])
export const $sceneDetails = atom<Record<string, SceneAsset>>({})
export const $sceneLibraryStatus = atom<'idle' | 'loading' | 'loaded' | 'error'>('idle')
export const $scenePolicy = atom<ScenePolicy>('llm_may_replace')
export const $sceneTaskStatus = atom<'none' | 'pending' | 'waiting_upload'>('none')
export const $sceneTaskSlow = atom(false)
export const $sceneTotal = atom(0)
export const $scenePage = atom(0)
export const $sceneQuery = atom('')
export const PAGE_SIZE = 24
const detailCache = new Map<string, SceneAsset>()
const detailReads = new Map<string, number>()
let nextRead = 0
// 场景图 URL 的签名 query 会轮换，同路径即同内容（资产名带随机后缀）：按去 query 路径缓存解析结果，避免轮询期重复取整图。
const MAX_SCENE_SRC_ENTRIES = 48
const sceneSrcCache = new Map<string, string>()
const sceneSrcRequests = new Map<string, Promise<string | null>>()
let stateRequest = 0
let listRequest = 0
let version = -1
let eventVersion = -1
let mutationRevision = 0
let submitting = false
let pollTimer: ReturnType<typeof setInterval> | null = null
let pollCount = 0

function toScene(row: SceneWire, url: string): ActiveScene {
  return { ...row, assetUrl: row.url || '', id: String(row.id), url, thumbnailUrl: url }
}

/** 把场景图片解析为可用 src；失败抛出，由调用方决定保留旧图还是占位。 */
async function resolveSceneUrl(row: SceneWire): Promise<string> {
  const url = row.url || ''

  if (url && !url.startsWith('data:') && !url.startsWith('http:') && !url.startsWith('https:')) {
    const key = url.split(/[?#]/)[0]
    const cached = sceneSrcCache.get(key)

    if (cached) {
      return cached
    }

    const pending = sceneSrcRequests.get(key)

    if (pending !== undefined) {
      return (await pending) || url
    }

    const epoch = currentClearEpoch()

    const request = (async (): Promise<string | null> => {
      const dataUrl = await backendApi()?.apiAsset({ url, preferCache: true })

      if (dataUrl && epoch === currentClearEpoch()) {
        sceneSrcCache.set(key, dataUrl)
        trimOldest(sceneSrcCache, MAX_SCENE_SRC_ENTRIES)
      }

      return dataUrl || null
    })().finally(() => {
      if (sceneSrcRequests.get(key) === request) {
        sceneSrcRequests.delete(key)
      }
    })

    sceneSrcRequests.set(key, request)

    return (await request) || url
  }

  return url
}

/** 单张图片加载失败只影响该场景：沿用已缓存的旧图，没有则留空显示占位。 */
async function resolveScene(row: SceneWire): Promise<ActiveScene> {
  try {
    return toScene(row, await resolveSceneUrl(row))
  } catch (error) {
    log.warn('scene', 'Scene image could not be loaded:', error)

    return toScene(row, detailCache.get(String(row.id))?.url ?? '')
  }
}

function cacheSceneDetails(entries: SceneAsset[], read: number): void {
  for (const entry of entries) {
    if (read < (detailReads.get(entry.id) ?? -1)) {
      continue
    }

    detailReads.set(entry.id, read)
    detailCache.delete(entry.id)
    detailCache.set(entry.id, entry)
  }

  trimOldest(detailCache, 48)

  for (const id of detailReads.keys()) {
    if (!detailCache.has(id)) {
      detailReads.delete(id)
    }
  }

  $sceneDetails.set(Object.fromEntries(detailCache))
}

async function preload(url: string): Promise<void> {
  if (!url) {
    return
  }

  const image = new Image()
  image.src = url
  await image.decode()
}

function stopPoll(): void {
  if (pollTimer !== null) {
    clearInterval(pollTimer)
  }

  pollTimer = null
  pollCount = 0
}

function startPoll(): void {
  if (pollTimer !== null) {
    return
  }

  pollTimer = setInterval(() => {
    pollCount++

    if (pollCount > 40) {
      stopPoll()
      $sceneTaskSlow.set(true)

      return
    }

    void hydrateScene()
  }, 3500)
}

registerStorageClearHandler(() => {
  stateRequest++
  listRequest++
  version = -1
  eventVersion = -1
  submitting = false
  stopPoll()
  $activeScene.set(null)
  $pendingScene.set(null)
  $sceneRegenerating.set(null)
  $sceneLibrary.set([])
  detailCache.clear()
  detailReads.clear()
  sceneSrcCache.clear()
  sceneSrcRequests.clear()
  $sceneDetails.set({})
  $sceneLibraryStatus.set('idle')
  $sceneTaskStatus.set('none')
  $sceneTaskSlow.set(false)
  $scenePolicy.set('llm_may_replace')
  $sceneTotal.set(0)
  $scenePage.set(0)
  $sceneQuery.set('')
})

export async function loadSceneLibrary(query = $sceneQuery.get(), page = $scenePage.get()): Promise<void> {
  const read = ++nextRead
  const request = ++listRequest
  const epoch = currentClearEpoch()
  const isCurrent = (): boolean => request === listRequest && epoch === currentClearEpoch()
  $sceneQuery.set(query)
  $scenePage.set(page)
  $sceneLibraryStatus.set('loading')

  const result = await authedResource('companion_scenes.scene_list_route', {
    query: { q: query, offset: page * PAGE_SIZE, limit: PAGE_SIZE }
  })

  if (!apiSucceeded(result, 'scene', 'Scene library could not be loaded:') || !result.value) {
    if (isCurrent()) {
      $sceneLibraryStatus.set('error')
    }

    return
  }

  const value = result.value
  const entries = await Promise.all(value.scenes.map(resolveScene))

  if (!isCurrent() || value.version < eventVersion) {
    return
  }

  cacheSceneDetails(entries, read)
  $sceneLibrary.set(entries.map(entry => detailCache.get(entry.id) ?? entry))
  $sceneTotal.set(value.total)
  $sceneLibraryStatus.set('loaded')

  if (page > 0 && entries.length === 0) {
    void loadSceneLibrary(query, page - 1)
  }
}

async function fetchSceneDetail(sceneId: string, deferMedia = false): Promise<SceneAsset | null> {
  const scope = captureAuthScope()

  if (!scope) {
    return null
  }

  const read = ++nextRead
  const startMutation = mutationRevision
  const startEvent = eventVersion

  const isCurrent = (): boolean =>
    scope() &&
    startMutation === mutationRevision &&
    startEvent === eventVersion &&
    read >= (detailReads.get(sceneId) ?? -1)

  const result = await authedResource('companion_scenes.scene_detail_route', { path: { scene_id: Number(sceneId) } })

  if (!isCurrent()) {
    return null
  }

  if (!result.ok) {
    if (result.reason === 'unauth') {
      return null
    }

    throw new Error(backendDetailMessage(result.error, getStrings().living.toasts.sceneStatusRefreshFailed))
  }

  if (!result.value || String(result.value.id) !== sceneId) {
    throw new Error(getStrings().living.toasts.sceneStatusRefreshFailed)
  }

  const row = result.value
  const cached = detailCache.get(sceneId)

  const scene = deferMedia ? toScene(row, row.url ? (cached?.url ?? '') : '') : await resolveScene(row)

  if (!isCurrent()) {
    return null
  }

  cacheSceneDetails([scene], read)

  if (deferMedia) {
    void resolveScene(row).then(resolved => {
      if (isCurrent() && detailReads.get(sceneId) === read) {
        cacheSceneDetails([resolved], read)
      }
    })
  }

  return scene
}

export async function loadSceneDetail(sceneId: string): Promise<SceneAsset | null> {
  try {
    return await fetchSceneDetail(sceneId)
  } catch (error) {
    log.warn('scene', 'Scene detail could not be loaded:', error)

    return null
  }
}

/** 刷新单个场景的最新状态；不等待场景列表或图片字节，业务错误交给页面显示。 */
export async function refreshSceneStatus(sceneId: string): Promise<SceneAsset | null> {
  const scope = captureAuthScope()
  const detail = await fetchSceneDetail(sceneId, true)

  if (!scope?.() || !detail) {
    return null
  }

  // 单场景终态立即解除它的忙碌标记；更早的全局读取不能再覆盖本次查询。
  stateRequest++

  if (detail.status === 'pending' || $pendingScene.get()?.id === sceneId) {
    $pendingScene.set(detail.status === 'pending' ? detail : null)
  }

  if (detail.regeneration?.status === 'pending' || $sceneRegenerating.get()?.id === sceneId) {
    $sceneRegenerating.set(detail.regeneration?.status === 'pending' ? detail : null)
  }

  const pending = $pendingScene.get()
  const taskStatus = !pending ? 'none' : pending.stage === 'waiting_upload' ? 'waiting_upload' : 'pending'
  $sceneTaskStatus.set(taskStatus)
  stopPoll()
  $sceneTaskSlow.set(false)

  if (taskStatus === 'pending' || $sceneRegenerating.get()) {
    startPoll()
  }

  return detail
}

/** 刷新场景状态并在尾部重刷列表；skipLibraryRefresh 只供首屏已并行 loadSceneLibrary 的调用方消去冗余刷新，其余刷新时机不得跳过。 */
export async function hydrateScene(options: { skipLibraryRefresh?: boolean } = {}): Promise<void> {
  const read = ++nextRead
  const request = ++stateRequest
  const epoch = currentClearEpoch()
  const isCurrent = (): boolean => request === stateRequest && epoch === currentClearEpoch()
  const result = await authedResource('companion_scenes.read_scene_state')

  if (!apiSucceeded(result, 'scene', 'Scene state could not be loaded:') || !result.value) {
    return
  }

  const state = result.value
  const stale = (): boolean => !isCurrent() || state.version < Math.max(version, eventVersion)

  if (stale()) {
    return
  }

  try {
    let active = $activeScene.get()

    // 当前环境的替换图预加载成功后才切换；失败保留旧图，其余状态照常刷新。
    try {
      const nextActive = state.active ? toScene(state.active, await resolveSceneUrl(state.active)) : null

      if (nextActive?.url && nextActive.url !== active?.url) {
        await preload(nextActive.url)
      }

      active = nextActive
    } catch (error) {
      log.warn('scene', 'Active scene image could not be loaded:', error)
    }

    const pending = state.pending ? await resolveScene(state.pending) : null
    const regenerating = state.regenerating ? await resolveScene(state.regenerating) : null

    if (stale()) {
      return
    }

    const oldPending = $pendingScene.get()
    const oldRegenerating = $sceneRegenerating.get()
    version = state.version
    $activeScene.set(active)
    $pendingScene.set(pending)
    $sceneRegenerating.set(regenerating)
    cacheSceneDetails(
      [...(active ? [active] : []), ...(pending ? [pending] : []), ...(regenerating ? [regenerating] : [])],
      read
    )
    $scenePolicy.set(state.policy)
    const taskStatus = !pending ? 'none' : pending.stage === 'waiting_upload' ? 'waiting_upload' : 'pending'
    $sceneTaskStatus.set(taskStatus)

    if ((taskStatus === 'pending' || regenerating) && !$sceneTaskSlow.get()) {
      startPoll()
    } else if (taskStatus !== 'pending' && !regenerating) {
      stopPoll()
      $sceneTaskSlow.set(false)
    }

    if (oldPending && !pending) {
      const detail = await loadSceneDetail(oldPending.id)

      if (isCurrent() && detail?.status === 'ready') {
        notify({ kind: 'success', message: getStrings().living.toasts.sceneReady })
      }
    }

    if (oldRegenerating && (!regenerating || oldRegenerating.id !== regenerating.id)) {
      const finished = await loadSceneDetail(oldRegenerating.id)

      if (isCurrent()) {
        if (finished?.regeneration?.status === 'ready') {
          notify({ kind: 'success', message: getStrings().living.toasts.sceneImageRegenerated })
        } else if (finished?.regeneration?.status === 'failed') {
          notify({
            kind: 'warning',
            message: finished.regeneration.error || getStrings().living.toasts.sceneRegenerateFailed
          })
        }
      }
    }

    if (isCurrent() && !options.skipLibraryRefresh) {
      await loadSceneLibrary()
    }
  } catch (error) {
    log.warn('scene', 'Scene hydration failed:', error)
  }
}

const sceneError = (error: unknown): string =>
  backendDetailMessage(error, getStrings().living.toasts.sceneRegenerateFailed)

// 写请求使在途的状态读取失效；返回非空 body，失败或请求期间账户被清理则抛出可直接展示的文案。
async function sceneRequest<K extends DesktopApiOperationKey>(
  operation: K,
  ...args: ResourceArguments<K>
): Promise<ApiOperation<K>['response']> {
  const epoch = currentClearEpoch()
  mutationRevision++
  stateRequest++
  const result = await authedResource(operation, ...args)

  if (epoch !== currentClearEpoch()) {
    throw new Error(getStrings().living.toasts.sceneRegenerateFailed)
  }

  if (!result.ok) {
    throw new Error(
      result.reason === 'err' ? sceneError(result.error) : getStrings().living.toasts.sceneRegenerateFailed
    )
  }

  if (result.value === undefined || result.value === null) {
    throw new Error(getStrings().living.toasts.sceneRegenerateFailed)
  }

  return result.value
}

async function mutate<K extends DesktopApiOperationKey>(
  operation: K,
  ...args: ResourceArguments<K>
): Promise<ApiOperation<K>['response']> {
  const epoch = currentClearEpoch()
  const value = await sceneRequest(operation, ...args)

  await hydrateScene()

  if (epoch !== currentClearEpoch()) {
    throw new Error(getStrings().living.toasts.sceneRegenerateFailed)
  }

  return value
}

export async function createScene(input: SceneGenerationInput = {}): Promise<ActiveScene | null> {
  if (submitting || $sceneTaskStatus.get() !== 'none' || $sceneRegenerating.get() !== null) {
    return null
  }

  const epoch = currentClearEpoch()
  const startVersion = version
  submitting = true

  try {
    const { content_type, ...details } = input
    const mime = (['image/png', 'image/jpeg', 'image/webp', 'image/gif'] as const).find(value => value === content_type)

    if (content_type && !mime) {
      throw new Error('仅支持 PNG / JPEG / WebP / GIF 图片')
    }

    const row = await sceneRequest('companion_scenes.create_scene', {
      body: { ...details, content_type: mime, source: 'generate' }
    })

    if (version === startVersion) {
      $pendingScene.set(toScene(row, row.url || ''))
      $sceneTaskStatus.set('pending')
    }

    $sceneTaskSlow.set(false)
    startPoll()
    await hydrateScene()

    if (epoch !== currentClearEpoch()) {
      return null
    }

    const created = await resolveScene(row)

    return epoch === currentClearEpoch() ? created : null
  } catch (error) {
    if (epoch === currentClearEpoch()) {
      notify({ kind: 'warning', message: sceneError(error) })
    }

    return null
  } finally {
    if (epoch === currentClearEpoch()) {
      submitting = false
    }
  }
}

export async function prepareScenePrompt(
  input: Pick<SceneGenerationInput, 'notes'>
): Promise<{ id: string; prompt: string }> {
  const row = await mutate('companion_scenes.post_scene_prompt', { body: input })

  return { id: String(row.id), prompt: row.prompt }
}

export async function adoptSceneImage(
  image: { base64: string; contentType: string },
  sceneId?: string
): Promise<string | null> {
  const result = await mutate('companion_scenes.create_scene', {
    body: {
      source: 'import',
      scene_id: sceneId ? Number(sceneId) : undefined,
      image: image.base64,
      content_type: image.contentType
    }
  })

  return result ? String(result.id) : null
}

export async function cancelSceneTask(sceneId: string): Promise<void> {
  await mutate('companion_scenes.post_scene_discard', { path: { scene_id: Number(sceneId) } })
}

export async function activateScene(sceneId: string): Promise<void> {
  const epoch = currentClearEpoch()

  try {
    await mutate('companion_scenes.post_scene_activate', { body: { scene_id: Number(sceneId) } })

    if (epoch === currentClearEpoch()) {
      notify({ kind: 'success', message: getStrings().living.toasts.sceneRollbackSuccess })
    }
  } catch (error) {
    if (epoch === currentClearEpoch()) {
      notify({ kind: 'warning', message: sceneError(error) })
    }
  }
}

export async function deleteScene(sceneId: string): Promise<void> {
  await mutate('companion_scenes.delete_scene_route', { path: { scene_id: Number(sceneId) } })
}

export async function editScene(sceneId: string, title: string, description: string): Promise<void> {
  await mutate('companion_scenes.scene_edit_route', {
    path: { scene_id: Number(sceneId) },
    body: { title, description }
  })
}

export async function regenerateScene(sceneId: string): Promise<void> {
  await mutate('companion_scenes.post_scene_regenerate', { path: { scene_id: Number(sceneId) } })
}

export async function analyzeScene(sceneId: string): Promise<void> {
  await mutate('companion_scenes.scene_analyze_route', { path: { scene_id: Number(sceneId) } })
}

export async function setScenePolicy(policy: ScenePolicy): Promise<void> {
  await mutate('companion_scenes.patch_scene_policy', { body: { policy } })
}

export function onSceneEvent(event: { payload?: unknown; type: string }): void {
  const payload = event.payload as { version?: unknown } | undefined

  if (typeof payload?.version !== 'number' || payload.version <= Math.max(version, eventVersion)) {
    return
  }

  eventVersion = payload.version
  void hydrateScene()
}
