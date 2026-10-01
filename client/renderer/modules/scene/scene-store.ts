import { atom } from 'nanostores'

import { apiSucceeded, authedApi } from '@/shared/lib/authed-api'
import { backendDetailMessage } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'
import { currentClearEpoch, registerStorageClearHandler } from '@/shared/lib/storage'
import { trimOldest } from '@/shared/lib/trim-oldest'
import { notify } from '@/shared/store/notifications'
import { getStrings } from '@/shared/strings'

export type SceneStatus = 'cancelled' | 'description_failed' | 'failed' | 'pending' | 'ready'
export type ScenePolicy = 'llm_may_replace' | 'locked'
export interface SceneRegeneration {
  task_id: string
  status: 'cancelled' | 'failed' | 'pending' | 'ready'
  stage: string
  error: string | null
}
export interface ActiveScene {
  id: string
  title: string
  description: string
  requirements: string
  outfit_description: string
  prompt: string
  url: string
  thumbnailUrl: string
  status: SceneStatus
  stage: string
  source: string
  origin: string
  error: string | null
  regeneration: SceneRegeneration | null
}
export type SceneAsset = ActiveScene
interface SceneWire extends Omit<ActiveScene, 'id' | 'thumbnailUrl'> {
  id: number
}
interface SceneStateWire {
  active: SceneWire | null
  pending: SceneWire | null
  regenerating: SceneWire | null
  policy: ScenePolicy
  version: number
  switch_version: number
}
interface SceneListWire {
  scenes: SceneWire[]
  total: number
  version: number
}
export interface SceneGenerationInput {
  notes?: string
  outfit_description?: string
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
let stateRequest = 0
let listRequest = 0
let version = -1
let eventVersion = -1
let submitting = false
let pollTimer: ReturnType<typeof setInterval> | null = null
let pollCount = 0

function toScene(row: SceneWire, url: string): ActiveScene {
  return { ...row, id: String(row.id), url, thumbnailUrl: url, regeneration: row.regeneration ?? null }
}

/** 把场景图片解析为可用 src；失败抛出，由调用方决定保留旧图还是占位。 */
async function resolveSceneUrl(row: SceneWire): Promise<string> {
  const url = row.url || ''

  if (url && !url.startsWith('data:') && !url.startsWith('http:') && !url.startsWith('https:')) {
    return (await window.spiritagent?.apiAsset({ url })) || url
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

function cacheSceneDetails(entries: SceneAsset[]): void {
  for (const entry of entries) {
    detailCache.delete(entry.id)
    detailCache.set(entry.id, entry)
  }

  trimOldest(detailCache, 48)
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
  const request = ++listRequest
  const epoch = currentClearEpoch()
  const isCurrent = (): boolean => request === listRequest && epoch === currentClearEpoch()
  $sceneQuery.set(query)
  $scenePage.set(page)
  $sceneLibraryStatus.set('loading')

  const result = await authedApi<SceneListWire>({
    path: `/api/companion/scenes?q=${encodeURIComponent(query)}&offset=${page * PAGE_SIZE}&limit=${PAGE_SIZE}`
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

  $sceneLibrary.set(entries)
  cacheSceneDetails(entries)
  $sceneTotal.set(value.total)
  $sceneLibraryStatus.set('loaded')

  if (page > 0 && entries.length === 0) {
    void loadSceneLibrary(query, page - 1)
  }
}

export async function loadSceneDetail(sceneId: string): Promise<SceneAsset | null> {
  const epoch = currentClearEpoch()
  const result = await authedApi<SceneWire>({ path: `/api/companion/scenes/${encodeURIComponent(sceneId)}` })

  if (
    !apiSucceeded(result, 'scene', 'Scene detail could not be loaded:') ||
    epoch !== currentClearEpoch() ||
    !result.value
  ) {
    return null
  }

  const scene = await resolveScene(result.value)

  if (epoch !== currentClearEpoch()) {
    return null
  }

  cacheSceneDetails([scene])

  return scene
}

export async function hydrateScene(): Promise<void> {
  const request = ++stateRequest
  const epoch = currentClearEpoch()
  const isCurrent = (): boolean => request === stateRequest && epoch === currentClearEpoch()
  const result = await authedApi<SceneStateWire>({ path: '/api/companion/scenes/state' })

  if (!apiSucceeded(result, 'scene', 'Scene state could not be loaded:') || !result.value) {
    return
  }

  const state = result.value

  if (state.version < Math.max(version, eventVersion)) {
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

    if (!isCurrent() || state.version < Math.max(version, eventVersion)) {
      return
    }

    const oldPending = $pendingScene.get()
    const oldRegenerating = $sceneRegenerating.get()
    version = state.version
    $activeScene.set(active)
    $pendingScene.set(pending)
    $sceneRegenerating.set(regenerating)
    cacheSceneDetails([
      ...(active ? [active] : []),
      ...(pending ? [pending] : []),
      ...(regenerating ? [regenerating] : [])
    ])
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

    if (isCurrent()) {
      await loadSceneLibrary()
    }
  } catch (error) {
    log.warn('scene', 'Scene hydration failed:', error)
  }
}

const sceneError = (error: unknown): string =>
  backendDetailMessage(error, getStrings().living.toasts.sceneRegenerateFailed)

// 写请求使在途的状态读取失效；返回非空 body，失败或请求期间账户被清理则抛出可直接展示的文案。
async function sceneRequest<T>(path: string, method: 'POST' | 'PATCH' | 'DELETE', body?: object): Promise<T> {
  const epoch = currentClearEpoch()
  stateRequest++
  const result = await authedApi<T>({ path: `/api/companion/scenes${path}`, method, ...(body ? { body } : {}) })

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

async function mutate<T>(path: string, method: 'POST' | 'PATCH' | 'DELETE', body?: object): Promise<T> {
  const epoch = currentClearEpoch()
  const value = await sceneRequest<T>(path, method, body)

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
    const row = await sceneRequest<SceneWire>('/generate', 'POST', input)

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
  input: Pick<SceneGenerationInput, 'notes' | 'outfit_description'>
): Promise<string> {
  const row = await mutate<SceneWire>('/prompt', 'POST', input)

  return row.prompt
}

export async function adoptSceneImage(image: { base64: string; contentType: string }, sceneId?: string): Promise<void> {
  const pending = $pendingScene.get()
  const targetId = sceneId ?? (pending?.stage === 'waiting_upload' ? pending.id : undefined)
  const path = targetId ? `/${encodeURIComponent(targetId)}/adopt` : '/adopt'
  await mutate(path, 'POST', { image: image.base64, content_type: image.contentType })
}

export async function cancelSceneTask(sceneId: string): Promise<void> {
  await mutate(`/${sceneId}/discard`, 'POST')
}

export async function activateScene(sceneId: string): Promise<void> {
  const epoch = currentClearEpoch()

  try {
    await mutate('/activate', 'POST', { scene_id: Number(sceneId) })

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
  await mutate(`/${sceneId}`, 'DELETE')
}

export async function editScene(sceneId: string, title: string, description: string): Promise<void> {
  await mutate(`/${sceneId}`, 'PATCH', { title, description })
}

export async function regenerateScene(sceneId: string): Promise<void> {
  await mutate(`/${sceneId}/regenerate`, 'POST')
}

export async function analyzeScene(sceneId: string): Promise<void> {
  await mutate(`/${sceneId}/analyze`, 'POST')
}

export async function setScenePolicy(policy: ScenePolicy): Promise<void> {
  await mutate('/policy', 'PATCH', { policy })
}

export function onSceneEvent(event: { payload?: unknown; type: string }): void {
  const payload = event.payload as { version?: unknown } | undefined

  if (typeof payload?.version !== 'number' || payload.version <= Math.max(version, eventVersion)) {
    return
  }

  eventVersion = payload.version
  void hydrateScene()
}
