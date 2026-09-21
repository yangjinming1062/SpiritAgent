import { atom } from 'nanostores'

import { authedApi } from '@/shared/lib/authed-api'
import { backendDetailMessage } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'
import { currentClearEpoch, registerStorageClearHandler } from '@/shared/lib/storage'
import { notify } from '@/shared/store/notifications'
import { getStrings } from '@/shared/strings'

export type SceneStatus = 'cancelled' | 'description_failed' | 'failed' | 'pending' | 'ready'
export type ScenePolicy = 'llm_may_replace' | 'locked'
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
}
export type SceneAsset = ActiveScene
interface SceneWire extends Omit<ActiveScene, 'id' | 'thumbnailUrl'> {
  id: number
}
interface SceneStateWire {
  active: SceneWire | null
  pending: SceneWire | null
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
export const $sceneLibrary = atom<SceneAsset[]>([])
export const $scenePolicy = atom<ScenePolicy>('llm_may_replace')
export const $sceneTaskStatus = atom<'none' | 'pending' | 'waiting_upload'>('none')
export const $sceneTaskSlow = atom(false)
export const $sceneTotal = atom(0)
export const $scenePage = atom(0)
export const $sceneQuery = atom('')
const PAGE_SIZE = 24
let stateRequest = 0
let listRequest = 0
let version = -1
let eventVersion = -1
let submitting = false
let pollTimer: ReturnType<typeof setInterval> | null = null
let pollCount = 0

async function resolveScene(row: SceneWire): Promise<ActiveScene> {
  let url = row.url || ''

  if (url && !url.startsWith('data:') && !url.startsWith('http:') && !url.startsWith('https:')) {
    url = (await window.spiritagent?.apiAsset({ url })) || url
  }

  return { ...row, id: String(row.id), url, thumbnailUrl: url }
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
  $sceneLibrary.set([])
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
  $sceneQuery.set(query)
  $scenePage.set(page)

  const result = await authedApi<SceneListWire>({
    path: `/api/companion/scenes?q=${encodeURIComponent(query)}&offset=${page * PAGE_SIZE}&limit=${PAGE_SIZE}`
  })

  if (!result.ok || !result.value) {
    return
  }

  const value = result.value
  let entries: SceneAsset[]

  try {
    entries = await Promise.all(value.scenes.map(resolveScene))
  } catch (error) {
    log.warn('scene', 'Scene library images could not be loaded:', error)

    return
  }

  if (request !== listRequest || epoch !== currentClearEpoch() || value.version < eventVersion) {
    return
  }

  $sceneLibrary.set(entries)
  $sceneTotal.set(value.total)

  if (page > 0 && entries.length === 0) {
    void loadSceneLibrary(query, page - 1)
  }
}

export async function hydrateScene(): Promise<void> {
  const request = ++stateRequest
  const epoch = currentClearEpoch()
  const result = await authedApi<SceneStateWire>({ path: '/api/companion/scenes/state' })

  if (!result.ok || !result.value) {
    return
  }

  const state = result.value

  if (state.version < Math.max(version, eventVersion)) {
    return
  }

  try {
    let active = $activeScene.get()

    try {
      const nextActive = state.active ? await resolveScene(state.active) : null

      if (nextActive?.url && nextActive.url !== active?.url) {
        await preload(nextActive.url)
      }

      active = nextActive
    } catch (error) {
      log.warn('scene', 'Active scene image could not be loaded:', error)
    }

    const pending = state.pending ? await resolveScene(state.pending) : null

    if (request !== stateRequest || epoch !== currentClearEpoch() || state.version < Math.max(version, eventVersion)) {
      return
    }

    const oldPending = $pendingScene.get()
    version = state.version
    $activeScene.set(active)
    $pendingScene.set(pending)
    $scenePolicy.set(state.policy)
    const taskStatus = !pending ? 'none' : pending.stage === 'waiting_upload' ? 'waiting_upload' : 'pending'
    $sceneTaskStatus.set(taskStatus)

    if (taskStatus === 'pending' && !$sceneTaskSlow.get()) {
      startPoll()
    } else if (taskStatus !== 'pending') {
      stopPoll()
      $sceneTaskSlow.set(false)
    }

    if (oldPending && !pending) {
      const detail = await authedApi<SceneWire>({ path: `/api/companion/scenes/${oldPending.id}` })

      if (request === stateRequest && epoch === currentClearEpoch() && detail.ok && detail.value?.status === 'ready') {
        notify({ kind: 'success', message: getStrings().living.toasts.sceneReady })
      }
    }

    if (request === stateRequest && epoch === currentClearEpoch()) {
      await loadSceneLibrary()
    }
  } catch (error) {
    log.warn('scene', 'Scene hydration failed:', error)
  }
}

const sceneError = (error: unknown): string =>
  backendDetailMessage(error, getStrings().living.toasts.sceneRegenerateFailed)

async function mutate<T>(path: string, method: 'POST' | 'PATCH' | 'DELETE', body?: object): Promise<T> {
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

  await hydrateScene()

  if (epoch !== currentClearEpoch()) {
    throw new Error(getStrings().living.toasts.sceneRegenerateFailed)
  }

  return result.value
}

export async function createScene(input: SceneGenerationInput = {}): Promise<boolean> {
  if (submitting || $sceneTaskStatus.get() !== 'none') {
    return false
  }

  const epoch = currentClearEpoch()
  const startVersion = version
  submitting = true
  stateRequest++

  try {
    const result = await authedApi<SceneWire>({ path: '/api/companion/scenes/generate', method: 'POST', body: input })

    if (epoch !== currentClearEpoch()) {
      return false
    }

    if (!result.ok || !result.value) {
      throw new Error(
        result.ok
          ? getStrings().living.toasts.sceneRegenerateFailed
          : result.reason === 'err'
            ? sceneError(result.error)
            : getStrings().living.toasts.sceneRegenerateFailed
      )
    }

    if (version === startVersion) {
      $pendingScene.set({ ...result.value, id: String(result.value.id), thumbnailUrl: result.value.url || '' })
      $sceneTaskStatus.set('pending')
    }

    $sceneTaskSlow.set(false)
    startPoll()
    await hydrateScene()

    return true
  } catch (error) {
    if (epoch === currentClearEpoch()) {
      notify({ kind: 'warning', message: sceneError(error) })
    }

    return false
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

export async function adoptSceneImage(image: { base64: string; contentType: string }): Promise<void> {
  const pending = $pendingScene.get()
  const path = pending?.stage === 'waiting_upload' ? `/${pending.id}/adopt` : '/adopt'
  await mutate(path, 'POST', { image: image.base64, content_type: image.contentType })
}

export async function discardPendingScene(): Promise<void> {
  const pending = $pendingScene.get()

  if (pending) {
    await mutate(`/${pending.id}/discard`, 'POST')
  }
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
