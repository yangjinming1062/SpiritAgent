import { atom } from 'nanostores'

import { apiSucceeded, authedApi, captureAuthScope } from '@/shared/lib/authed-api'
import { log } from '@/shared/lib/log'
import { registerStorageClearHandler } from '@/shared/lib/storage'

import type {
  DesktopVideoAction,
  DesktopVideoPlayCommand,
  DesktopVideoProposal,
  DesktopVideoReceiptStatus,
  DesktopVideoSet,
  DesktopVideoState
} from './types'

const API = '/api/companion/desktop-videos'

export const $desktopVideoState = atom<DesktopVideoState | null>(null)
export const $desktopVideoSets = atom<DesktopVideoSet[]>([])
export const $desktopVideoPlayback = atom<DesktopVideoPlayCommand | null>(null)
export const $desktopVideoLoading = atom(false)
export const $desktopVideoError = atom<string | null>(null)
export const $desktopVideoContextRevision = atom(0)

let refreshRequest: Promise<void> | null = null
let refreshAgain = false
let epoch = 0
let libraryVersion = -1

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

async function mutation<T>(path: string, body: object = {}, method: 'POST' | 'PUT' = 'POST'): Promise<T> {
  const result = await authedApi<T>({ path: `${API}${path}`, method, body })

  if (!result.ok) {
    throw result.reason === 'err' ? result.error : new Error('Account changed')
  }

  if (!result.value) {
    throw new Error('Desktop video response is unavailable')
  }

  return result.value
}

export async function ensureDesktopVideoCurrent(
  trigger: 'entry' | 'context_change' = 'entry'
): Promise<DesktopVideoState> {
  const state = await mutation<DesktopVideoState>('/ensure-current', { trigger })
  applyState(state)
  await refreshDesktopVideos()

  return state
}

export async function generateDesktopVideoAction(actionId: number, feedback?: string): Promise<void> {
  await mutation<DesktopVideoAction>(`/actions/${actionId}/generate`, feedback ? { feedback } : {})
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

export async function reviewDesktopVideoAction(actionId: number, accept: boolean): Promise<void> {
  await mutation<DesktopVideoAction>(`/actions/${actionId}/${accept ? 'accept' : 'reject'}`)
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
  const command = await mutation<DesktopVideoPlayCommand>('/play', {
    action_id: actionId,
    expected_set_id: setId,
    reason
  })

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

/** 仅真实的身份、换装或场景事件调用，初次水合不代表用户改变了组合。 */
export function notifyDesktopVideoContextChanged(): void {
  $desktopVideoContextRevision.set($desktopVideoContextRevision.get() + 1)
}

registerStorageClearHandler(() => {
  epoch += 1
  libraryVersion = -1
  refreshAgain = false
  refreshRequest = null
  $desktopVideoState.set(null)
  $desktopVideoSets.set([])
  $desktopVideoPlayback.set(null)
  $desktopVideoLoading.set(false)
  $desktopVideoError.set(null)
  $desktopVideoContextRevision.set(0)
})
