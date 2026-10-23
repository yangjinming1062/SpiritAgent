import { atom } from 'nanostores'

import { captureAuthScope } from '@/shared/lib/authed-api'
import { log } from '@/shared/lib/log'
import { registerStorageClearHandler } from '@/shared/lib/storage'

import type { ApiSchemas } from '../../../../shared/api.generated'

import type { VisualReceiptBody, VisualsListQuery } from './api'
import { visualsApi } from './api'
import type {
  VisualDetail,
  VisualItem,
  VisualJob,
  VisualJobCommand,
  VisualPlayCommand,
  VisualPresentationUpdate,
  VisualsPresentation
} from './types'

export const $visualsItems = atom<VisualItem[]>([])
export const $visualsJobs = atom<VisualJob[]>([])
export const $visualsTotal = atom(0)
export const $visualsLoading = atom(false)
export const $visualsError = atom<string | null>(null)
export const $visualsDetail = atom<{ id: number; detail: VisualDetail } | null>(null)
export const $visualsDetailError = atom<string | null>(null)
export const $visualsPresentation = atom<VisualsPresentation | null>(null)
export const $visualsPresentationError = atom<string | null>(null)
/** 桌面播放指令；`queued` 只表示受理，实际回执才更新呈现事实。 */
export const $visualPlayback = atom<VisualPlayCommand | null>(null)

let epoch = 0
let refreshQuery: VisualsListQuery = {}
let refreshQueued = false
let refreshRequest: Promise<void> | null = null
let detailRequest: { id: number; request: Promise<VisualDetail> } | null = null
const playClients = new Map<string, string>()

/**
 * 成品库与制作区一起刷新。并发调用合并为排队中的一次读取（后到的筛选生效），
 * 无参刷新沿用上次筛选；执行中的旧筛选结果不回写。
 */
export async function refreshVisuals(query?: VisualsListQuery): Promise<void> {
  const scope = captureAuthScope()

  if (!scope) {
    return
  }

  if (query) {
    refreshQuery = query
  }

  refreshQueued = true

  if (refreshRequest) {
    return refreshRequest
  }

  const currentEpoch = epoch
  const current = (): boolean => scope() && epoch === currentEpoch
  $visualsLoading.set(true)

  const request = (async (): Promise<void> => {
    while (refreshQueued && current()) {
      const pending = refreshQuery
      refreshQueued = false

      try {
        const [list, jobs] = await Promise.all([visualsApi.list(pending), visualsApi.jobs()])

        if (!current()) {
          return
        }

        if (pending === refreshQuery) {
          $visualsItems.set(list.visuals)
          $visualsTotal.set(list.total)
          $visualsJobs.set(jobs.jobs)
          $visualsError.set(null)
        }
      } catch (error) {
        if (current() && pending === refreshQuery) {
          $visualsError.set(String(error))
        }
      }
    }
  })().finally(() => {
    if (refreshRequest === request) {
      refreshRequest = null
    }

    if (current()) {
      $visualsLoading.set(false)
    }
  })

  refreshRequest = request

  return request
}

/** 详情按 ID 单读并合并在途请求；迟到回写受账户与代次守卫。 */
export function loadVisualDetail(visualId: number): Promise<VisualDetail> {
  const scope = captureAuthScope()

  if (detailRequest?.id === visualId) {
    return detailRequest.request
  }

  const currentEpoch = epoch
  $visualsDetailError.set(null)

  const request = visualsApi
    .detail(visualId)
    .then(detail => {
      if (scope?.() && epoch === currentEpoch && detailRequest?.request === request) {
        $visualsDetail.set({ id: visualId, detail })
        $visualsDetailError.set(null)
      }

      return detail
    })
    .catch(error => {
      if (scope?.() && epoch === currentEpoch && detailRequest?.request === request) {
        $visualsDetailError.set(String(error))
      }

      throw error
    })

  detailRequest = { id: visualId, request }

  void request
    .finally(() => {
      if (detailRequest?.request === request) {
        detailRequest = null
      }
    })
    .catch(() => undefined)

  return request
}

export function clearVisualDetail(): void {
  detailRequest = null
  $visualsDetail.set(null)
  $visualsDetailError.set(null)
}

export async function refreshVisualPresentation(): Promise<void> {
  const scope = captureAuthScope()

  if (!scope) {
    return
  }

  const currentEpoch = epoch

  try {
    const response = await visualsApi.presentation()

    if (!scope?.() || epoch !== currentEpoch) {
      return
    }

    publishPresentation(response)
  } catch (error) {
    if (scope?.() && epoch === currentEpoch) {
      $visualsPresentationError.set(String(error))
    }
  }
}

export async function setVisualPreferences(body: VisualPresentationUpdate): Promise<void> {
  publishPresentation(await visualsApi.updatePresentation(body))
}

function publishPresentation(response: VisualsPresentation): void {
  const previous = $visualsPresentation.get()

  if (
    previous &&
    (response.presentation_epoch < previous.presentation_epoch ||
      (response.presentation_epoch === previous.presentation_epoch &&
        response.state_revision < previous.state_revision))
  ) {
    return
  }

  $visualsPresentation.set(response)
  $visualsPresentationError.set(null)
}

export async function regenerateVisual(
  visualId: number,
  body: Parameters<typeof visualsApi.regenerate>[1]
): Promise<VisualJob> {
  const job = await visualsApi.regenerate(visualId, body)
  await Promise.all([refreshVisuals(), refreshVisualPresentation()])

  return job
}

export async function commandVisualJob(jobId: number, command: VisualJobCommand): Promise<void> {
  await visualsApi.jobCommand(jobId, { command })
  await refreshVisuals()
}

/** 播放认领：同一 play_id 只由一个可见舞台认领，认领后才允许上报回执。 */
export async function claimVisualPlay(playId: string, clientId: string): Promise<boolean> {
  const result = await visualsApi.claimPlay(playId, { client_id: clientId })

  if (result.claimed) {
    playClients.set(playId, clientId)
  }

  return result.claimed
}

export async function sendVisualReceipt(playId: string, body: Omit<VisualReceiptBody, 'client_id'>): Promise<void> {
  const clientId = playClients.get(playId)

  if (!clientId) {
    return
  }

  await visualsApi.receiptPlay(playId, { ...body, client_id: clientId })

  if (body.status !== 'started') {
    playClients.delete(playId)
  }
}

function publishPlayCommand(command: VisualPlayCommand): void {
  const previous = $visualPlayback.get()

  if (
    previous &&
    (previous.play_id === command.play_id ||
      previous.presentation_epoch > command.presentation_epoch ||
      (previous.presentation_epoch === command.presentation_epoch && previous.state_revision > command.state_revision))
  ) {
    return
  }

  $visualPlayback.set(command)
}

/**
 * 网关事件：`companion.visual.updated` 刷新库与呈现，`companion.visual.play`
 * 派发播放指令（载荷为播放指令本身），`companion.visual.playback` 同步播放账本。
 * 载荷类型由网关事件联合契约保证。
 */
export function handleVisualEvent(
  name:
    | 'companion.visual.updated'
    | 'companion.visual.initial_requested'
    | 'companion.visual.play'
    | 'companion.visual.playback',
  payload:
    | ApiSchemas['RpcVisualUpdatedEvent']
    | ApiSchemas['RpcEmptyResult']
    | VisualPlayCommand
    | ApiSchemas['RpcVisualPlaybackEvent']
    | null
): boolean {
  switch (name) {
    case 'companion.visual.updated':

    case 'companion.visual.initial_requested':
      void refreshVisuals()
      void refreshVisualPresentation()

      return true
    case 'companion.visual.play': {
      const command = payload as VisualPlayCommand | ApiSchemas['RpcVisualPlaybackEvent'] | null

      if (!command || !('play_id' in command) || !('expires_at' in command)) {
        log.warn('visuals', 'Invalid playback command ignored')

        return true
      }

      if (Date.parse(command.expires_at) > Date.now()) {
        publishPlayCommand(command)
      } else {
        log.warn('visuals', 'Expired playback request ignored', command.play_id)
      }

      return true
    }

    case 'companion.visual.playback': {
      const state = payload as ApiSchemas['RpcVisualPlaybackEvent'] | null
      const current = $visualPlayback.get()

      // 播放账本终态清除对应指令，失效指令不再占据呈现。
      if (
        current &&
        state !== null &&
        state.play_id === current.play_id &&
        (state.status === 'finished' || state.status === 'failed' || state.status === 'expired')
      ) {
        $visualPlayback.set(null)
      }

      return true
    }
  }
}

export function resetVisualsForAccount(): void {
  epoch += 1
  refreshQuery = {}
  refreshQueued = false
  refreshRequest = null
  detailRequest = null
  playClients.clear()
  $visualsItems.set([])
  $visualsJobs.set([])
  $visualsTotal.set(0)
  $visualsLoading.set(false)
  $visualsError.set(null)
  $visualsDetail.set(null)
  $visualsDetailError.set(null)
  $visualsPresentation.set(null)
  $visualsPresentationError.set(null)
  $visualPlayback.set(null)
}

registerStorageClearHandler(resetVisualsForAccount)
