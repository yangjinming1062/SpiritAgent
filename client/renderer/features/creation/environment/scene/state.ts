import { atom } from 'nanostores'

import { backendDetailMessage } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'
import { authedResource, type ResourceArguments } from '@/shared/lib/resource-request'
import { currentClearEpoch, registerStorageClearHandler } from '@/shared/lib/storage'
import { trimOldest } from '@/shared/lib/trim-oldest'
import { backendApi } from '@/shared/platform/desktop'
import { getStrings } from '@/shared/strings'
import type { DesktopApiOperationKey } from '@ipc/contracts'

import type { ApiOperation } from '../../../../../../shared/api'
import type { ApiSchemas } from '../../../../../../shared/api.generated'

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
  review_job_id?: string | null
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
export interface SceneGenerationInput {
  notes?: string
  image?: string
}

export const $sceneLibrary = atom<SceneAsset[]>([])
export const $sceneDetails = atom<Record<string, SceneAsset>>({})
export const $sceneLibraryStatus = atom<'idle' | 'loading' | 'loaded' | 'error'>('idle')
export const $scenePolicy = atom<ScenePolicy>('llm_may_replace')
export const $sceneCreationIssue = atom<SceneCreationIssue | null>(null)
export const $sceneMutationIssues = atom<Record<string, SceneMutationIssue>>({})
export const $sceneTotal = atom(0)
export const $sceneHasMore = atom(false)
export const $scenePage = atom(0)
export const $sceneQuery = atom('')
export const PAGE_SIZE = 24
export const detailCache = new Map<string, SceneAsset>()
export const detailReads = new Map<string, number>()
// 场景图 URL 的签名 query 会轮换，同路径即同内容（资产名带随机后缀）：按去 query 路径缓存解析结果，避免轮询期重复取整图。
const MAX_SCENE_SRC_ENTRIES = 48
const sceneSrcCache = new Map<string, string>()
const sceneSrcRequests = new Map<string, Promise<string | null>>()
export const sceneIO = {
  cursors: [undefined] as (string | undefined)[],
  eventVersion: -1,
  listRequest: 0,
  mutationRevision: 0,
  nextRead: 0,
  stateRequest: 0,
  submitting: false
}

export function toScene(row: SceneWire, url: string, imageLoadFailed = false): SceneAsset {
  return { ...row, assetUrl: row.url || '', url, thumbnailUrl: url, imageLoadFailed }
}

/** 把场景图片解析为可用 src；失败抛出，由调用方决定保留旧图还是占位。 */
export async function resolveSceneUrl(row: SceneWire, retryImage = false): Promise<string> {
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
export async function resolveScene(row: SceneWire, retryImage = false): Promise<SceneAsset> {
  try {
    const url = await resolveSceneUrl(row, retryImage)

    await preload(url)

    return toScene(row, url)
  } catch (error) {
    log.warn('scene', 'Scene image could not be loaded:', error)

    return toScene(row, detailCache.get(row.id)?.url ?? '', true)
  }
}

export function cacheSceneDetails(entries: SceneAsset[], read: number): void {
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

export async function preload(url: string): Promise<void> {
  if (!url) {
    return
  }

  const image = new Image()
  image.src = url
  await image.decode()
}

registerStorageClearHandler(() => {
  sceneIO.stateRequest++
  sceneIO.listRequest++
  sceneIO.eventVersion = -1
  sceneIO.submitting = false
  $sceneLibrary.set([])
  detailCache.clear()
  detailReads.clear()
  sceneSrcCache.clear()
  sceneSrcRequests.clear()
  $sceneDetails.set({})
  $sceneLibraryStatus.set('idle')
  $sceneCreationIssue.set(null)
  $sceneMutationIssues.set({})
  $scenePolicy.set('llm_may_replace')
  $sceneTotal.set(0)
  $sceneHasMore.set(false)
  sceneIO.cursors = [undefined]
  $scenePage.set(0)
  $sceneQuery.set('')
})

export const sceneError = (error: unknown): string =>
  backendDetailMessage(error, getStrings().living.toasts.sceneRegenerateFailed)

// 写请求使在途的状态读取失效；返回非空 body，失败或请求期间账户被清理则抛出可直接展示的文案。
export async function sceneRequest<K extends DesktopApiOperationKey>(
  operation: K,
  ...args: ResourceArguments<K>
): Promise<ApiOperation<K>['response']> {
  const epoch = currentClearEpoch()
  sceneIO.mutationRevision++
  sceneIO.stateRequest++
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
