import type { VisualJob } from '@/modules/visuals'
import type { Dictionary } from '@/shared/strings'

export type VisualsCopy = Dictionary['visuals']

/** 任务尚未收尾：列表与详情据此标注等待中的状态。 */
export function isPendingState(state: string | null | undefined): boolean {
  return state === 'queued' || state === 'running' || state === 'paused'
}

/** 会自动推进的状态；暂停与终态不会自行变化，不据此轮询。 */
export function isAdvancingState(state: string | null | undefined): boolean {
  return state === 'queued' || state === 'running'
}

export function formatDuration(
  durationMs: number | null | undefined,
  seconds: (value: number) => string
): string | null {
  return durationMs && durationMs > 0 ? seconds(Math.round(durationMs / 1000)) : null
}

/** 准备任务优先标注动作名称，其余按动作与目标媒体类型。 */
export function visualJobLabel(job: VisualJob, t: VisualsCopy): string {
  if (job.preparation) {
    return job.action_name ? `${t.preparationJob} · ${job.action_name}` : t.preparationJob
  }

  if (!job.action_name) {
    return job.target_kind === 'video' ? t.jobVideo : t.jobImage
  }

  const mediaKind = job.target_kind === 'video' ? t.mediaKindVideo : t.mediaKindImage

  return `${job.action_name} · ${mediaKind}`
}
