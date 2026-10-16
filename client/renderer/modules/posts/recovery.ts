import { apiSucceeded } from '@/shared/lib/authed-api'
import { authedResource } from '@/shared/lib/resource-request'

import type { ApiSchemas } from '../../../../shared/api.generated'

export type VideoPublicationRecovery = ApiSchemas['PostPublicationRecovery']
type VideoPublicationRecoveryList = ApiSchemas['PostPublicationRecoveryList']

export async function listVideoPublications(offset = 0): Promise<VideoPublicationRecoveryList | null> {
  const result = await authedResource('GET /api/companion/posts/publications', {
    query: { state: 'recoverable', offset }
  })

  return apiSucceeded(result, 'posts', 'video recovery list failed') ? result.value : null
}

export async function queryVideoPublication(id: string): Promise<VideoPublicationRecovery | null> {
  const result = await authedResource('POST /api/companion/posts/publications/{publication_id}/query', {
    path: { publication_id: id }
  })

  return apiSucceeded(result, 'posts', 'video recovery query failed') ? result.value : null
}

export async function resolveVideoPublication(id: string, action: 'adopt' | 'discard'): Promise<boolean> {
  const result = await authedResource('POST /api/companion/posts/publications/{publication_id}/decision', {
    path: { publication_id: id },
    body: { decision: action === 'adopt' ? 'accept' : 'reject' }
  })

  return apiSucceeded(result, 'posts', 'video recovery decision failed') && result.value !== null
}
