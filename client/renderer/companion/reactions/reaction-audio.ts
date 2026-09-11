import type { ReactionBucket, ReactionEntry } from '@/shared/types/reactions'

import { speakScripted } from '../tts'

import manifestJson from './manifest.json'

interface ManifestShape {
  buckets: readonly ReactionBucket[]
  files: readonly { id: string; tags: readonly string[]; bucket: ReactionBucket; text: string }[]
}

const MANIFEST = manifestJson as ManifestShape

const VALID_BUCKET: ReadonlySet<ReactionBucket> = new Set(MANIFEST.buckets)

// `Map<bucket, ReactionEntry[]>` — fast lookup by reaction bucket.
const index: Map<ReactionBucket, ReactionEntry[]> = (() => {
  const byBucket = new Map<ReactionBucket, ReactionEntry[]>()

  for (const file of MANIFEST.files) {
    if (!VALID_BUCKET.has(file.bucket)) {
      continue
    }

    let list = byBucket.get(file.bucket)

    if (!list) {
      list = []
      byBucket.set(file.bucket, list)
    }

    list.push({ id: file.id, tags: [...file.tags], bucket: file.bucket, text: file.text })
  }

  return byBucket
})()

/** 按性格标签交集匹配从指定 bucket 候选中选择反应条目。
 *  交集匹配 > 0 优先；通用条目/同 bucket 条目始终作为候选池的一部分。 */
export function pickReaction(bucket: ReactionBucket, companionTags: string[]): ReactionEntry | null {
  const candidates = index.get(bucket)

  if (!candidates || candidates.length === 0) {
    return null
  }

  if (companionTags.length > 0) {
    const tagSet = new Set(companionTags)

    const scored = candidates.map(e => ({
      entry: e,
      score: e.tags.filter(t => tagSet.has(t)).length
    }))

    const maxScore = Math.max(...scored.map(s => s.score))

    if (maxScore > 0) {
      const topMatches = scored.filter(s => s.score === maxScore || s.entry.tags.length === 0).map(s => s.entry)

      return topMatches[Math.floor(Math.random() * topMatches.length)]
    }
  }

  return candidates[Math.floor(Math.random() * candidates.length)]
}

/** 本地反应池入口（DESIGN §6.3 离线/机械降级）。首次播放合成一次并落盘，
 *  之后每次戳都是本地读盘。换音色或改台词会让缓存键失效，自动重新生成。 */
export async function playReactionAudio(entry: ReactionEntry | null): Promise<boolean> {
  if (!entry) {
    return false
  }

  return await speakScripted(entry.text, undefined, 'reaction')
}
