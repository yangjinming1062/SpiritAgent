import { atom } from 'nanostores'

import { captureAuthScope } from '@/shared/lib/authed-api'
import { ipcErrorStatus } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'
import { registerStorageClearHandler } from '@/shared/lib/storage'
import { isRecord } from '@client-shared/is-record'

import type { ApiSchemas } from '../../../../shared/api.generated'
import { ApiError } from '../../../../shared/errors'

import type { VisualReceiptBody, VisualsListQuery } from './api'
import { visualsApi } from './api'
import { $ambientLoopChoice, feedDirectorPresentation, resolveAmbientLoop } from './director'
import {
  $performance,
  interruptPerformance,
  type PerformancePorts,
  performanceSettled,
  performanceStarted,
  receivePerformanceCommand
} from './performance-scheduler'
import { $stagePlan, deriveStagePlan } from './stage-plan'
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
export const $visualsTotal = atom<number | null>(null)
export const $visualsNextCursor = atom<string | null>(null)
export const $visualsLoading = atom(false)
export const $visualsError = atom<string | null>(null)
export const $visualsDetail = atom<{ id: string; detail: VisualDetail } | null>(null)
export const $visualsDetailError = atom<string | null>(null)
export const $visualsPresentation = atom<VisualsPresentation | null>(null)
export const $visualsPresentationError = atom<string | null>(null)
/** 桌面播放指令；`queued` 只表示受理，实际回执才更新呈现事实。 */
export const $visualPlayback = atom<VisualPlayCommand | null>(null)

let epoch = 0
let presentationRequest = 0
let refreshQuery: VisualsListQuery = {}
let refreshQueued = false
let refreshRequest: Promise<void> | null = null
let detailRequest: { id: string; request: Promise<VisualDetail> } | null = null
const playClients = new Map<string, string>()
const receiptQueues = new Map<string, Promise<void>>()
const receiptKinds = new Map<string, VisualReceiptBody['receipt_kind']>()

/** 每次绑定捕获账户作用域，旧宿主的迟到回包不能操作新账户。 */
export function visualPerformancePorts(): Omit<PerformancePorts, 'valid'> {
  const scope = captureAuthScope()

  return {
    dispatch: async request => {
      if (!scope?.()) {
        throw new Error('Performance account changed')
      }

      const command = await visualsApi.playVisual(request.visualId)

      if (scope()) {
        await refreshVisualPresentation()
      }

      return command
    },
    cancel: async (command, reason) => {
      if (!scope?.()) {
        return
      }

      const clientId = playClients.get(command.play_id) ?? crypto.randomUUID()

      if (!playClients.has(command.play_id)) {
        const result = await visualsApi.claimPlay(command.play_id, { client_id: clientId })

        if (!scope() || !result.claimed) {
          return
        }

        playClients.set(command.play_id, clientId)
      }

      await sendVisualReceipt(command.play_id, {
        status: 'interrupted',
        receipt_kind: receiptKinds.get(command.play_id) ?? command.media_kind,
        error: reason
      })
    },
    restore: async () => {
      if (scope?.()) {
        await refreshVisualPresentation()
      }
    }
  }
}

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
          $visualsNextCursor.set(list.next_cursor)
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
export function loadVisualDetail(visualId: string): Promise<VisualDetail> {
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
  const request = ++presentationRequest

  try {
    const response = await visualsApi.presentation()

    if (!scope?.() || epoch !== currentEpoch || request !== presentationRequest) {
      return
    }

    publishPresentation(response)
  } catch (error) {
    if (scope?.() && epoch === currentEpoch && request === presentationRequest) {
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
  const presentation = $visualsPresentation.get()
  feedDirectorPresentation(presentation)
  $stagePlan.set(deriveStagePlan(presentation, null, resolveAmbientLoop(presentation)))
}

$visualsPresentation.listen(refreshStagePlan)
$ambientLoopChoice.listen(() => {
  const presentation = $visualsPresentation.get()
  $stagePlan.set(deriveStagePlan(presentation, null, resolveAmbientLoop(presentation)))
})

export async function regenerateVisual(
  visualId: string,
  body: Parameters<typeof visualsApi.regenerate>[1]
): Promise<VisualJob> {
  const job = await visualsApi.regenerate(visualId, body)
  await Promise.all([refreshVisuals(), refreshVisualPresentation()])

  return job
}

export async function commandVisualJob(jobId: string, command: VisualJobCommand): Promise<void> {
  await visualsApi.jobCommand(jobId, { command })
  await refreshVisuals()
}

/** 播放认领：同一 play_id 只由一个可见舞台认领，认领后才允许上报回执。 */
export async function claimVisualPlay(playId: string, clientId: string): Promise<boolean> {
  const scope = captureAuthScope()

  try {
    const result = await visualsApi.claimPlay(playId, { client_id: clientId })

    if (!scope?.()) {
      return false
    }

    if (result.claimed) {
      playClients.set(playId, clientId)
    } else {
      performanceSettled(playId)
    }

    return result.claimed
  } catch (error) {
    if (scope?.()) {
      performanceSettled(playId)
    }

    throw error
  }
}

export async function sendVisualReceipt(playId: string, body: Omit<VisualReceiptBody, 'client_id'>): Promise<void> {
  const scope = captureAuthScope()
  const command = $performance.get().command

  if (scope?.() && body.status === 'started') {
    receiptKinds.set(playId, body.receipt_kind)
    performanceStarted(playId)
  }

  const previous = receiptQueues.get(playId) ?? Promise.resolve()

  const request = previous
    .catch(() => undefined)
    .then(async () => {
      const clientId = playClients.get(playId)

      if (!scope?.() || !clientId) {
        return
      }

      let terminal = body.status !== 'started'

      try {
        await visualsApi.receiptPlay(playId, { ...body, client_id: clientId })

        // 循环和图片只结算本次切换，之后由基础舞台继续播放，不逐圈建账。
        if (body.status === 'started' && command?.play_id === playId && command.loop && scope()) {
          await visualsApi.receiptPlay(playId, {
            status: 'finished',
            receipt_kind: body.receipt_kind,
            client_id: clientId
          })
          terminal = true
        }
      } catch (error) {
        if (scope() && body.status === 'started') {
          interruptPerformance(playId, 'Playback acknowledgment failed')
        }

        throw error
      } finally {
        if (scope() && terminal) {
          playClients.delete(playId)
          receiptKinds.delete(playId)
          performanceSettled(playId, body.status === 'failed')
        }
      }
    })
    .finally(() => {
      if (receiptQueues.get(playId) === request) {
        receiptQueues.delete(playId)
      }
    })

  receiptQueues.set(playId, request)

  return request
}

export function staticPresentationKey(
  target: Pick<ApiSchemas['StageReceipt'], 'request_id' | 'presentation_epoch' | 'state_revision'>
): string {
  return `${target.request_id}:${target.presentation_epoch}:${target.state_revision}`
}

/** 旧回执被拒绝后由调用方回源；传输失败保留错误，不转报成图片加载失败。 */
export async function sendStaticReceipt(body: ApiSchemas['StageReceipt']): Promise<boolean> {
  const scope = captureAuthScope()
  const presentation = $visualsPresentation.get()
  const target = presentation?.static_presentation

  if (
    !scope ||
    !target ||
    presentation?.pending_play ||
    $visualPlayback.get() ||
    staticPresentationKey(target) !== staticPresentationKey(body)
  ) {
    return false
  }

  try {
    await visualsApi.receiptStatic(body)

    return true
  } catch (error) {
    if (!scope()) {
      return false
    }

    if (ipcErrorStatus(error) !== 409) {
      throw error
    }

    log.info(
      'visuals',
      'Static receipt superseded',
      staticPresentationKey(body),
      error instanceof ApiError ? error.business.details : null
    )

    // 在线代次未变时，普通档位也须收敛已被生活或偏好更新作废的静态目标。
    if (
      error instanceof ApiError &&
      error.code === 'static_presentation_expired' &&
      isRecord(error.business.details) &&
      ['life_changed', 'state_changed'].includes(String(error.business.details.reason))
    ) {
      await visualsApi.restorePresentation()
    }

    return false
  }
}

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

      if (state && ['finished', 'failed', 'expired'].includes(state.status)) {
        performanceSettled(state.play_id)
        void refreshVisualPresentation()
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
