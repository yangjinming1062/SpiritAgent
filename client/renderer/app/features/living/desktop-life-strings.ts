import { useStore } from '@nanostores/react'

import { $locale } from '@/shared/store/locale'

const zh = {
  title: '桌景',
  hint: '把伙伴的穿着与生活环境融入桌面画面。',
  prepare: '准备当前画面',
  refresh: '刷新',
  loading: '正在读取…',
  empty: '还没有桌面画面。选择桌面模式或点击“准备当前画面”，制作当前穿着与场景的基础画面。',
  current: '当前组合',
  back: '返回组合列表',
  noPreview: '画面尚未准备',
  generate: '制作',
  remake: '重新制作',
  retry: '重试制作',
  resume: '继续制作',
  resumeHint: '已保存制作进度，可继续完成当前动作。',
  paused: '制作已暂停',
  play: '用于桌面',
  playing: '当前动作',
  accept: '采用',
  reject: '舍弃',
  loop: '持续状态',
  once: '短暂动作',
  pin: '固定当前动作',
  autonomy: '允许伙伴自主切换',
  feedback: '重做建议（可选）',
  currentOnly: '切换到对应穿着与场景后可用于桌面',
  design: '设计新动作',
  actionName: '动作名称',
  actionDescription: '生活姿态与画面描述',
  duration: '时长（秒）',
  submitDesign: '提交设计并制作',
  designSubmitted: '设计已提交，可在动作列表查看后续结果。',
  designs: '动作设计',
  inspectAction: '查看动作',
  proposalStatuses: {
    pending: '正在检查设计',
    approved: '设计已通过',
    reused: '已有合适动作',
    deferred: '等待资料补齐',
    rejected: '设计未通过'
  },
  fail: '桌面画面暂时不可用',
  actionNames: {
    idle: '安静相伴',
    focus: '专注活动',
    relax: '放松休息',
    look: '看向你',
    smile: '微笑回应',
    stretch: '舒展身体'
  },
  actionDescriptions: {
    idle: '以舒服自然的姿态安静相伴，保留呼吸、眨眼等细微变化。',
    focus: '在当前环境中专注于适合自身身体与性格的安静活动。',
    relax: '以自然的坐卧或休息姿态放松，保持轻微而连续的动作。',
    look: '自然地把注意力转向你，短暂关注后平静收束。',
    smile: '用符合自身性格的柔和神态短暂回应你。',
    stretch: '适度舒展身体，结束后重新放松。'
  },
  statuses: {
    queued: '尚未制作',
    processing: '正在制作',
    ready: '已就绪',
    failed: '制作失败',
    review_pending: '等待复核'
  }
}

const en: typeof zh = {
  title: 'Desktop scene',
  hint: 'Bring your companion, their outfit, and living space into your desktop.',
  prepare: 'Prepare current scene',
  refresh: 'Refresh',
  loading: 'Loading…',
  empty:
    'No desktop scenes yet. Select desktop mode or Prepare current scene to create a basic scene with the current outfit and setting.',
  current: 'Current combination',
  back: 'Back to combinations',
  noPreview: 'Scene is not ready yet',
  generate: 'Create',
  remake: 'Create again',
  retry: 'Retry creation',
  resume: 'Continue creation',
  resumeHint: 'Progress has been saved. Continue to finish this action.',
  paused: 'Creation paused',
  play: 'Use on desktop',
  playing: 'Current action',
  accept: 'Accept',
  reject: 'Discard',
  loop: 'Persistent state',
  once: 'Brief action',
  pin: 'Keep current action',
  autonomy: 'Allow companion to switch actions',
  feedback: 'Suggestions for recreation (optional)',
  currentOnly: 'Switch to the matching outfit and setting to use this on desktop',
  design: 'Design an action',
  actionName: 'Action name',
  actionDescription: 'Describe the pose, activity, and scene',
  duration: 'Duration (seconds)',
  submitDesign: 'Submit design and create',
  designSubmitted: 'Design submitted. Follow its progress in the action list.',
  designs: 'Action designs',
  inspectAction: 'View action',
  proposalStatuses: {
    pending: 'Reviewing design',
    approved: 'Design approved',
    reused: 'Existing action reused',
    deferred: 'Awaiting more information',
    rejected: 'Design rejected'
  },
  fail: 'Desktop scene is temporarily unavailable',
  actionNames: {
    idle: 'Quiet company',
    focus: 'Focused activity',
    relax: 'Relaxing',
    look: 'Look at you',
    smile: 'Smile back',
    stretch: 'Stretch'
  },
  actionDescriptions: {
    idle: 'Keep you company in a comfortable pose with subtle breathing and blinking.',
    focus: 'Focus on a quiet activity suited to their body, personality, and surroundings.',
    relax: 'Relax in a natural seated or reclining pose with gentle, continuous movement.',
    look: 'Turn their attention toward you briefly, then settle comfortably.',
    smile: 'Respond to you briefly with a gentle expression suited to their personality.',
    stretch: 'Stretch comfortably, then relax again.'
  },
  statuses: {
    queued: 'Not created',
    processing: 'Creating',
    ready: 'Ready',
    failed: 'Creation failed',
    review_pending: 'Awaiting review'
  }
}

export function useDesktopLifeStrings(): typeof zh {
  return useStore($locale) === 'en' ? en : zh
}
