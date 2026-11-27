import { useEffect, useState } from 'react'

import type { ApiInput, ApiResponse } from './api'
import type { ApiSchemas } from './api.generated'
import type { MaterialOption } from './visual-materials'

type Analysis = ApiSchemas['ImportAnalysisView']
type Kind = ApiSchemas['ImportComponentView']['observation']['kind']
type Operation = 'companion.get_outfits' | 'companion_scenes.scene_list_route' | 'companion_actions.list_actions'
type ReadMaterials = <K extends Operation>(operation: K, input: ApiInput<K>) => Promise<ApiResponse<K>>

async function readOptions(read: ReadMaterials, kind: Kind, current: () => boolean): Promise<MaterialOption[]> {
  if (kind === 'outfit') {
    const { outfits } = await read('companion.get_outfits', {})
    return outfits
      .filter(row => row.status === 'ready')
      .map(row => ({
        id: row.id,
        name: row.name,
        preview: row.fullbody_url
      }))
  }
  const options: MaterialOption[] = []
  let cursor: string | null = null
  do {
    const query: { cursor: string | null; limit: number } = { cursor, limit: 100 }
    if (kind === 'scene') {
      const page = await read('companion_scenes.scene_list_route', { query })
      for (const row of page.scenes) {
        if (row.status === 'ready') {
          options.push({ id: row.id, name: row.title, preview: row.url })
        }
      }
      cursor = page.next_cursor
    } else {
      const page = await read('companion_actions.list_actions', { query: { ...query, group: 'in_library' } })
      options.push(...page.actions.map(row => ({ id: row.id, name: row.name })))
      cursor = page.next_cursor
    }
  } while (cursor && current())
  return options
}

function useGroup(read: ReadMaterials, kind: Kind, analysis: Analysis, captureScope?: () => (() => boolean) | null) {
  const components = analysis.result?.components.filter(row => row.observation.kind === kind) ?? []
  const revision = components.map(row => `${row.material_id}:${row.status}:${row.operation_state}`).join(',')
  const id = analysis.id
  const [saved, setSaved] = useState<{ id: string; options: MaterialOption[]; error?: unknown }>()
  useEffect(() => {
    const scope = captureScope?.()
    if (!revision || scope === null) {
      return
    }
    let live = true
    const current = () => live && (!scope || scope())
    void readOptions(read, kind, current)
      .then(options => {
        if (current()) {
          setSaved({ id, options })
        }
      })
      .catch(error => {
        if (current()) {
          setSaved(previous => ({ id, options: previous?.id === id ? previous.options : [], error }))
        }
      })
    return () => {
      live = false
    }
  }, [read, kind, id, revision, captureScope])
  const result = revision && saved?.id === id ? saved : undefined
  return { kind, components, options: result?.options ?? [], error: result?.error }
}

/** 只读有识别条目的素材库，按类别分页和刷新。 */
export function useVisualMaterialGroups(
  read: ReadMaterials,
  analysis: Analysis,
  captureScope?: () => (() => boolean) | null
) {
  return [
    useGroup(read, 'outfit', analysis, captureScope),
    useGroup(read, 'scene', analysis, captureScope),
    useGroup(read, 'action', analysis, captureScope)
  ]
}
