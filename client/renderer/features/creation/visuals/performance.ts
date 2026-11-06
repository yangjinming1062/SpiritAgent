import { captureAuthScope } from '@/shared/lib/authed-api'
import { ipcErrorStatus } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'
import { isRecord } from '@client-shared/is-record'

import type { ApiSchemas } from '../../../../../shared/api.generated'
import { ApiError } from '../../../../../shared/errors'

import type { VisualReceiptBody } from './api'
import { visualsApi } from './api'
import { refreshVisualPresentation } from './library'
import {
  $performance,
  interruptPerformance,
  type PerformancePorts,
  performanceSettled,
  performanceStarted
} from './performance-scheduler'
import { $visualPlayback, $visualsPresentation, playClients, receiptKinds, receiptQueues } from './state'

export function visualPerformancePorts(): Omit<PerformancePorts, 'valid'> {
  const scope = captureAuthScope()

  return {
    dispatch: async request => {
      if (!scope?.()) {
        throw new Error('Performance account changed')
      }

      const command = await visualsApi.playVisual(
        request.visualId,
        request.visualVersion,
        request.source === 'life' ? 'life' : 'user',
        request.source === 'life' ? (request.intentRevision ?? undefined) : undefined
      )

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
