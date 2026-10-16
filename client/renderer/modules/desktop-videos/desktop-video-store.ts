import { atom } from 'nanostores'

import { apiSucceeded, authedApi, captureAuthScope } from '@/shared/lib/authed-api'
import { backendDetailMessage, backendErrorDetail, ipcErrorStatus } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'
import { registerStorageClearHandler } from '@/shared/lib/storage'
import { backendApi, fileApi } from '@/shared/platform/desktop'
import { $presentation } from '@/shared/store/presentation'

import type {
  DesktopVideoAction,
  DesktopVideoAspectMismatch,
  DesktopVideoAspectPolicy,
  DesktopVideoPlayCommand,
  DesktopVideoPrompt,
  DesktopVideoProposal,
  DesktopVideoReceiptStatus,
  DesktopVideoRefinementPreview,
  DesktopVideoRefinementSource,
  DesktopVideoSet,
  DesktopVideoSource,
  DesktopVideoState
} from './types'

const API = '/api/companion/desktop-videos'
const DESKTOP_MODE_GATE_ERROR = '桌面模式当前未启用'
// 网关重连/显示器热插拔后，桌面激活信号经精灵窗 companion.signal 抵达后端存在秒级传播差；
// 主进程 presentation 已是 desktop+active，模式门禁 409 属暂态，按退避覆盖该窗口期。
const MODE_GATE_RETRY_DELAYS_MS = [500, 1000, 2000, 4000, 8000, 15000] as const

export const $desktopVideoState = atom<DesktopVideoState | null>(null)
export const $desktopVideoSets = atom<DesktopVideoSet[]>([])
export const $desktopVideoPlayback = atom<DesktopVideoPlayCommand | null>(null)
export const $desktopVideoLoading = atom(false)
export const $desktopVideoError = atom<string | null>(null)

let refreshRequest: Promise<void> | null = null
let refreshAgain = false
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

/** 水合仅查询；制作只由实际进入桌面或显式操作触发。 */
export async function refreshDesktopVideos(): Promise<void> {
  if (!captureAuthScope()) {
    return
  }

  if (refreshRequest) {
    refreshAgain = true

    return refreshRequest
  }

  const scope = captureAuthScope()
  const currentEpoch = epoch
  $desktopVideoLoading.set(true)

  const request = (async (): Promise<void> => {
    do {
      refreshAgain = false

      const results = await Promise.all([
        authedApi<DesktopVideoState>({ path: `${API}/state` }),
        authedApi<{ sets: DesktopVideoSet[]; version: number }>({ path: `${API}/sets` })
      ])

      if (!scope?.() || epoch !== currentEpoch) {
        return
      }

      const [state, sets] = results

      if (apiSucceeded(state, 'desktop-video', 'State refresh failed') && state.value) {
        applyState(state.value)
        $desktopVideoError.set(null)
      } else if (!state.ok && state.reason === 'err') {
        $desktopVideoError.set(String(state.error))
      }

      if (
        apiSucceeded(sets, 'desktop-video', 'Library refresh failed') &&
        sets.value &&
        sets.value.version >= libraryVersion
      ) {
        libraryVersion = sets.value.version
        $desktopVideoSets.set(sets.value.sets)
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

async function mutation<T>(
  path: string,
  body: object = {},
  method: 'POST' | 'PUT' | 'PATCH' | 'DELETE' = 'POST'
): Promise<T> {
  const result = await authedApi<T>({ path: `${API}${path}`, method, body })

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
  return ipcErrorStatus(error) === 409 && backendDetailMessage(error, '') === DESKTOP_MODE_GATE_ERROR
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
  const target = desktopScreenResolution()

  if (!target) {
    return Promise.reject(new Error('无法读取桌面显示器分辨率，请在创建桌景时填写宽高。'))
  }

  return withDesktopModeGateRetry(() => mutation<DesktopVideoState>('/ensure-current', { trigger: 'entry', ...target }))
}

export function desktopScreenResolution(): { target_width: number; target_height: number } | null {
  const presentation = $presentation.get()
  const display = presentation.displays.find(item => item.id === presentation.displayId)

  return display &&
    typeof display.pixelWidth === 'number' &&
    display.pixelWidth > 0 &&
    typeof display.pixelHeight === 'number' &&
    display.pixelHeight > 0
    ? { target_width: display.pixelWidth, target_height: display.pixelHeight }
    : null
}

export function desktopVideoAspectMismatch(error: unknown): DesktopVideoAspectMismatch | null {
  const detail = backendErrorDetail(error)

  if (!detail || typeof detail !== 'object' || !('reason' in detail) || detail.reason !== 'aspect_ratio_mismatch') {
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

export async function createDesktopVideoSet(input: {
  request_id: string
  name: string
  scene_id: number
  outfit_id: number
  source: DesktopVideoSource
  feedback?: string
  target_width: number
  target_height: number
}): Promise<DesktopVideoSet> {
  const set = await mutation<DesktopVideoSet>('/sets', input)
  await refreshDesktopVideos()

  return set
}

export async function renameDesktopVideoSet(setId: number, name: string): Promise<void> {
  await mutation(`/sets/${setId}`, { name }, 'PATCH')
  await refreshDesktopVideos()
}

export async function resizeDesktopVideoSet(set: DesktopVideoSet, width: number, height: number): Promise<void> {
  await mutation(
    `/sets/${set.id}`,
    {
      target_width: width,
      target_height: height,
      expected_resolution_revision: set.resolution_revision
    },
    'PATCH'
  )
  await refreshDesktopVideos()
}

export async function deleteDesktopVideoSet(setId: number): Promise<void> {
  applyState(await mutation<DesktopVideoState>(`/sets/${setId}`, {}, 'DELETE'))
  await refreshDesktopVideos()
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

      const state = await mutation<DesktopVideoState>(`/sets/${set.id}/activate`)

      if (isCurrent()) {
        applyState(state)
        await refreshDesktopVideos()
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

export async function generateDesktopVideoAction(
  action: DesktopVideoAction,
  set: DesktopVideoSet,
  feedback = '',
  aspectPolicy: DesktopVideoAspectPolicy = 'ask'
): Promise<DesktopVideoAction> {
  const generated = await mutation<DesktopVideoAction>(`/actions/${action.id}/generate`, {
    ...desktopActionGuard(action, set),
    feedback,
    aspect_policy: aspectPolicy
  })

  await refreshDesktopVideos()

  return generated
}

export async function generateDesktopVideoImage(
  action: DesktopVideoAction,
  set: DesktopVideoSet,
  feedback = '',
  regenerate = false,
  aspectPolicy: DesktopVideoAspectPolicy = 'ask'
): Promise<void> {
  await mutation(`/actions/${action.id}/image`, {
    ...desktopActionGuard(action, set),
    feedback,
    regenerate,
    aspect_policy: aspectPolicy
  })
  await refreshDesktopVideos()
}

function videoRefinementInput(
  action: DesktopVideoAction,
  set: DesktopVideoSet,
  source: DesktopVideoRefinementSource,
  feedback: string,
  aspectPolicy: 'keep' | 'expand'
): object {
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
  return mutation<DesktopVideoRefinementPreview>(
    `/actions/${action.id}/refine-video/preview`,
    videoRefinementInput(action, set, source, feedback, aspectPolicy)
  )
}

export async function refineDesktopVideo(
  action: DesktopVideoAction,
  set: DesktopVideoSet,
  source: DesktopVideoRefinementSource,
  feedback: string,
  aspectPolicy: 'keep' | 'expand'
): Promise<void> {
  await mutation<DesktopVideoAction>(
    `/actions/${action.id}/refine-video`,
    videoRefinementInput(action, set, source, feedback, aspectPolicy)
  )
  await refreshDesktopVideos()
}

export async function confirmDesktopVideoImage(
  action: DesktopVideoAction,
  set: DesktopVideoSet,
  generateVideo = true
): Promise<void> {
  if (!action.draft) {
    throw new Error('图片草稿已失效')
  }

  await mutation(`/actions/${action.id}/confirm-image`, {
    draft_id: action.draft.id,
    expected_base_version: set.base_version,
    expected_resolution_revision: set.resolution_revision,
    generate_video: generateVideo
  })
  await refreshDesktopVideos()
}

export async function cancelDesktopVideoDraft(action: DesktopVideoAction): Promise<void> {
  if (!action.draft) {
    return
  }

  await mutation(`/actions/${action.id}/cancel`, { draft_id: action.draft.id })
  await refreshDesktopVideos()
}

export async function pauseDesktopVideoDraft(action: DesktopVideoAction): Promise<void> {
  if (!action.draft) {
    return
  }

  await mutation(`/actions/${action.id}/pause`, { draft_id: action.draft.id })
  await refreshDesktopVideos()
}

export async function resumeDesktopVideoDraft(action: DesktopVideoAction, set: DesktopVideoSet): Promise<void> {
  if (!action.draft) {
    throw new Error('制作草稿已失效')
  }

  await mutation(`/actions/${action.id}/resume`, {
    draft_id: action.draft.id,
    expected_base_version: set.base_version,
    expected_resolution_revision: set.resolution_revision
  })
  await refreshDesktopVideos()
}

export async function getDesktopVideoPrompt(
  action: DesktopVideoAction,
  set: DesktopVideoSet,
  additionalRequirements = '',
  aspectPolicy: Exclude<DesktopVideoAspectPolicy, 'expand'> = 'ask'
): Promise<DesktopVideoPrompt> {
  return mutation<DesktopVideoPrompt>(`/actions/${action.id}/external-prompt`, {
    ...desktopActionGuard(action, set),
    expected_set_id: set.id,
    aspect_policy: aspectPolicy,
    requirements: additionalRequirements.trim()
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
  const proposal = await mutation<DesktopVideoProposal>('/design', input)
  await refreshDesktopVideos()

  return proposal
}

export async function reviewDesktopVideoAction(
  action: DesktopVideoAction,
  set: DesktopVideoSet,
  accept: boolean
): Promise<void> {
  if (!action.draft) {
    throw new Error('制作草稿已失效')
  }

  await mutation<DesktopVideoAction>(`/actions/${action.id}/${accept ? 'accept' : 'reject'}`, {
    draft_id: action.draft.id,
    expected_base_version: set.base_version,
    expected_resolution_revision: set.resolution_revision
  })
  await refreshDesktopVideos()
}

export async function setDesktopVideoPreferences(preferences: {
  pinned?: boolean
  autonomous_enabled?: boolean
}): Promise<void> {
  applyState(await mutation<DesktopVideoState>('/preferences', preferences, 'PUT'))
}

export async function playDesktopVideoAction(
  actionId: number,
  setId: number,
  reason = 'user'
): Promise<DesktopVideoPlayCommand> {
  const command = await withDesktopModeGateRetry(() =>
    mutation<DesktopVideoPlayCommand>('/play', {
      action_id: actionId,
      expected_set_id: setId,
      reason
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
  return (await mutation<{ claimed: boolean }>(`/plays/${encodeURIComponent(playId)}/claim`, { client_id: clientId }))
    .claimed
}

export async function acknowledgeDesktopVideoPlay(
  playId: string,
  clientId: string,
  status: DesktopVideoReceiptStatus,
  error?: string
): Promise<void> {
  await mutation(`/plays/${encodeURIComponent(playId)}/receipt`, {
    client_id: clientId,
    status,
    ...(error ? { error } : {})
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
})
