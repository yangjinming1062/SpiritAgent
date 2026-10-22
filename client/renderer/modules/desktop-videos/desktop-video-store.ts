import { atom } from 'nanostores'

import { apiSucceeded, captureAuthScope } from '@/shared/lib/authed-api'
import { log } from '@/shared/lib/log'
import { authedResource, type ResourceArguments } from '@/shared/lib/resource-request'
import { registerStorageClearHandler } from '@/shared/lib/storage'
import { backendApi, fileApi } from '@/shared/platform/desktop'
import { $presentation } from '@/shared/store/presentation'
import type { DesktopApiOperationKey } from '@ipc/contracts'

import type { ApiOperation } from '../../../../shared/api'
import type { ApiSchemas } from '../../../../shared/api.generated'
import { ApiError } from '../../../../shared/errors'

import { desktopDisplayResolution } from './display'
import type {
  DesktopVideoAction,
  DesktopVideoAspectMismatch,
  DesktopVideoAspectPolicy,
  DesktopVideoPlayCommand,
  DesktopVideoProposal,
  DesktopVideoReceiptStatus,
  DesktopVideoRefinementPreview,
  DesktopVideoRefinementSource,
  DesktopVideoSet,
  DesktopVideoState
} from './types'

// 网关重连/显示器热插拔后，桌面激活信号经精灵窗 companion.signal 抵达后端存在秒级传播差；
// 主进程 presentation 已是 desktop+active，模式门禁 409 属暂态，按退避覆盖该窗口期。
const MODE_GATE_RETRY_DELAYS_MS = [500, 1000, 2000, 4000, 8000, 15000] as const

export const $desktopVideoState = atom<DesktopVideoState | null>(null)
export const $desktopVideoSets = atom<DesktopVideoSet[]>([])
export const $desktopVideoPlayback = atom<DesktopVideoPlayCommand | null>(null)
export const $desktopVideoLoading = atom(false)
// 状态读取错误由桌面壁纸呈现，套装库读取错误只归管理页，两者互不牵连。
export const $desktopVideoError = atom<string | null>(null)
export const $desktopVideoLibraryError = atom<string | null>(null)

let refreshRequest: Promise<void> | null = null
let refreshAgain = false
// 本轮是否跳过状态读；合并进在途批次的调用只要有一方需要状态，这一轮就不能跳过。
let refreshSkipState = true
let epoch = 0
let libraryVersion = -1
let activationGeneration = 0
let activationWrite: Promise<void> = Promise.resolve()

let ensureRequest: {
  scope: () => boolean
  request: Promise<DesktopVideoState>
} | null = null

function applyState(state: DesktopVideoState): void {
  if (state.version >= ($desktopVideoState.get()?.version ?? -1)) {
    $desktopVideoState.set(state)
  }
}

/** 水合仅查询；素材制作由用户显式操作触发。skipState 用于写操作后只刷新套装库。 */
export async function refreshDesktopVideos(options: { skipState?: boolean } = {}): Promise<void> {
  if (!captureAuthScope()) {
    return
  }

  if (refreshRequest) {
    // 合并进在途批次：任一方需要状态就不能跳过状态读，否则这次刷新拿不到状态。
    refreshSkipState = refreshSkipState && Boolean(options.skipState)
    refreshAgain = true

    return refreshRequest
  }

  const scope = captureAuthScope()
  const currentEpoch = epoch
  refreshSkipState = Boolean(options.skipState)
  $desktopVideoLoading.set(true)

  const request = (async (): Promise<void> => {
    do {
      refreshAgain = false
      const skipState = refreshSkipState
      // 本轮按合并后的口径执行；之后加入的调用重新决定下一轮。
      refreshSkipState = true

      const [state, sets] = await Promise.all([
        skipState ? null : authedResource('companion_desktop_videos.read_state'),
        authedResource('companion_desktop_videos.read_sets')
      ])

      if (!scope?.() || epoch !== currentEpoch) {
        return
      }

      const stateReady = state !== null && apiSucceeded(state, 'desktop-video', 'State refresh failed')
      const libraryReady = apiSucceeded(sets, 'desktop-video', 'Library refresh failed')

      if (stateReady && state.value) {
        applyState(state.value)
      }

      if (libraryReady && sets.value && sets.value.version >= libraryVersion) {
        libraryVersion = sets.value.version
        $desktopVideoSets.set(sets.value.sets)
      }

      if (stateReady) {
        $desktopVideoError.set(null)
      } else if (state && !state.ok && state.reason === 'err') {
        $desktopVideoError.set(String(state.error))
      }

      if (libraryReady) {
        $desktopVideoLibraryError.set(null)
      } else if (!sets.ok && sets.reason === 'err') {
        $desktopVideoLibraryError.set(String(sets.error))
      }
    } while (refreshAgain && scope() && epoch === currentEpoch)
  })().finally(() => {
    if (refreshRequest === request) {
      refreshRequest = null
      $desktopVideoLoading.set(false)
    }
  })

  refreshRequest = request

  return request
}

async function mutation<K extends DesktopApiOperationKey>(
  operation: K,
  ...args: ResourceArguments<K>
): Promise<ApiOperation<K>['response']> {
  const result = await authedResource(operation, ...args)

  if (!result.ok) {
    throw result.reason === 'err' ? result.error : new Error('Account changed')
  }

  if (!result.value) {
    throw new Error('Desktop video response is unavailable')
  }

  return result.value
}

function desktopModeIsActive(): boolean {
  const presentation = $presentation.get()

  return presentation.effectiveMode === 'desktop' && presentation.status === 'active'
}

function waitForRetry(delayMs: number): Promise<void> {
  return new Promise(resolve => window.setTimeout(resolve, delayMs))
}

function isDesktopModeGateError(error: unknown): boolean {
  return error instanceof ApiError && error.status === 409 && error.business.code === 'desktop_mode_inactive'
}

/** 桌面渲染激活后，网关呈现信号可能尚未抵达 Backend；只重试明确的模式门禁，其他错误保持原语义。 */
async function withDesktopModeGateRetry<T>(request: () => Promise<T>): Promise<T> {
  const scope = captureAuthScope()

  for (let attempt = 0; ; attempt += 1) {
    try {
      return await request()
    } catch (error) {
      const delayMs = MODE_GATE_RETRY_DELAYS_MS[attempt]

      if (!isDesktopModeGateError(error) || delayMs === undefined || !scope?.()) {
        throw error
      }

      await waitForRetry(delayMs)

      if (!scope?.() || !desktopModeIsActive()) {
        throw error
      }
    }
  }
}

function ensureCurrentWithRetry(): Promise<DesktopVideoState> {
  return withDesktopModeGateRetry(() => {
    const presentation = $presentation.get()

    const target = desktopDisplayResolution(
      presentation.displays.find(display => display.id === presentation.displayId)
    )

    if (!target) {
      throw new Error('无法读取桌面显示器分辨率，请选择可用的桌面显示器或等待读取完成。')
    }

    return mutation('companion_desktop_videos.prepare_current', {
      body: {
        trigger: 'entry',
        target_width: target.width,
        target_height: target.height
      }
    })
  })
}

export function desktopVideoAspectMismatch(error: unknown): DesktopVideoAspectMismatch | null {
  if (!(error instanceof ApiError) || error.business.code !== 'aspect_ratio_mismatch') {
    return null
  }

  const detail = error.business.details

  if (!detail || typeof detail !== 'object') {
    return null
  }

  const item = detail as Partial<DesktopVideoAspectMismatch>

  return [item.source_width, item.source_height, item.target_width, item.target_height].every(
    value => typeof value === 'number' && Number.isFinite(value) && value > 0
  ) &&
    (item.media_kind === 'image' || item.media_kind === 'video') &&
    Array.isArray(item.choices)
    ? (item as DesktopVideoAspectMismatch)
    : null
}

export async function ensureDesktopVideoCurrent(): Promise<DesktopVideoState> {
  const scope = captureAuthScope()

  if (!scope) {
    return ensureCurrentWithRetry()
  }

  if (ensureRequest?.scope()) {
    return ensureRequest.request
  }

  const request = (async (): Promise<DesktopVideoState> => {
    const state = await ensureCurrentWithRetry()
    applyState(state)
    await refreshDesktopVideos()

    return state
  })()

  ensureRequest = { request, scope }

  try {
    return await request
  } finally {
    if (ensureRequest?.request === request) {
      ensureRequest = null
    }
  }
}

export function desktopActionGuard(
  action: DesktopVideoAction,
  set: DesktopVideoSet
): {
  expected_draft_id: string | null
  expected_base_version: number
  expected_resolution_revision: number
} {
  return {
    expected_draft_id: action.draft?.id ?? null,
    expected_base_version: set.base_version,
    expected_resolution_revision: set.resolution_revision
  }
}

export async function createDesktopVideoSet(
  input: ApiSchemas['DesktopVideoCreateSetRequest']
): Promise<DesktopVideoSet> {
  const set = await mutation('companion_desktop_videos.create_set', { body: input })
  await refreshDesktopVideos()

  return set
}

export async function renameDesktopVideoSet(setId: number, name: string): Promise<void> {
  await mutation('companion_desktop_videos.rename_set', { path: { set_id: setId }, body: { name } })
  await refreshDesktopVideos()
}

export async function deleteDesktopVideoSet(setId: number): Promise<void> {
  // 回包即完整状态，只需刷新套装库。
  applyState(await mutation('companion_desktop_videos.delete_set', { path: { set_id: setId } }))
  await refreshDesktopVideos({ skipState: true })
}

export async function activateDesktopVideoSet(set: DesktopVideoSet): Promise<boolean> {
  const generation = ++activationGeneration
  const scope = captureAuthScope()
  const isCurrent = (): boolean => generation === activationGeneration && scope?.() === true
  const idle = set.actions.find(action => action.key === 'idle' && action.playable)

  if (!idle?.video_url) {
    throw new Error('安静相伴的视频尚未就绪')
  }

  const bytes = await backendApi()
    .apiAssetBuffer({ url: idle.video_url, preferCache: true })
    .catch(error => {
      if (!isCurrent()) {
        return null
      }

      throw error
    })

  if (bytes === null || !isCurrent()) {
    return false
  }

  const mime = idle.video_url.split(/[?#]/)[0].toLowerCase().endsWith('.mov') ? 'video/quicktime' : 'video/mp4'
  const url = URL.createObjectURL(new Blob([new Uint8Array(bytes)], { type: mime }))
  const video = document.createElement('video')
  video.muted = true
  video.preload = 'auto'

  try {
    await new Promise<void>((resolve, reject) => {
      const timeout = window.setTimeout(() => finish(new Error('桌景视频预加载超时')), 30000)

      const finish = (error?: Error): void => {
        window.clearTimeout(timeout)
        video.onloadeddata = null
        video.onerror = null

        if (error) {
          reject(error)
        } else {
          resolve()
        }
      }

      video.onloadeddata = () => finish()
      video.onerror = () => finish(new Error('桌景视频无法播放'))
      video.src = url
      video.load()
    })

    // 按用户最后一次选择提交；已经发出的切换完成后，后一次才能写入，避免网络乱序反转选择。
    const write = activationWrite.then(async () => {
      if (!isCurrent()) {
        return
      }

      const state = await mutation('companion_desktop_videos.select_current_set', { body: { set_id: set.id } })

      if (isCurrent()) {
        applyState(state)
        await refreshDesktopVideos({ skipState: true })
      }
    })

    activationWrite = write.catch(() => undefined)
    await write

    return isCurrent()
  } catch (error) {
    if (!isCurrent()) {
      return false
    }

    throw error
  } finally {
    video.onloadeddata = null
    video.onerror = null
    video.pause()
    video.removeAttribute('src')
    video.load()
    URL.revokeObjectURL(url)
  }
}

async function submitDesktopVideoDraft(
  actionId: number,
  body: ApiOperation<'companion_desktop_videos.command_draft'>['body']
): Promise<DesktopVideoAction> {
  const action = await mutation('companion_desktop_videos.command_draft', { path: { action_id: actionId }, body })
  await refreshDesktopVideos()

  return action
}

export async function generateDesktopVideoAction(
  action: DesktopVideoAction,
  set: DesktopVideoSet,
  feedback = '',
  aspectPolicy: DesktopVideoAspectPolicy = 'ask',
  videoFeedback?: string | null
): Promise<DesktopVideoAction> {
  return submitDesktopVideoDraft(action.id, {
    command: 'generate-video',
    ...desktopActionGuard(action, set),
    feedback,
    aspect_policy: aspectPolicy,
    ...(videoFeedback !== undefined ? { video_feedback: videoFeedback } : {})
  })
}

export async function generateDesktopVideoImage(
  action: DesktopVideoAction,
  set: DesktopVideoSet,
  feedback = '',
  regenerate = false,
  aspectPolicy: DesktopVideoAspectPolicy = 'ask'
): Promise<void> {
  await submitDesktopVideoDraft(action.id, {
    command: 'generate-image',
    ...desktopActionGuard(action, set),
    feedback,
    regenerate,
    aspect_policy: aspectPolicy
  })
}

function videoRefinementInput(
  action: DesktopVideoAction,
  set: DesktopVideoSet,
  source: DesktopVideoRefinementSource,
  feedback: string,
  aspectPolicy: 'keep' | 'expand'
): ApiSchemas['DesktopVideoRefineRequest'] {
  return {
    ...desktopActionGuard(action, set),
    expected_action_version: action.version,
    source,
    feedback: feedback.trim(),
    aspect_policy: aspectPolicy
  }
}

export function previewDesktopVideoRefinement(
  action: DesktopVideoAction,
  set: DesktopVideoSet,
  source: DesktopVideoRefinementSource,
  feedback: string,
  aspectPolicy: 'keep' | 'expand'
): Promise<DesktopVideoRefinementPreview> {
  return mutation('companion_desktop_videos.preview_video_refinement', {
    path: { action_id: action.id },
    body: videoRefinementInput(action, set, source, feedback, aspectPolicy)
  })
}

export async function refineDesktopVideo(
  action: DesktopVideoAction,
  set: DesktopVideoSet,
  source: DesktopVideoRefinementSource,
  feedback: string,
  aspectPolicy: 'keep' | 'expand'
): Promise<void> {
  await submitDesktopVideoDraft(action.id, {
    ...videoRefinementInput(action, set, source, feedback, aspectPolicy),
    command: 'refine-video'
  })
}

export async function cancelDesktopVideoDraft(action: DesktopVideoAction): Promise<void> {
  if (!action.draft) {
    return
  }

  await submitDesktopVideoDraft(action.id, {
    command: 'cancel',
    draft_id: action.draft.id
  })
}

export async function pauseDesktopVideoDraft(action: DesktopVideoAction): Promise<void> {
  if (!action.draft) {
    return
  }

  await submitDesktopVideoDraft(action.id, {
    command: 'pause',
    draft_id: action.draft.id
  })
}

export async function resumeDesktopVideoDraft(action: DesktopVideoAction, set: DesktopVideoSet): Promise<void> {
  if (!action.draft) {
    throw new Error('制作草稿已失效')
  }

  await submitDesktopVideoDraft(action.id, {
    command: 'resume',
    draft_id: action.draft.id,
    expected_base_version: set.base_version,
    expected_resolution_revision: set.resolution_revision
  })
}

export async function retryDesktopVideoDraft(action: DesktopVideoAction, set: DesktopVideoSet): Promise<void> {
  if (!action.draft) {
    throw new Error('制作草稿已失效')
  }

  await submitDesktopVideoDraft(action.id, {
    command: 'retry',
    draft_id: action.draft.id,
    expected_base_version: set.base_version,
    expected_resolution_revision: set.resolution_revision
  })
}

export async function uploadDesktopVideoAction(
  action: DesktopVideoAction,
  set: DesktopVideoSet,
  path: string,
  authSessionId: string,
  mediaKind: 'video' | 'image' = 'video',
  aspectPolicy: DesktopVideoAspectPolicy = 'ask'
): Promise<void> {
  await fileApi().uploadDesktopVideo({
    actionId: action.id,
    authSessionId,
    expectedSetId: set.id,
    expectedActionVersion: action.version,
    path,
    mediaKind,
    expectedDraftId: action.draft?.id ?? null,
    expectedBaseVersion: set.base_version,
    expectedResolutionRevision: set.resolution_revision,
    aspectPolicy
  })
  await refreshDesktopVideos()
}

export async function designDesktopVideoAction(input: {
  name: string
  motion_description: string
  kind: 'loop' | 'once'
  duration_seconds: number
  expected_set_id: number
}): Promise<DesktopVideoProposal> {
  const proposal = await mutation('companion_desktop_videos.design_action', { body: input })
  await refreshDesktopVideos()

  return proposal
}

export async function setDesktopVideoPreferences(preferences: {
  pinned?: boolean
  autonomous_enabled?: boolean
}): Promise<void> {
  applyState(await mutation('companion_desktop_videos.update_preferences', { body: preferences }))
}

export async function playDesktopVideoAction(
  actionId: number,
  setId: number,
  reason = 'user'
): Promise<DesktopVideoPlayCommand> {
  const command = await withDesktopModeGateRetry(() =>
    mutation('companion_desktop_videos.play_action', {
      body: {
        action_id: actionId,
        expected_set_id: setId,
        reason
      }
    })
  )

  publishPlayCommand(command)
  await refreshDesktopVideos()

  return command
}

function publishPlayCommand(command: DesktopVideoPlayCommand): void {
  const previous = $desktopVideoPlayback.get()

  if (
    previous &&
    (previous.play_id === command.play_id ||
      previous.set_epoch > command.set_epoch ||
      (previous.set_epoch === command.set_epoch && Date.parse(previous.expires_at) > Date.parse(command.expires_at)))
  ) {
    return
  }

  $desktopVideoPlayback.set(command)
}

export async function claimDesktopVideoPlay(playId: string, clientId: string): Promise<boolean> {
  return (
    await mutation('companion_desktop_videos.claim_play', {
      path: { play_id: playId },
      body: { client_id: clientId }
    })
  ).claimed
}

export async function acknowledgeDesktopVideoPlay(
  playId: string,
  clientId: string,
  status: DesktopVideoReceiptStatus,
  error?: string
): Promise<void> {
  await mutation('companion_desktop_videos.receipt_play', {
    path: { play_id: playId },
    body: {
      client_id: clientId,
      status,
      ...(error ? { error } : {})
    }
  })
}

function isPlayCommand(value: unknown): value is DesktopVideoPlayCommand {
  if (!value || typeof value !== 'object') {
    return false
  }

  const item = value as Partial<DesktopVideoPlayCommand>

  return (
    typeof item.play_id === 'string' &&
    typeof item.set_id === 'number' &&
    typeof item.action_id === 'number' &&
    typeof item.set_epoch === 'number' &&
    typeof item.expires_at === 'string' &&
    Number.isFinite(Date.parse(item.expires_at)) &&
    typeof item.video_url === 'string' &&
    (item.poster_url === null || typeof item.poster_url === 'string') &&
    (item.kind === 'loop' || item.kind === 'once') &&
    (item.continuity === 'base' || item.continuity === 'independent') &&
    (item.base_version === null || (Number.isSafeInteger(item.base_version) && Number(item.base_version) > 0)) &&
    (item.continuity !== 'base' || item.base_version !== null) &&
    (item.continuity !== 'independent' || item.base_version === null) &&
    (item.transition === 'base' || item.transition === 'fade') &&
    (item.geometry_mode === 'target' || item.geometry_mode === 'keep') &&
    typeof item.version === 'number'
  )
}

export function handleDesktopVideoEvent(name: string, payload: unknown): void {
  if (name === 'companion.desktop_video.updated') {
    void refreshDesktopVideos()
  } else if (name === 'companion.desktop_video.play_requested' && isPlayCommand(payload)) {
    if (Date.parse(payload.expires_at) > Date.now() && $desktopVideoPlayback.get()?.play_id !== payload.play_id) {
      publishPlayCommand(payload)
    } else {
      log.warn('desktop-video', 'Expired playback request ignored', payload.play_id)
    }
  }
}

registerStorageClearHandler(() => {
  epoch += 1
  activationGeneration += 1
  activationWrite = Promise.resolve()
  libraryVersion = -1
  refreshAgain = false
  refreshRequest = null
  ensureRequest = null
  $desktopVideoState.set(null)
  $desktopVideoSets.set([])
  $desktopVideoPlayback.set(null)
  $desktopVideoLoading.set(false)
  $desktopVideoError.set(null)
  $desktopVideoLibraryError.set(null)
})
