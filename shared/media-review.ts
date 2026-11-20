import type { ApiSchemas } from './api.generated'
import { newRequestId } from './request-id'

export const mediaReviewCopy = {
  zh: {
    showCandidates: '查看候选',
    approve: '采用这个候选',
    regenerate: '重新生成',
    candidate: '候选',
    working: '正在处理…',
    approved: '已采用',
    loading: '正在读取候选…',
    mediaLoading: '正在加载预览…',
    mediaFailed: '预览加载失败',
    retryMedia: '重新加载预览',
    notes: '检查说明',
    reload: '刷新候选',
    noCandidate: '尚无可预览的候选',
    identity: '身份',
    goal: '制作目标',
    quality: '画面质量',
    motion: '动作轨迹',
    opening: '开场状态',
    pass: '通过',
    review: '存疑',
    fail: '未通过'
  },
  en: {
    showCandidates: 'View candidates',
    approve: 'Use this candidate',
    regenerate: 'Regenerate',
    candidate: 'Candidate',
    working: 'Working…',
    approved: 'Selected',
    loading: 'Loading candidates…',
    mediaLoading: 'Loading preview…',
    mediaFailed: 'Preview failed to load',
    retryMedia: 'Reload preview',
    notes: 'Review notes',
    reload: 'Refresh candidates',
    noCandidate: 'No candidate is available yet',
    identity: 'Identity',
    goal: 'Goal',
    quality: 'Quality',
    motion: 'Motion',
    opening: 'Opening',
    pass: 'Passed',
    review: 'Uncertain',
    fail: 'Failed'
  }
}

export type MediaReviewCopy = (typeof mediaReviewCopy)['zh']

export function manualMediaRequest(
  job: ApiSchemas['MediaJobView'],
  slot: number,
  action: 'approve' | 'regenerate',
  candidateId?: string,
  previous?: ApiSchemas['MediaManualRequest'] | null
): ApiSchemas['MediaManualRequest'] {
  if (
    previous &&
    previous.expected_revision === job.revision &&
    previous.slot === slot &&
    previous.action === action &&
    previous.candidate_id === (candidateId ?? null)
  ) {
    return previous
  }
  return {
    request_id: newRequestId(),
    slot,
    action,
    candidate_id: candidateId ?? null,
    expected_revision: job.revision
  }
}
