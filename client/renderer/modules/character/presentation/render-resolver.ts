import { getStrings } from '@/shared/strings'

import type { ActionCatalogStatus } from '../actions'
import type { VideoGenStage } from '../rendering/video'

import type { CompanionFallbackStatus, CompanionPresentation } from './types'

export const VIDEO_GEN_STAGE_TEXT_KEYS = {
  script: 'videoGenStageScript',
  pose: 'videoGenStagePose',
  submit: 'videoGenStageSubmit',
  generate: 'videoGenStageGenerate',
  download: 'videoGenStageDownload',
  process: 'videoGenStageProcess',
  publish: 'videoGenStagePublish'
} as const satisfies Record<VideoGenStage, string>

function fallback(
  fallbackStatus: CompanionFallbackStatus,
  fallbackMessage: string,
  fallbackActionAvailable: boolean
): CompanionPresentation {
  return {
    fallbackActionAvailable,
    fallbackMessage,
    fallbackStatus,
    renderer: 'fallback'
  }
}

/** 目录就绪挂媒体层；否则落蛋形，并区分准备中 / 生成中 / 失败 / 尚未就绪。 */
export function resolveCompanionPresentation(opts: {
  catalogStatus: ActionCatalogStatus
  generationState: 'idle' | 'generating' | 'failed'
  generationStage?: VideoGenStage | null
}): CompanionPresentation {
  if (opts.catalogStatus === 'ready') {
    return { renderer: 'media' }
  }

  const appearance = getStrings().living.appearance
  const egg = getStrings().companion.egg

  if (opts.generationState === 'generating') {
    const stage = opts.generationStage

    return fallback('generating', stage ? appearance[VIDEO_GEN_STAGE_TEXT_KEYS[stage]] : egg.generating, false)
  }

  if (opts.generationState === 'failed') {
    return fallback('failed', egg.failed, true)
  }

  if (opts.catalogStatus === 'idle' || opts.catalogStatus === 'loading') {
    return fallback('preparing', egg.preparing, false)
  }

  return fallback('unavailable', egg.unavailable, true)
}
