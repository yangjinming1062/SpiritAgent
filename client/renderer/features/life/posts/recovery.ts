import { apiSucceeded } from '@/shared/lib/authed-api'
import { authedResource } from '@/shared/lib/resource-request'

import type { ApiSchemas } from '../../../../../shared/api.generated'

export type MediaPublicationRecovery = ApiSchemas['PostPublicationRecovery']
type MediaPublicationRecoveryList = ApiSchemas['PostPublicationRecoveryList']

export async function listMediaPublications(cursor?: string): Promise<MediaPublicationRecoveryList | null> {
  const result = await authedResource('companion_posts.get_publication_recoveries', {
    query: { cursor }
  })

  return apiSucceeded(result, 'posts', 'video recovery list failed') ? result.value : null
}

export async function queryMediaPublication(id: string): Promise<MediaPublicationRecovery | null> {
  const result = await authedResource('companion_posts.query_publication_recovery', {
    path: { publication_id: id }
  })

  return apiSucceeded(result, 'posts', 'video recovery query failed') ? result.value : null
}

export async function discardMediaPublication(id: string): Promise<boolean> {
  const result = await authedResource('companion_posts.discard_publication', {
    path: { publication_id: id }
  })

  return apiSucceeded(result, 'posts', 'video recovery decision failed') && result.value !== null
}
