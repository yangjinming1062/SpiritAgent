import { requestResource } from '@/shared/lib/resource-request'

import type {
  ActionDefinition,
  ActionDefinitionRequest,
  ActionListResponse,
  ActionUpdateRequest,
  PlaybackReceiptKind,
  VisualDetail,
  VisualJob,
  VisualJobCommandRequest,
  VisualJobListResponse,
  VisualListResponse,
  VisualMediaKind,
  VisualPresentationUpdate,
  VisualRegenerateRequest,
  VisualsPresentation
} from './types'

export interface VisualsListQuery {
  query?: string
  media_kind?: VisualMediaKind
  outfit_id?: string
  scene_id?: string
  action_id?: string
  source?: string
  cursor?: string
  include_total?: boolean
  limit?: number
}

/** 播放回执：status 为实际回执状态，receipt_kind 区分完整图片、视频与窗口静态投影。 */
export interface VisualReceiptBody {
  status: 'started' | 'finished' | 'failed' | 'interrupted'
  receipt_kind: PlaybackReceiptKind
  error?: string
  client_id: string
}

export const visualsApi = {
  list: (query: VisualsListQuery): Promise<VisualListResponse> =>
    requestResource('companion_visuals.list_visuals', { query }),
  detail: (visualId: string): Promise<VisualDetail> =>
    requestResource('companion_visuals.get_visual', { path: { visual_id: visualId } }),
  regenerate: (visualId: string, body: VisualRegenerateRequest): Promise<VisualJob> =>
    requestResource('companion_visuals.regenerate_visual', { path: { visual_id: visualId }, body }),
  jobs: (): Promise<VisualJobListResponse> => requestResource('companion_visuals.list_visual_jobs'),
  jobCommand: (jobId: string, body: VisualJobCommandRequest): Promise<VisualJob> =>
    requestResource('companion_visuals.command_visual_job', { path: { job_id: jobId }, body }),
  presentation: (): Promise<VisualsPresentation> => requestResource('companion_visuals.get_presentation'),
  updatePresentation: (body: VisualPresentationUpdate): Promise<VisualsPresentation> =>
    requestResource('companion_visuals.put_presentation', { body }),
  /** 播放认领与回执仅桌面承载使用；同一 play_id 只由一个可见舞台认领。 */
  claimPlay: (playId: string, body: { client_id: string }): Promise<{ claimed: boolean }> =>
    requestResource('companion_visuals.claim_play', { path: { play_id: playId }, body }),
  receiptPlay: (playId: string, body: VisualReceiptBody): Promise<{ ok: boolean }> =>
    requestResource('companion_visuals.receipt_play', { path: { play_id: playId }, body })
}

export const actionsApi = {
  list: (query: { query?: string; cursor?: string; limit?: number } = {}): Promise<ActionListResponse> =>
    requestResource('companion_actions.list_actions', { query }),
  create: (body: ActionDefinitionRequest): Promise<ActionDefinition> =>
    requestResource('companion_actions.create_action', { body }),
  update: (actionId: string, body: ActionUpdateRequest): Promise<ActionDefinition> =>
    requestResource('companion_actions.update_action', { path: { action_id: actionId }, body }),
  remove: (actionId: string): Promise<void> =>
    requestResource('companion_actions.delete_action', { path: { action_id: actionId } })
}
