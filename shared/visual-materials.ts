import type { ApiSchemas } from './api.generated'

type Component = ApiSchemas['ImportComponentView']

export function visualAnalysisControls(job: ApiSchemas['ImportAnalysisView'] | null | undefined) {
  const active = job?.state === 'queued' || job?.state === 'running'
  const canContinue =
    !active &&
    (!job?.result ||
      ['paused', 'cancelled', 'unknown'].includes(job.state) ||
      job.result.components.some(component => ['paused', 'failed'].includes(component.operation_state)) ||
      job.state === 'failed')
  const status = active
    ? 'working'
    : job?.state === 'awaiting_input'
      ? 'awaiting'
      : job?.state === 'failed'
        ? 'attention'
        : job?.state === 'paused' || job?.state === 'cancelled'
          ? 'paused'
          : job?.state === 'succeeded'
            ? 'collection'
            : 'pending'
  return { active, canContinue, status } as const
}

export function visualMaterialGroups(analysis: ApiSchemas['ImportAnalysisView'] | null | undefined) {
  return (['outfit', 'scene', 'action'] as const).map(kind => ({
    kind,
    components: analysis?.result?.components.filter(component => component.observation.kind === kind) ?? []
  }))
}

export function visualMaterialControls(component: Component) {
  const busy = ['queued', 'running', 'paused'].includes(component.operation_state)
  const linked = component.material_id !== null && ['created', 'reused'].includes(component.status)
  return {
    busy,
    linked,
    confirmation: component.status === 'ambiguous',
    needsAction: !busy && !linked && ['failed', 'ambiguous', 'recognized'].includes(component.status),
    status: busy ? component.operation_state : component.status,
    extractCommand: component.extract_command,
    extractLabel:
      component.extract_command === 'regenerate'
        ? 'regenerate'
        : component.extract_command === 'retry'
          ? component.candidate_verdict === 'pass' && component.preview_path
            ? 'retrySave'
            : 'resume'
          : 'extract',
    notice:
      component.failure_code ??
      (linked
        ? ''
        : component.status === 'ambiguous'
          ? 'ambiguous'
          : component.creation_method === 'generated' ||
              (component.observation.kind !== 'action' && !component.observation.extractable)
            ? 'completion'
            : ''),
    pose: component.observation.kind === 'action' && component.observation.start_seconds === null
  } as const
}

export const visualMaterialCopy = {
  zh: {
    title: '素材',
    groups: { outfit: '着装', scene: '环境', action: '动作' },
    start: '识别影像',
    retry: '继续处理',
    reanalyze: '重新识别',
    cancel: '取消处理',
    working: '正在整理影像…',
    idle: '原件已保存',
    pending: '尚未识别',
    empty: '未识别到此类素材',
    approved: '已通过检查，可在合适情境中使用',
    collection: '仅收藏，暂不可自动播放',
    awaiting: '有信息需要确认',
    attention: '素材整理未完成',
    paused: '处理已暂停，原件与进度已保留',
    manage: '调整与管理',
    confirm: '确认素材',
    existing: '选择已有素材',
    select: '请选择',
    applyResult: '应用分析结果',
    apply: '关联',
    change: '更换关联',
    extract: '提取并入库',
    resume: '继续处理',
    retrySave: '重试入库',
    regenerate: '重新提取',
    skip: '暂不关联',
    undo: '撤销本次入库',
    notes: '补充要求（可选）',
    pose: '静态姿态',
    generated: '生成补全',
    failed: '素材操作失败',
    states: {
      queued: '等待处理',
      running: '处理中',
      paused: '已暂停',
      succeeded: '已完成',
      failed: '需继续处理',
      cancelled: '已取消',
      unknown: '待核对',
      awaiting_input: '待确认'
    } as Record<string, string>,
    components: {
      recognized: '未关联',
      ambiguous: '待选择',
      reused: '已关联',
      created: '已入库',
      reviewing: '评审中',
      failed: '尚未入库',
      skipped: '未关联',
      undone: '已撤销入库',
      queued: '等待处理',
      running: '处理中',
      paused: '已暂停'
    } as Record<string, string>,
    stages: {
      match: '核对已有素材',
      organize: '整理素材',
      generate_material: '生成补全中',
      extraction_review: '检查素材中',
      action_review: '评审动作中',
      save_material: '保存入库中',
      presentation_review: '检查播放条件'
    } as Record<string, string>,
    notices: {
      quality_rejected: '本次提取未通过检查，尚未入库。',
      action_rejected: '动作未通过评审，可在动作库调整后重试。',
      generation_failed: '未取得可用素材，可以重新提取。',
      result_download_failed: '素材下载未完成，继续处理会重试原结果。',
      result_unknown: '生成结果待核对，继续处理不会重新提交生成。',
      save_failed: '素材尚未保存，重试入库会复用已有结果。',
      processing_failed: '处理未完成，已保存的结果会继续保留。',
      identity_unavailable: '请先完成伙伴形象设置。',
      ambiguous: '可关联已有素材，也可提取新素材。',
      completion: '提取时会生成补全，遮挡部分为推测。'
    } as Record<string, string>
  },
  en: {
    title: 'Materials',
    groups: { outfit: 'Outfit', scene: 'Environment', action: 'Action' },
    start: 'Identify visual',
    retry: 'Continue processing',
    reanalyze: 'Identify again',
    cancel: 'Cancel processing',
    working: 'Identifying visual…',
    idle: 'Original saved',
    pending: 'Not identified yet',
    empty: 'No material of this type identified',
    approved: 'Checked and available for suitable situations',
    collection: 'Saved for viewing; automatic playback unavailable',
    awaiting: 'Some information needs confirmation',
    attention: 'Material organization incomplete',
    paused: 'Paused; original and progress preserved',
    manage: 'Adjust and manage',
    confirm: 'Confirm material',
    existing: 'Choose an existing material',
    select: 'Select',
    applyResult: 'Apply analysis results',
    apply: 'Link',
    change: 'Change link',
    extract: 'Extract and save',
    resume: 'Continue processing',
    retrySave: 'Retry saving',
    regenerate: 'Extract again',
    skip: 'Leave unlinked',
    undo: 'Undo this addition',
    notes: 'Additional requirements (optional)',
    pose: 'Static pose',
    generated: 'Generated completion',
    failed: 'Material operation failed',
    states: {
      queued: 'Waiting',
      running: 'Processing',
      paused: 'Paused',
      succeeded: 'Completed',
      failed: 'Needs attention',
      cancelled: 'Cancelled',
      unknown: 'Needs verification',
      awaiting_input: 'Needs confirmation'
    } as Record<string, string>,
    components: {
      recognized: 'Unlinked',
      ambiguous: 'Choose a material',
      reused: 'Linked',
      created: 'Saved to library',
      reviewing: 'Under review',
      failed: 'Not saved to library',
      skipped: 'Unlinked',
      undone: 'Addition undone',
      queued: 'Waiting',
      running: 'Processing',
      paused: 'Paused'
    } as Record<string, string>,
    stages: {
      match: 'Checking existing materials',
      organize: 'Organizing',
      generate_material: 'Generating completion',
      extraction_review: 'Checking material',
      action_review: 'Reviewing action',
      save_material: 'Saving to library',
      presentation_review: 'Checking playback conditions'
    } as Record<string, string>,
    notices: {
      quality_rejected: 'The extracted material did not pass checks and has not been saved.',
      action_rejected: 'The action did not pass review. Adjust it in the action library before retrying.',
      generation_failed: 'No usable material was produced. You can extract again.',
      result_download_failed: 'Download incomplete. Continue to retry the existing result.',
      result_unknown: 'The generation result needs verification. Continuing will not submit generation again.',
      save_failed: 'Not saved yet. Retry saving will reuse the existing result.',
      processing_failed: 'Processing incomplete. Saved progress is preserved.',
      identity_unavailable: 'Complete the companion appearance setup first.',
      ambiguous: 'Link an existing material or extract a new one.',
      completion: 'Extraction generates missing regions; hidden details are inferred.'
    } as Record<string, string>
  }
}
