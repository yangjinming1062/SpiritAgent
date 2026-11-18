import { createQueryResource } from '@/shared/lib/query-resource'
import { requestResource } from '@/shared/lib/resource-request'

import type { ApiSchemas } from '../../../../shared/api.generated'
import { type ArchiveSearchFilters, archiveSearchQuery } from '../../../../shared/archive-search'

export const lifeSearch = createQueryResource<ApiSchemas['LifeTrajectorySearchResponse'] | null>(null, 'life-search')

export function searchLife(filters: ArchiveSearchFilters, pursuitId?: string, topicId?: string): Promise<void> {
  const query = { ...archiveSearchQuery(filters), pursuit_id: pursuitId, topic_id: topicId }

  return lifeSearch.load(JSON.stringify(query), () =>
    requestResource('companion_life.search_life_trajectory', { query })
  )
}
