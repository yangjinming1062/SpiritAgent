import { useEffect, useState } from 'react'

import { loadVisualMaterials, type VisualMaterialOptions } from '../shared/visuals'

import type { RemoteApi } from './api'
import { errorText } from './api'
import { useAlive } from './ui'

/** 着装、环境、动作三类素材选项；revision 变化时重取，active 为假时不加载。 */
export function useMaterialOptions(
  api: RemoteApi,
  active: boolean,
  revision: unknown
): { materials: VisualMaterialOptions; error: string } {
  const alive = useAlive()
  const [materials, setMaterials] = useState<VisualMaterialOptions>({ outfits: [], scenes: [], actions: [] })
  const [error, setError] = useState('')

  useEffect(() => {
    if (!active) {
      return
    }
    let current = true
    void loadVisualMaterials({
      outfits: () => api.invoke('companion.get_outfits'),
      scenes: cursor => api.invoke('companion_scenes.scene_list_route', { query: { cursor, limit: 100 } }),
      actions: cursor => api.invoke('companion_actions.list_actions', { query: { cursor, limit: 100 } })
    })
      .then(value => {
        if (current && alive()) {
          setMaterials(value)
          setError('')
        }
      })
      .catch(failure => {
        if (current && alive()) {
          setError(errorText(failure))
        }
      })
    return () => {
      current = false
    }
  }, [api, active, revision, alive])

  return { materials, error }
}
