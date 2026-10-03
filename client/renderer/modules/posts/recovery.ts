import { apiSucceeded, authedApi } from '@/shared/lib/authed-api'

type PublicationStatus =
  | 'queued'
  | 'running'
  | 'published'
  | 'partial'
  | 'failed'
  | 'blocked'
  | 'result_unknown'
  | 'declined'
  | 'discarded'

export interface VideoPublicationRecovery {
  publication_id: string
  status: PublicationStatus
  post_id: string | null
  error: string | null
  title: string
  video_status: 'pending' | 'ready' | 'failed' | 'unknown' | 'discarded'
  media_url: string | null
  can_adopt: boolean
  can_discard: boolean
}

export interface VideoPublicationRecoveryList {
  items: VideoPublicationRecovery[]
  next_offset: number | null
}

export async function listVideoPublications(offset = 0): Promise<VideoPublicationRecoveryList | null> {
  const result = await authedApi<VideoPublicationRecoveryList>({
    path: `/api/companion/posts/publications/recovery?offset=${offset}`
  })

  return apiSucceeded(result, 'posts', 'video recovery list failed') ? result.value : null
}

export async function queryVideoPublication(id: string): Promise<VideoPublicationRecovery | null> {
  const result = await authedApi<VideoPublicationRecovery>({
    method: 'POST',
    path: `/api/companion/posts/publications/${encodeURIComponent(id)}/query`
  })

  return apiSucceeded(result, 'posts', 'video recovery query failed') ? result.value : null
}

export async function resolveVideoPublication(id: string, action: 'adopt' | 'discard'): Promise<boolean> {
  const result = await authedApi<{ status: PublicationStatus }>({
    method: 'POST',
    path: `/api/companion/posts/publications/${encodeURIComponent(id)}/${action}`
  })

  return apiSucceeded(result, 'posts', 'video recovery decision failed') && result.value !== null
}
