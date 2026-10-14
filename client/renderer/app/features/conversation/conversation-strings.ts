import { useStore } from '@nanostores/react'

import { $locale } from '@/shared/store/locale'

const zh = {
  showConversations: '展开对话列表',
  hideConversations: '收起对话列表',
  companion: '陪伴对话',
  assistant: '专业对话',
  readOnly: '任务对话 · 只读',
  execution: '执行详情',
  closeExecution: '关闭执行详情',
  pinned: '置顶',
  recent: '最近对话',
  running: '正在运行',
  loadFailed: '对话加载失败，已有内容与草稿已保留。',
  retry: '重试'
}

const en: typeof zh = {
  showConversations: 'Show conversations',
  hideConversations: 'Hide conversations',
  companion: 'Companion chat',
  assistant: 'Assistant chat',
  readOnly: 'Task conversation · read-only',
  execution: 'Execution details',
  closeExecution: 'Close execution details',
  pinned: 'Pinned',
  recent: 'Recent conversations',
  running: 'Running',
  loadFailed: 'Conversation could not load. Your messages and draft have been retained.',
  retry: 'Retry'
}

export function useConversationStrings(): typeof zh {
  return useStore($locale) === 'zh' ? zh : en
}
