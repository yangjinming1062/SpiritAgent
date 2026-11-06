import type { ApiSchemas } from './api.generated'
import type { VisualJobCommand, VisualJobState } from './protocol'

export interface VisualMaterialOption {
  id: string
  name: string
  preview?: string
}

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

export interface VisualMaterialOptions {
  outfits: VisualMaterialOption[]
  scenes: VisualMaterialOption[]
  actions: VisualMaterialOption[]
}

export async function loadVisualMaterials(reader: {
  outfits: () => Promise<ApiSchemas['OutfitListResponse']>
  scenes: (cursor?: string) => Promise<ApiSchemas['SceneListResponse']>
  actions: (cursor?: string) => Promise<ApiSchemas['ActionListResponse']>
}): Promise<VisualMaterialOptions> {
  async function allPages<T>(read: (cursor?: string) => Promise<{ rows: T[]; next: string | null }>): Promise<T[]> {
    const rows: T[] = []
    let cursor: string | undefined
    do {
      const page = await read(cursor)
      rows.push(...page.rows)
      cursor = page.next ?? undefined
    } while (cursor)
    return rows
  }
  const [wardrobe, scenes, actions] = await Promise.all([
    reader.outfits(),
    allPages(async cursor => {
      const page = await reader.scenes(cursor)
      return { rows: page.scenes, next: page.has_more ? page.next_cursor : null }
    }),
    allPages(async cursor => {
      const page = await reader.actions(cursor)
      return { rows: page.actions, next: page.has_more ? page.next_cursor : null }
    })
  ])
  return {
    outfits: wardrobe.outfits
      .filter(row => row.status === 'ready')
      .map(row => ({ id: row.id, name: row.name, preview: row.fullbody_url })),
    scenes: scenes
      .filter(row => row.status === 'ready')
      .map(row => ({ id: row.id, name: row.title, preview: row.url })),
    actions: actions.filter(row => row.status === 'approved').map(row => ({ id: row.id, name: row.name }))
  }
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
