import { requestResource } from '@/shared/lib/resource-request'
import type {
  ActionMediaListResponse,
  PlaybackReceiptKind,
  VisualDetail,
  VisualJob,
  VisualJobCommandRequest,
  VisualJobListResponse,
  VisualListResponse,
  VisualPresentationUpdate,
  VisualRegenerateRequest
} from '@protocol'

import type { ApiOperation } from '../../../../../shared/api'
import type { ApiSchemas } from '../../../../../shared/api.generated'
import { creationSubmitter } from '../creation-request'

import type { VisualPlayCommand, VisualsPresentation } from './types'

const submitCreation = creationSubmitter('da.visuals.creationRequests')

export type VisualsListQuery = ApiOperation<'companion_visuals.list_visuals'>['query']
type VisualJobsListQuery = ApiOperation<'companion_visuals.list_visual_jobs'>['query']

/** 播放回执：status 为实际回执状态，receipt_kind 区分完整图片、视频与窗口静态投影。 */
export interface VisualReceiptBody {
  status: 'started' | 'finished' | 'failed' | 'interrupted'
  receipt_kind: PlaybackReceiptKind
  error?: string
  client_id: string
}

export const visualsApi = {
  analyze: (visualId: string, body: ApiSchemas['ImportAnalysisRequest']): Promise<ApiSchemas['ImportAnalysisView']> =>
    requestResource('companion_visuals.analyze_visual', { path: { visual_id: visualId }, body }),
  interpret: (body: ApiSchemas['VisualInterpretRequest']): Promise<ApiSchemas['VisualInterpretation']> =>
    requestResource('companion_visuals.interpret_visual', { body }),
  /** 统一创建入口：同一份草稿复用同一个 request_id，重试不会产生第二个工作项。 */
  create: (draft: Record<string, unknown>): Promise<VisualJob> =>
    submitCreation(draft, request_id =>
      requestResource('companion_visuals.create_visual', { body: { request_id, draft } })
    ),
  upload: (
    data: Uint8Array,
    query: { filename: string; title?: string; description?: string }
  ): Promise<VisualDetail> => requestResource('companion_visuals.upload_visual', { body: data, query }),
  receiptStatic: (body: ApiSchemas['StageReceipt']): Promise<{ ok: boolean }> =>
    requestResource('companion_visuals.static_receipt', { body }),
  list: (query: VisualsListQuery): Promise<VisualListResponse> =>
    requestResource('companion_visuals.list_visuals', { query }),
  detail: (visualId: string): Promise<VisualDetail> =>
    requestResource('companion_visuals.get_visual', { path: { visual_id: visualId } }),
  delete: (visualId: string): Promise<void> =>
    requestResource('companion_visuals.delete_visual', { path: { visual_id: visualId } }),
  listActionMedia: (actionId: string): Promise<ActionMediaListResponse> =>
    requestResource('companion_visuals.list_action_media', { path: { action_id: actionId }, query: { limit: 30 } }),

  regenerate: (visualId: string, body: VisualRegenerateRequest): Promise<VisualJob> =>
    requestResource('companion_visuals.regenerate_visual', { path: { visual_id: visualId }, body }),
  jobs: (query?: VisualJobsListQuery): Promise<VisualJobListResponse> =>
    requestResource('companion_visuals.list_visual_jobs', { query }),
  jobCommand: (jobId: string, body: VisualJobCommandRequest): Promise<VisualJob> =>
    requestResource('companion_visuals.command_visual_job', { path: { job_id: jobId }, body }),
  presentation: (): Promise<VisualsPresentation> => requestResource('companion_visuals.get_presentation'),
  restorePresentation: (): Promise<VisualPlayCommand | null> =>
    requestResource('companion_visuals.restore_presentation'),
  updatePresentation: (body: VisualPresentationUpdate): Promise<VisualsPresentation> =>
    requestResource('companion_visuals.put_presentation', { body }),
  /** 播放认领与回执仅当前舞台宿主使用；同一 play_id 只由一个可见舞台认领。 */
  claimPlay: (playId: string, body: ApiSchemas['VisualClaimRequest']): Promise<{ claimed: boolean }> =>
    requestResource('companion_visuals.claim_play', { path: { play_id: playId }, body }),
  receiptPlay: (playId: string, body: VisualReceiptBody): Promise<{ ok: boolean }> =>
    requestResource('companion_visuals.receipt_play', { path: { play_id: playId }, body }),
  /** 导演点播：走完整指令 + 认领 + 回执管线；仅舞台宿主使用。origin 区分用户点播
   * 与生活导演选择，life 请求携带选择时的生活修订供受理端核对意图仍有效。 */
  playVisual: (
    visualId: string,
    version: number,
    origin: 'user' | 'life' = 'user',
    lifeRevision?: number
  ): Promise<VisualPlayCommand> =>
    requestResource('companion_visuals.play_visual', {
      path: { visual_id: visualId },
      query: { version, origin, ...(lifeRevision !== undefined ? { life_revision: lifeRevision } : {}) }
    })
}
