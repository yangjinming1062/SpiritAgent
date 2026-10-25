import { apiSucceeded } from '@/shared/lib/authed-api'
import { authedResource } from '@/shared/lib/resource-request'

import type { ApiSchemas } from '../../../../shared/api.generated'

export type VideoPublicationRecovery = ApiSchemas['PostPublicationRecovery']
type VideoPublicationRecoveryList = ApiSchemas['PostPublicationRecoveryList']

export async function listVideoPublications(cursor?: string): Promise<VideoPublicationRecoveryList | null> {
  const result = await authedResource('companion_posts.get_publication_recoveries', {
    query: { state: 'recoverable', cursor }
  })

  return apiSucceeded(result, 'posts', 'video recovery list failed') ? result.value : null
}

export async function queryVideoPublication(id: string): Promise<VideoPublicationRecovery | null> {
  const result = await authedResource('companion_posts.query_publication_recovery', {
    path: { publication_id: id }
  })

  return apiSucceeded(result, 'posts', 'video recovery query failed') ? result.value : null
}

export async function resolveVideoPublication(id: string): Promise<boolean> {
  const result = await authedResource('companion_posts.discard_publication', {
    path: { publication_id: id }
  })

  return apiSucceeded(result, 'posts', 'video recovery decision failed') && result.value !== null
}
