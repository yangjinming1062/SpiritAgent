import { atom } from 'nanostores'

import { apiSucceeded, captureAuthScope } from '@/shared/lib/authed-api'
import { backendDetailMessage, isClientErrorIpc } from '@/shared/lib/ipc-error'
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
type SceneRegeneration = ApiSchemas['SceneRegenerationResponse']
export interface SceneAsset {
  id: string
  title: string
  description: string
  requirements: string
  prompt: string
  url: string
  /** 后端原始资产 URL，供受限媒体桥取字节；url 是解析后的可显示 src。 */
  assetUrl: string
  thumbnailUrl: string
  imageLoadFailed: boolean
  status: SceneStatus
  stage: string
  can_retry_generation: boolean
  source: string
  origin: string
  target_size: SceneImageSize | null
  source_size: SceneImageSize | null
  image_size: SceneImageSize | null
  error: string | null
  regeneration: SceneRegeneration | null
}
export type SceneCreationResult =
  | { kind: 'created'; sceneId: string }
  | { kind: 'unavailable' }
  | { kind: 'failed'; message: string; outcome: 'rejected' | 'unknown' }
export type SceneCreationIssue = Extract<SceneCreationResult, { kind: 'failed' }> & { inspected: boolean }
export interface SceneMutationIssue {
  baseline: SceneAsset
  message: string
  inspected: boolean
  pending: boolean
}
export type SceneWire = ApiSchemas['SceneResponse']
interface SceneGenerationInput {
  notes?: string
  image?: string
  content_type?: string
}

export const $pendingScene = atom<SceneAsset | null>(null)
export const $sceneRegenerating = atom<SceneAsset | null>(null)
export const $sceneLibrary = atom<SceneAsset[]>([])
export const $sceneDetails = atom<Record<string, SceneAsset>>({})
export const $sceneLibraryStatus = atom<'idle' | 'loading' | 'loaded' | 'error'>('idle')
export const $scenePolicy = atom<ScenePolicy>('llm_may_replace')
export const $sceneTaskStatus = atom<'none' | 'pending' | 'waiting_upload'>('none')
export const $sceneTaskSlow = atom(false)
export const $sceneCreationIssue = atom<SceneCreationIssue | null>(null)
export const $sceneMutationIssues = atom<Record<string, SceneMutationIssue>>({})
export const $sceneTotal = atom(0)
export const $sceneHasMore = atom(false)
let scenePageCursors: (string | undefined)[] = [undefined]
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
let eventVersion = -1
let mutationRevision = 0
let submitting = false
let pollTimer: ReturnType<typeof setInterval> | null = null
let pollCount = 0

function toScene(row: SceneWire, url: string, imageLoadFailed = false): SceneAsset {
  return { ...row, assetUrl: row.url || '', url, thumbnailUrl: url, imageLoadFailed }
}

/** 把场景图片解析为可用 src；失败抛出，由调用方决定保留旧图还是占位。 */
async function resolveSceneUrl(row: SceneWire, retryImage = false): Promise<string> {
  const url = row.url || ''

  if (url && !url.startsWith('data:') && !url.startsWith('http:') && !url.startsWith('https:')) {
    const key = url.split(/[?#]/)[0]

    if (retryImage) {
      sceneSrcCache.delete(key)
    }

    const cached = sceneSrcCache.get(key)

    if (cached) {
      return cached
    }

    const pending = sceneSrcRequests.get(key)

    if (pending !== undefined && !retryImage) {
      return (await pending) || url
    }

    const epoch = currentClearEpoch()

    const request = (async (): Promise<string | null> => {
      const dataUrl = await backendApi()?.apiAsset({ url, preferCache: !retryImage })

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

/** 单张图片加载失败只影响该场景：沿用已缓存的旧图，没有则留空显示占位。解码由调用方保证一次。 */
async function resolveScene(row: SceneWire, retryImage = false): Promise<SceneAsset> {
  try {
    const url = await resolveSceneUrl(row, retryImage)

    await preload(url)

    return toScene(row, url)
  } catch (error) {
    log.warn('scene', 'Scene image could not be loaded:', error)

    return toScene(row, detailCache.get(row.id)?.url ?? '', true)
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

/** 写操作回包后登记详情顺序，再校准全局状态；换号后丢弃迟到结果。 */
async function mutateSceneRow(request: () => Promise<SceneWire>): Promise<SceneWire> {
  const isCurrent = captureAuthScope()
  const row = await request()
  const read = ++nextRead

  if (!isCurrent?.()) {
    throw new Error(getStrings().living.toasts.sceneRegenerateFailed)
  }

  const scene = await resolveScene(row)

  if (!isCurrent()) {
    throw new Error(getStrings().living.toasts.sceneRegenerateFailed)
  }

  cacheSceneDetails([scene], read)
  await hydrateScene()

  if (!isCurrent()) {
    throw new Error(getStrings().living.toasts.sceneRegenerateFailed)
  }

  return row
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
      $sceneTaskSlow.set(true)

      if (pollCount % 4 !== 0) {
        return
      }
    }

    void hydrateScene()
  }, 3500)
}

registerStorageClearHandler(() => {
  stateRequest++
  listRequest++
  eventVersion = -1
  submitting = false
  stopPoll()
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
  $sceneCreationIssue.set(null)
  $sceneMutationIssues.set({})
  $scenePolicy.set('llm_may_replace')
  $sceneTotal.set(0)
  $sceneHasMore.set(false)
  scenePageCursors = [undefined]
  $scenePage.set(0)
  $sceneQuery.set('')
})

interface SceneLibraryRequest {
  /** 发起时的列表读序号；被更新的读取代后不再复用。 */
  requestId: number
  promise: Promise<boolean>
}

const sceneLibraryRequests = new Map<string, SceneLibraryRequest>()

export async function loadSceneLibrary(query = $sceneQuery.get(), page = $scenePage.get()): Promise<boolean> {
  const scope = captureAuthScope()

  if (!scope) {
    return false
  }

  if (query !== $sceneQuery.get() || page === 0) {
    page = 0
    scenePageCursors = [undefined]
  } else if (page >= scenePageCursors.length) {
    page = $scenePage.get()
  }

  // 同参数并发读共享一次请求：重复进入创建页不再重发同样的列表读。
  // 已被更新的读取代的请求不复用——复用它只会拿到 false，这次查询就停在旧结果上。
  const key = `${query}\0${page}`
  const pending = sceneLibraryRequests.get(key)

  if (pending && pending.requestId === listRequest) {
    return pending.promise
  }

  const read = ++nextRead
  const requestId = ++listRequest
  const epoch = currentClearEpoch()
  const isCurrent = (): boolean => scope() && requestId === listRequest && epoch === currentClearEpoch()

  const request = (async (): Promise<boolean> => {
    $sceneQuery.set(query)
    $scenePage.set(page)
    $sceneLibraryStatus.set('loading')

    const result = await authedResource('companion_scenes.scene_list_route', {
      query: { q: query, cursor: scenePageCursors[page], include_total: true, limit: PAGE_SIZE }
    })

    if (!apiSucceeded(result, 'scene', 'Scene library could not be loaded:') || !result.value) {
      if (isCurrent()) {
        $sceneLibraryStatus.set('error')
      }

      return false
    }

    const value = result.value
    const entries = await Promise.all(value.scenes.map(row => resolveScene(row)))

    if (!isCurrent()) {
      return false
    }

    cacheSceneDetails(entries, read)
    $sceneLibrary.set(entries.map(entry => detailCache.get(entry.id) ?? entry))
    $sceneTotal.set(value.total ?? 0)
    $sceneHasMore.set(value.has_more)
    scenePageCursors = scenePageCursors.slice(0, page + 1)

    if (value.next_cursor) {
      scenePageCursors.push(value.next_cursor)
    }

    $sceneLibraryStatus.set('loaded')

    if (page > 0 && entries.length === 0) {
      void loadSceneLibrary(query, page - 1)
    }

    return true
  })()

  sceneLibraryRequests.set(key, { promise: request, requestId })

  try {
    return await request
  } finally {
    if (sceneLibraryRequests.get(key)?.promise === request) {
      sceneLibraryRequests.delete(key)
    }
  }
}

async function fetchSceneDetail(
  sceneId: string,
  media: 'defer' | 'resolve' | 'reload' = 'resolve'
): Promise<SceneAsset | null> {
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

  const result = await authedResource('companion_scenes.scene_detail_route', { path: { scene_id: sceneId } })

  if (!isCurrent()) {
    return null
  }

  if (!result.ok) {
    if (result.reason === 'unauth') {
      return null
    }

    throw new Error(backendDetailMessage(result.error, getStrings().living.toasts.sceneStatusRefreshFailed))
  }

  if (!result.value || result.value.id !== sceneId) {
    throw new Error(getStrings().living.toasts.sceneStatusRefreshFailed)
  }

  const row = result.value
  const cached = detailCache.get(sceneId)
  const deferMedia = media === 'defer'
  const sameImage = row.url.split(/[?#]/)[0] === cached?.assetUrl.split(/[?#]/)[0]

  const scene = deferMedia
    ? toScene(row, row.url && sameImage ? (cached?.url ?? '') : '', Boolean(sameImage && cached?.imageLoadFailed))
    : await resolveScene(row, media === 'reload')

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

export async function reloadSceneImage(sceneId: string): Promise<SceneAsset | null> {
  return fetchSceneDetail(sceneId, 'reload')
}

/** 刷新单个场景的最新状态；不等待场景列表或图片字节，业务错误交给页面显示。 */
export async function refreshSceneStatus(sceneId: string): Promise<SceneAsset | null> {
  const scope = captureAuthScope()
  const detail = await fetchSceneDetail(sceneId, 'defer')

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

/** 刷新场景状态；已自行读取列表的调用方使用 skipLibraryRefresh 消去重复读取。返回值只确认本次状态快照有效。 */
export async function hydrateScene(options: { skipLibraryRefresh?: boolean } = {}): Promise<boolean> {
  const scope = captureAuthScope()

  if (!scope) {
    return false
  }

  const read = ++nextRead
  const request = ++stateRequest
  const epoch = currentClearEpoch()
  const isCurrent = (): boolean => scope() && request === stateRequest && epoch === currentClearEpoch()
  const result = await authedResource('companion_scenes.read_scene_state')

  if (!apiSucceeded(result, 'scene', 'Scene state could not be loaded:') || !result.value) {
    return false
  }

  const state = result.value
  const stale = (): boolean => !isCurrent()

  if (stale()) {
    return false
  }

  try {
    const pendingMaterial = state.materials.find(item => item.status === 'pending') ?? null
    const regeneratingMaterial = state.materials.find(item => item.regeneration?.status === 'pending') ?? null
    const pending = pendingMaterial ? await resolveScene(pendingMaterial) : null
    const regenerating = regeneratingMaterial ? await resolveScene(regeneratingMaterial) : null

    if (stale()) {
      return false
    }

    const oldPending = $pendingScene.get()
    const oldRegenerating = $sceneRegenerating.get()
    $pendingScene.set(pending)
    $sceneRegenerating.set(regenerating)
    cacheSceneDetails([...(pending ? [pending] : []), ...(regenerating ? [regenerating] : [])], read)
    $scenePolicy.set(state.policy)
    const taskStatus = !pending ? 'none' : pending.stage === 'waiting_upload' ? 'waiting_upload' : 'pending'
    $sceneTaskStatus.set(taskStatus)

    if (taskStatus === 'pending' || regenerating) {
      startPoll()
    } else {
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

    return isCurrent()
  } catch (error) {
    log.warn('scene', 'Scene hydration failed:', error)

    return false
  }
}

export async function inspectSceneCreationIssue(): Promise<boolean> {
  const previous = $sceneCreationIssue.get()
  const scope = captureAuthScope()

  if (!scope || previous?.outcome !== 'unknown') {
    return false
  }

  const issue = previous.inspected ? { ...previous, inspected: false } : previous
  $sceneCreationIssue.set(issue)

  try {
    const [stateRead, libraryRead] = await Promise.all([
      hydrateScene({ skipLibraryRefresh: true }),
      loadSceneLibrary('', 0)
    ])

    if (!scope() || issue !== $sceneCreationIssue.get() || !stateRead || !libraryRead) {
      return false
    }

    $sceneCreationIssue.set({ ...issue, inspected: true })

    return true
  } catch (error) {
    log.warn('scene', 'Scene creation inspection failed:', error)

    return false
  }
}

/** 只结束用户已核对的本地等待提醒；不推断提交结局，也不取消或重发制作。 */
export function finishSceneCreationWait(issue: SceneCreationIssue): void {
  if (issue === $sceneCreationIssue.get() && issue.outcome === 'unknown' && issue.inspected) {
    $sceneCreationIssue.set(null)
  }
}

export function updateSceneMutationIssue(
  sceneId: string,
  update: (current: SceneMutationIssue | null) => SceneMutationIssue | null
): void {
  const issues = $sceneMutationIssues.get()
  const previous = issues[sceneId] ?? null
  const issue = update(previous)

  if (issue === previous) {
    return
  }

  const next = { ...issues }

  if (issue) {
    next[sceneId] = issue
  } else {
    delete next[sceneId]
  }

  $sceneMutationIssues.set(next)
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
    throw result.reason === 'err' ? result.error : new Error(getStrings().living.toasts.sceneRegenerateFailed)
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

export async function createScene(input: SceneGenerationInput = {}): Promise<SceneCreationResult> {
  if (
    submitting ||
    $sceneTaskStatus.get() !== 'none' ||
    $sceneRegenerating.get() !== null ||
    $sceneCreationIssue.get()?.outcome === 'unknown'
  ) {
    return { kind: 'unavailable' }
  }

  const epoch = currentClearEpoch()
  let requestStarted = false
  submitting = true
  $sceneCreationIssue.set(null)

  try {
    const { content_type, ...details } = input
    const mime = (['image/png', 'image/jpeg', 'image/webp', 'image/gif'] as const).find(value => value === content_type)

    if (content_type && !mime) {
      throw new Error('仅支持 PNG / JPEG / WebP / GIF 图片')
    }

    requestStarted = true

    const row = await sceneRequest('companion_scenes.create_scene', {
      body: { ...details, content_type: mime, source: 'generate' }
    })

    if (epoch === currentClearEpoch()) {
      $pendingScene.set(toScene(row, row.url || ''))
      $sceneTaskStatus.set('pending')
    }

    $sceneTaskSlow.set(false)
    startPoll()
    await hydrateScene()

    if (epoch !== currentClearEpoch()) {
      return { kind: 'unavailable' }
    }

    return { kind: 'created', sceneId: row.id }
  } catch (error) {
    if (epoch === currentClearEpoch()) {
      const issue: SceneCreationIssue = {
        kind: 'failed',
        message: sceneError(error),
        outcome: !requestStarted || isClientErrorIpc(error) ? 'rejected' : 'unknown',
        inspected: false
      }

      $sceneCreationIssue.set(issue)

      return issue
    }

    return { kind: 'unavailable' }
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

  return { id: row.id, prompt: row.prompt }
}

export async function adoptSceneImage(
  image: { base64: string; contentType: string },
  sceneId?: string
): Promise<string | null> {
  const result = await mutate('companion_scenes.create_scene', {
    body: {
      source: 'analysis',
      scene_id: sceneId ? sceneId : undefined,
      image: image.base64,
      content_type: image.contentType
    }
  })

  return result?.id ?? null
}

export async function cancelSceneTask(sceneId: string): Promise<SceneWire> {
  return mutateSceneRow(() => sceneRequest('companion_scenes.post_scene_discard', { path: { scene_id: sceneId } }))
}

export async function deleteScene(sceneId: string): Promise<void> {
  await mutate('companion_scenes.delete_scene_route', { path: { scene_id: sceneId } })
}

export async function editScene(sceneId: string, title: string, description: string): Promise<SceneWire> {
  return mutateSceneRow(() =>
    sceneRequest('companion_scenes.scene_edit_route', {
      path: { scene_id: sceneId },
      body: { title, description }
    })
  )
}

export async function regenerateScene(sceneId: string): Promise<void> {
  await mutateSceneRow(() => sceneRequest('companion_scenes.post_scene_regenerate', { path: { scene_id: sceneId } }))
}

export async function analyzeScene(sceneId: string): Promise<SceneWire> {
  return mutateSceneRow(() => sceneRequest('companion_scenes.scene_analyze_route', { path: { scene_id: sceneId } }))
}

export async function setScenePolicy(policy: ScenePolicy): Promise<void> {
  await mutate('companion_scenes.patch_scene_policy', { body: { policy } })
}

/** 场景素材变更的事件计数；仅用于中止与刷新竞争中的过期详情读取。 */
export function onSceneEvent(): void {
  eventVersion += 1
  void hydrateScene()
}
