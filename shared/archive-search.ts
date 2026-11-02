export interface ArchiveSearchFilters {
  query: string
  dateFrom: string
  dateTo: string
}

export interface ArchiveSearchState {
  draft: ArchiveSearchFilters
  applied: ArchiveSearchFilters | null
}

export function emptyArchiveSearch(): ArchiveSearchState {
  return { draft: { query: '', dateFrom: '', dateTo: '' }, applied: null }
}

export function isArchiveSearchState(value: unknown): value is ArchiveSearchState {
  if (!value || typeof value !== 'object') {
    return false
  }

  const state = value as Record<string, unknown>
  const filters = (item: unknown): item is ArchiveSearchFilters => {
    if (!item || typeof item !== 'object') {
      return false
    }

    const fields = item as Record<string, unknown>

    return (
      typeof fields.query === 'string' &&
      fields.query.length <= 1000 &&
      ['dateFrom', 'dateTo'].every(
        key => typeof fields[key] === 'string' && (fields[key] === '' || /^\d{4}-\d{2}-\d{2}$/.test(fields[key]))
      )
    )
  }

  return filters(state.draft) && (state.applied === null || filters(state.applied))
}

export function archiveSearchQuery(filters: ArchiveSearchFilters) {
  return { q: filters.query.trim(), date_from: filters.dateFrom || undefined, date_to: filters.dateTo || undefined }
}

const COPY = {
  zh: {
    life: '搜索生活历史',
    diary: '搜索日记',
    query: '主题或关键词',
    from: '开始日期',
    to: '结束日期',
    search: '搜索',
    clear: '清除筛选',
    loading: '正在查找…',
    retry: '重新搜索',
    failed: '暂时无法搜索，已有内容仍可查看。',
    empty: '暂未找到匹配内容。可以换个关键词，或扩大日期范围。',
    keyword: '当前按关键词匹配，可以换个词或调整日期再试。',
    indexing: '正在补齐历史检索，当前结果可能不完整，稍后可重新搜索。',
    limit: '显示最相关的前 20 条，更多内容可缩小主题或日期范围查找。',
    open: '查看原文',
    details: '查看事项',
    notes: '阅读笔记',
    source: '来源',
    facts: { intent: '计划', virtual_activity: '虚拟情景', execution: '执行过程', result: '已确认成果' },
    subjects: { companion: '伙伴', user: '用户', system: '系统' }
  },
  en: {
    life: 'Search life history',
    diary: 'Search diaries',
    query: 'Topic or keywords',
    from: 'Start date',
    to: 'End date',
    search: 'Search',
    clear: 'Clear filters',
    loading: 'Searching…',
    retry: 'Search again',
    failed: 'Search is unavailable. Existing content remains readable.',
    empty: 'No matches yet. Try another keyword or a wider date range.',
    keyword: 'Using keyword matching. Try another word or date range.',
    indexing: 'Historical search is being prepared. Results may be incomplete; search again later.',
    limit: 'Showing the 20 most relevant results. Narrow the topic or date range to find more.',
    open: 'Read original',
    details: 'View pursuit',
    notes: 'Reading notes',
    source: 'Source',
    facts: { intent: 'Plan', virtual_activity: 'Simulated scene', execution: 'Execution', result: 'Confirmed outcome' },
    subjects: { companion: 'Companion', user: 'User', system: 'System' }
  }
} as const

export function archiveSearchCopy(locale = 'zh') {
  return COPY[locale === 'en' ? 'en' : 'zh']
}
