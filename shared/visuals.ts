import type { ApiSchemas } from './api.generated'
import type { VisualJobCommand, VisualJobState } from './protocol'

export const VISUAL_LIBRARY_SECTIONS = ['complete', 'sprite'] as const
export type VisualLibrarySection = (typeof VISUAL_LIBRARY_SECTIONS)[number]

export const VISUAL_CREATION_INTENTS = {
  complete: ['photo', 'video'],
  sprite: ['motion']
} as const satisfies Record<VisualLibrarySection, readonly string[]>

export type VisualCreationIntent = (typeof VISUAL_CREATION_INTENTS)[VisualLibrarySection][number]

/** 草稿引用的素材已失效：客户端据此作废预览并让用户重新整理，而不是原样重试。 */
const STALE_DRAFT_CODES = ['material_not_ready', 'material_missing', 'material_mismatch'] as const

export function isStaleDraftError(code: string | null | undefined): boolean {
  return STALE_DRAFT_CODES.some(item => item === code)
}

/** 同一请求身份已绑定不同内容：换新请求身份或重新整理，不能原样重试。 */
export function isRequestConflictError(code: string | null | undefined): boolean {
  return code === 'request_conflict'
}

/** 环境动态整理的默认描述；整理请求与草稿绑定核对共用同一取值。 */
export const SCENERY_DEFAULT_DESCRIPTION = '让这个环境自然地动起来'

/** 环境动态整理时冻结的用户输入；与表单不一致时草稿作废，必须重新整理。 */
export interface MotionPlanInputs {
  description: string
  sceneId: string
  phase: '' | 'dawn' | 'day' | 'dusk' | 'night'
}

export function sameMotionInputs(a: MotionPlanInputs, b: MotionPlanInputs): boolean {
  return a.description === b.description && a.sceneId === b.sceneId && a.phase === b.phase
}

export function showVisualJobActivity(job: Pick<ApiSchemas['VisualJobView'], 'state' | 'request_source'>): boolean {
  const recoverableSource = job.request_source === 'user_requested' || job.request_source === 'stage_preparation'

  return (
    job.state !== 'succeeded' &&
    job.state !== 'cancelled' &&
    ((job.state !== 'failed' && job.state !== 'unknown') || recoverableSource)
  )
}

/** 两端共用工作项的状态操作；具体失败原因仍由服务端校验。 */
export function visualJobCommands(state: VisualJobState, failureReason?: string | null): VisualJobCommand[] {
  switch (state) {
    case 'queued':
    case 'running':
      return ['pause', 'cancel']
    case 'paused':
      return ['resume', 'cancel']
    case 'failed':
      return ['retry', 'cancel']
    case 'unknown':
      return failureReason === 'result_download_failed' ? ['resume', 'cancel'] : ['cancel']
    case 'succeeded':
    case 'cancelled':
      return []
  }
}
