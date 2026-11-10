import type { ApiSchemas } from './api.generated'

export const mediaReviewCopy = {
  zh: {
    approve: '采用这个候选',
    regenerate: '重新生成',
    candidate: '候选',
    working: '正在处理…',
    approved: '已放行',
    loading: '正在读取候选…',
    mediaLoading: '正在加载预览…',
    mediaFailed: '预览加载失败',
    retryMedia: '重新加载预览',
    notes: '检查说明',
    reload: '刷新候选',
    receive: '核对原结果',
    receiving: '正在核对…',
    noCandidate: '尚无可预览的候选',
    identity: '身份',
    goal: '制作目标',
    quality: '画面质量',
    pass: '通过',
    review: '待人工确认',
    fail: '未通过'
  },
  en: {
    approve: 'Use this candidate',
    regenerate: 'Regenerate',
    candidate: 'Candidate',
    working: 'Working…',
    approved: 'Approved',
    loading: 'Loading candidates…',
    mediaLoading: 'Loading preview…',
    mediaFailed: 'Preview failed to load',
    retryMedia: 'Reload preview',
    notes: 'Review notes',
    reload: 'Refresh candidates',
    receive: 'Check original result',
    receiving: 'Checking result…',
    noCandidate: 'No candidate is available yet',
    identity: 'Identity',
    goal: 'Goal',
    quality: 'Quality',
    pass: 'Passed',
    review: 'Needs human review',
    fail: 'Failed'
  }
}

export type MediaReviewCopy = (typeof mediaReviewCopy)['zh']

// 手机端经局域网 HTTP 打开时不是安全上下文，crypto.randomUUID 不可用。
function newRequestId(): string {
  if (typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }

  const bytes = crypto.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('')

  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

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
