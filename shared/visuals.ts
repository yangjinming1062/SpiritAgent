import type { ApiSchemas } from './api.generated'
import type { VisualJobCommand, VisualJobState } from './protocol'

export interface VisualMaterialOptions {
  outfits: { id: string; name: string }[]
  scenes: { id: string; name: string }[]
  actions: { id: string; name: string }[]
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
    outfits: wardrobe.outfits.filter(row => row.status === 'ready').map(row => ({ id: row.id, name: row.name })),
    scenes: scenes.filter(row => row.status === 'ready').map(row => ({ id: row.id, name: row.title })),
    actions: actions.filter(row => row.status === 'approved').map(row => ({ id: row.id, name: row.name }))
  }
}

export interface VisualCreationDraft {
  mode: 'create' | 'upload' | null
  assetKind: 'actor' | 'scene' | 'cinematic'
  mediaKind: 'image' | 'video'
  outfitId: string
  sceneId: string
  actionId: string
  requirements: string
  title: string
  description: string
}

export const emptyVisualCreation: VisualCreationDraft = {
  mode: null,
  assetKind: 'cinematic',
  mediaKind: 'image',
  outfitId: '',
  sceneId: '',
  actionId: '',
  requirements: '',
  title: '',
  description: ''
}

export function isVisualCreationDraft(value: unknown): value is VisualCreationDraft {
  if (typeof value !== 'object' || value === null) {
    return false
  }
  const draft = value as Record<string, unknown>
  return (
    (draft.mode === null || draft.mode === 'create' || draft.mode === 'upload') &&
    ['actor', 'scene', 'cinematic'].includes(String(draft.assetKind)) &&
    ['image', 'video'].includes(String(draft.mediaKind)) &&
    ['outfitId', 'sceneId', 'actionId', 'requirements', 'title', 'description'].every(
      key => typeof draft[key] === 'string'
    )
  )
}

export function visualCreationReady(draft: VisualCreationDraft): boolean {
  return (
    (draft.assetKind === 'actor' || Boolean(draft.sceneId)) &&
    (draft.assetKind === 'scene' || Boolean(draft.outfitId && draft.actionId))
  )
}

/** 两端共用工作项的状态操作；具体失败原因仍由服务端校验。 */
export function visualJobCommands(state: VisualJobState): VisualJobCommand[] {
  switch (state) {
    case 'queued':
    case 'running':
      return ['pause', 'cancel']
    case 'paused':
      return ['resume', 'cancel']
    case 'failed':
      return ['retry', 'cancel']
    case 'unknown':
      return ['cancel']
    case 'succeeded':
    case 'cancelled':
      return []
  }
}
