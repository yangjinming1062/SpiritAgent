import { captureAuthScope } from '@/shared/lib/authed-api'
import { requestResource } from '@/shared/lib/resource-request'
import { definePersistedAtom } from '@/shared/lib/storage'

import type { ApiSchemas } from '../../../../shared/api.generated'

import type {
  ActionDefinition,
  ActionDefinitionRequest,
  ActionLibraryGroup,
  ActionListResponse,
  ActionMediaListResponse,
  ActionUpdateRequest,
  PlaybackReceiptKind,
  VisualDetail,
  VisualJob,
  VisualJobCommandRequest,
  VisualJobListResponse,
  VisualListResponse,
  VisualMediaKind,
  VisualPlayCommand,
  VisualPresentationUpdate,
  VisualRegenerateRequest,
  VisualsPresentation
} from './types'

const creationRequests = definePersistedAtom<{ ids: Record<string, string> }>({
  key: 'da.visuals.creationRequests',
  fallback: { ids: {} },
  isPersistable: (value): value is { ids: Record<string, string> } =>
    typeof value === 'object' &&
    value !== null &&
    'ids' in value &&
    typeof value.ids === 'object' &&
    value.ids !== null &&
    Object.values(value.ids).every(id => typeof id === 'string')
})

async function submitCreation<T>(key: string, submit: (requestId: string) => Promise<T>): Promise<T> {
  const scope = captureAuthScope()
  const requestId = creationRequests.get().ids[key] ?? crypto.randomUUID()
  creationRequests.set({ ids: { ...creationRequests.get().ids, [key]: requestId } })
  const result = await submit(requestId)

  if (scope?.()) {
    const pending = { ...creationRequests.get().ids }
    delete pending[key]
    creationRequests.set({ ids: pending })
  }

  return result
}

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
  receiptStatic: (body: ApiSchemas['StageReceipt']): Promise<{ ok: boolean }> =>
    requestResource('companion_visuals.static_receipt', { body }),
  list: (query: VisualsListQuery): Promise<VisualListResponse> =>
    requestResource('companion_visuals.list_visuals', { query }),
  detail: (visualId: string): Promise<VisualDetail> =>
    requestResource('companion_visuals.get_visual', { path: { visual_id: visualId } }),
  listActionMedia: (actionId: string): Promise<ActionMediaListResponse> =>
    requestResource('companion_visuals.list_action_media', { path: { action_id: actionId }, query: { limit: 30 } }),
  /** 制作透明主体动画：身份 + 造型 + 动作，不绑定环境；桌面与手机共用同一契约。 */
  createActorClip: (body: Omit<ApiSchemas['ActorClipRequest'], 'request_id'>): Promise<VisualJob> =>
    submitCreation(JSON.stringify(['actor', body]), request_id =>
      requestResource('companion_visuals.create_actor_clip', { body: { ...body, request_id } })
    ),
  /** 制作环境循环视频：单张已就绪的环境素材。 */
  createSceneMedia: (body: Omit<ApiSchemas['SceneMediaRequest'], 'request_id'>): Promise<VisualJob> =>
    submitCreation(JSON.stringify(['scene', body]), request_id =>
      requestResource('companion_visuals.create_scene_media', { body: { ...body, request_id } })
    ),
  regenerate: (visualId: string, body: VisualRegenerateRequest): Promise<VisualJob> =>
    requestResource('companion_visuals.regenerate_visual', { path: { visual_id: visualId }, body }),
  jobs: (): Promise<VisualJobListResponse> => requestResource('companion_visuals.list_visual_jobs'),
  jobCommand: (jobId: string, body: VisualJobCommandRequest): Promise<VisualJob> =>
    requestResource('companion_visuals.command_visual_job', { path: { job_id: jobId }, body }),
  presentation: (): Promise<VisualsPresentation> => requestResource('companion_visuals.get_presentation'),
  restorePresentation: (): Promise<VisualPlayCommand | null> =>
    requestResource('companion_visuals.restore_presentation'),
  updatePresentation: (body: VisualPresentationUpdate): Promise<VisualsPresentation> =>
    requestResource('companion_visuals.put_presentation', { body }),
  /** 播放认领与回执仅桌面承载使用；同一 play_id 只由一个可见舞台认领。 */
  claimPlay: (playId: string, body: { client_id: string }): Promise<{ claimed: boolean }> =>
    requestResource('companion_visuals.claim_play', { path: { play_id: playId }, body }),
  receiptPlay: (playId: string, body: VisualReceiptBody): Promise<{ ok: boolean }> =>
    requestResource('companion_visuals.receipt_play', { path: { play_id: playId }, body }),
  /** 导演点播：走完整指令 + 认领 + 回执管线；仅舞台宿主使用。 */
  playVisual: (visualId: string): Promise<VisualPlayCommand> =>
    requestResource('companion_visuals.play_visual', { path: { visual_id: visualId } })
}

export const actionsApi = {
  list: (
    query: { query?: string; cursor?: string; limit?: number; group?: ActionLibraryGroup } = {}
  ): Promise<ActionListResponse> => requestResource('companion_actions.list_actions', { query }),
  create: (body: ActionDefinitionRequest): Promise<ActionDefinition> =>
    requestResource('companion_actions.create_action', { body }),
  update: (actionId: string, body: ActionUpdateRequest): Promise<ActionDefinition> =>
    requestResource('companion_actions.update_action', { path: { action_id: actionId }, body }),
  review: (actionId: string, expectedUpdatedAt: string): Promise<ActionDefinition> =>
    requestResource('companion_actions.retry_review', {
      path: { action_id: actionId },
      body: { expected_updated_at: expectedUpdatedAt }
    }),
  remove: (actionId: string): Promise<void> =>
    requestResource('companion_actions.delete_action', { path: { action_id: actionId } })
}
