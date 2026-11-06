import { onMount } from 'nanostores'

import { captureAuthScope } from '@/shared/lib/authed-api'
import { $dayPhase } from '@/shared/lib/day-phase'
import { log } from '@/shared/lib/log'
import { registerStorageClearHandler } from '@/shared/lib/storage'

import type { ApiSchemas } from '../../../../../shared/api.generated'

import type { VisualsListQuery } from './api'
import { visualsApi } from './api'
import { $ambientLoopChoice, feedDirectorPresentation, resolveAmbientLoop } from './director'
import { performanceSettled, receivePerformanceCommand } from './performance-scheduler'
import { $stagePlan, deriveStagePlan } from './stage-plan'
import {
  $visualPlayback,
  $visualsDetail,
  $visualsDetailError,
  $visualsError,
  $visualsItems,
  $visualsJobs,
  $visualsLoading,
  $visualsNextCursor,
  $visualsPresentation,
  $visualsPresentationError,
  $visualsTotal,
  playClients,
  receiptKinds,
  receiptQueues,
  visualsState
} from './state'
import type {
  VisualDetail,
  VisualJob,
  VisualJobCommand,
  VisualPlayCommand,
  VisualPresentationUpdate,
  VisualsPresentation
} from './types'

export async function refreshVisuals(query?: VisualsListQuery): Promise<void> {
  const scope = captureAuthScope()

  if (!scope) {
    return
  }

  if (query) {
    visualsState.refreshQuery = query
  }

  visualsState.refreshQueued = true

  if (visualsState.refreshRequest) {
    return visualsState.refreshRequest
  }

  const currentEpoch = visualsState.epoch
  const current = (): boolean => scope() && visualsState.epoch === currentEpoch
  $visualsLoading.set(true)

  const request = (async (): Promise<void> => {
    while (visualsState.refreshQueued && current()) {
      const pending = visualsState.refreshQuery
      visualsState.refreshQueued = false

      try {
        const [list, jobs] = await Promise.all([visualsApi.list(pending), visualsApi.jobs()])

        if (!current()) {
          return
        }

        if (pending === visualsState.refreshQuery) {
          $visualsItems.set(list.visuals)
          $visualsTotal.set(list.total)
          $visualsNextCursor.set(list.next_cursor)
          $visualsJobs.set(jobs.jobs)
          $visualsError.set(null)
        }
      } catch (error) {
        if (current() && pending === visualsState.refreshQuery) {
          $visualsError.set(String(error))
        }
      }
    }
  })().finally(() => {
    if (visualsState.refreshRequest === request) {
      visualsState.refreshRequest = null
    }

    if (current()) {
      $visualsLoading.set(false)
    }
  })

  visualsState.refreshRequest = request

  return request
}

/** 普通读取合并在途请求；写入后 fresh 读取使旧快照失效，迟到回写仍受账户与代次守卫。 */
export function loadVisualDetail(visualId: string, options: { fresh?: boolean } = {}): Promise<VisualDetail> {
  const scope = captureAuthScope()

  if (!options.fresh && visualsState.detailRequest?.id === visualId) {
    return visualsState.detailRequest.request
  }

  const currentEpoch = visualsState.epoch
  $visualsDetailError.set(null)

  const request = visualsApi
    .detail(visualId)
    .then(detail => {
      if (scope?.() && visualsState.epoch === currentEpoch && visualsState.detailRequest?.request === request) {
        $visualsDetail.set({ id: visualId, detail })
        $visualsDetailError.set(null)
      }

      return detail
    })
    .catch(error => {
      if (scope?.() && visualsState.epoch === currentEpoch && visualsState.detailRequest?.request === request) {
        $visualsDetailError.set(String(error))
      }

      throw error
    })

  visualsState.detailRequest = { id: visualId, request }

  void request
    .finally(() => {
      if (visualsState.detailRequest?.request === request) {
        visualsState.detailRequest = null
      }
    })
    .catch(() => undefined)

  return request
}

export function clearVisualDetail(): void {
  visualsState.detailRequest = null
  $visualsDetail.set(null)
  $visualsDetailError.set(null)
}

export async function refreshVisualPresentation(): Promise<void> {
  const scope = captureAuthScope()

  if (!scope) {
    return
  }

  const currentEpoch = visualsState.epoch
  const request = ++visualsState.presentationRequest

  try {
    const response = await visualsApi.presentation()

    if (!scope?.() || visualsState.epoch !== currentEpoch || request !== visualsState.presentationRequest) {
      return
    }

    publishPresentation(response)
  } catch (error) {
    if (scope?.() && visualsState.epoch === currentEpoch && request === visualsState.presentationRequest) {
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

  if (
    previous &&
    response.presentation_epoch === previous.presentation_epoch &&
    (response.intent?.life_revision ?? -1) < (previous.intent?.life_revision ?? -1)
  ) {
    response = { ...response, intent: previous.intent }
  }

  $visualsPresentation.set(response)
  $visualsPresentationError.set(null)

  const command = $visualPlayback.get()

  if (
    command &&
    (command.presentation_epoch < response.presentation_epoch ||
      (command.presentation_epoch === response.presentation_epoch &&
        command.state_revision < response.state_revision) ||
      Date.parse(command.expires_at) <= Date.now())
  ) {
    $visualPlayback.set(null)
  }
}

// 呈现与待播指令任一变化都重新解析统一舞台计划；窗口与桌面消费同一份。
// 导演只在舞台宿主装配后接收刷新；常驻循环的本地选择参与主体层解析，
// 且轮换是导演自身的输出，不回灌导演（避免重入选择循环）。
function refreshStagePlan(): void {
  feedDirectorPresentation($visualsPresentation.get())
  updateStagePlan()
}

function updateStagePlan(): void {
  const presentation = $visualsPresentation.get()
  $stagePlan.set(deriveStagePlan(presentation, null, resolveAmbientLoop(presentation), false, $stagePlan.get().scene))
}

$visualsPresentation.listen(refreshStagePlan)
$ambientLoopChoice.listen(updateStagePlan)
onMount($stagePlan, () => $dayPhase.listen(updateStagePlan))

export async function regenerateVisual(
  visualId: string,
  body: Parameters<typeof visualsApi.regenerate>[1]
): Promise<VisualJob> {
  const job = await visualsApi.regenerate(visualId, body)
  await Promise.all([refreshVisuals(), refreshVisualPresentation()])

  return job
}

/** 只读任务摘要：登录、重连与活动投影补读用，不触碰列表筛选。 */
export async function refreshVisualJobs(): Promise<void> {
  const scope = captureAuthScope()

  if (!scope) {
    return
  }

  const currentEpoch = visualsState.epoch

  try {
    const jobs = await visualsApi.jobs()

    if (scope() && visualsState.epoch === currentEpoch) {
      $visualsJobs.set(jobs.jobs)
    }
  } catch (error) {
    if (scope() && visualsState.epoch === currentEpoch) {
      log.warn('visuals', 'Job summary refresh failed', error)
    }
  }
}

export async function commandVisualJob(jobId: string, command: VisualJobCommand): Promise<void> {
  await visualsApi.jobCommand(jobId, { command })
  await refreshVisuals()
}

/** 播放认领：同一 play_id 只由一个可见舞台认领，认领后才允许上报回执。 */
function publishPlayCommand(command: VisualPlayCommand): void {
  const previous = $visualPlayback.get()
  const presentation = $visualsPresentation.get()

  if (
    (presentation &&
      (presentation.presentation_epoch > command.presentation_epoch ||
        (presentation.presentation_epoch === command.presentation_epoch &&
          presentation.state_revision > command.state_revision))) ||
    (previous &&
      (previous.play_id === command.play_id ||
        previous.presentation_epoch > command.presentation_epoch ||
        (previous.presentation_epoch === command.presentation_epoch &&
          previous.state_revision > command.state_revision)))
  ) {
    return
  }

  $visualPlayback.set(command)
  receivePerformanceCommand(command)
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
    case 'companion.visual.initial_requested': {
      void refreshVisuals()
      void refreshVisualPresentation()
      const selected = $visualsDetail.get()

      if (selected) {
        void loadVisualDetail(selected.id).catch(error => log.warn('visuals', 'Analysis detail refresh failed', error))
      }

      return true
    }

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

      if (state && ['finished', 'failed', 'expired'].includes(state.status)) {
        performanceSettled(state.play_id)
        void refreshVisualPresentation()
      }

      return true
    }
  }
}

function resetVisualsForAccount(): void {
  visualsState.epoch += 1
  visualsState.refreshQuery = {}
  visualsState.refreshQueued = false
  visualsState.refreshRequest = null
  visualsState.detailRequest = null
  playClients.clear()
  receiptQueues.clear()
  receiptKinds.clear()
  $visualsItems.set([])
  $visualsJobs.set([])
  $visualsTotal.set(null)
  $visualsNextCursor.set(null)
  $visualsLoading.set(false)
  $visualsError.set(null)
  $visualsDetail.set(null)
  $visualsDetailError.set(null)
  $visualsPresentation.set(null)
  $visualsPresentationError.set(null)
  $visualPlayback.set(null)
  feedDirectorPresentation(null)
  $stagePlan.set(deriveStagePlan(null, null, null))
}

registerStorageClearHandler(resetVisualsForAccount)
