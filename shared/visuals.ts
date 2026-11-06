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
