import { createQueryResource } from '@/shared/lib/query-resource'
import { requestResource } from '@/shared/lib/resource-request'

import type { ApiSchemas } from '../../../../../shared/api.generated'
import { type ArchiveSearchFilters, archiveSearchQuery } from '../../../../../shared/archive-search'

export const diarySearch = createQueryResource<ApiSchemas['DiarySearchResponse'] | null>(null, 'diary-search')

export function searchDiary(filters: ArchiveSearchFilters): Promise<void> {
  const query = archiveSearchQuery(filters)

  return diarySearch.load(JSON.stringify(query), () =>
    requestResource('companion_journal.search_diary_entries', { query })
  )
}
