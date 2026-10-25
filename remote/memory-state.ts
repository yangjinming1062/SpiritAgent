import type { MemoryItem, MemoryListResponse } from '@protocol'

import type { ApiInput } from '../shared/api'

import type { RemoteApi } from './api'
import { saveWithReadback } from './save-result'

/** 单条记忆的未保存修改；映射里存在该条即表示它有未保存内容。 */
export interface MemoryDraft {
  baseContent: string
  content: string
  version: number
}

/** 草稿映射，键为记忆 ID。 */
export type MemoryDrafts = Record<string, MemoryDraft>

export function readMemoryDraft(drafts: MemoryDrafts, row: MemoryItem): MemoryDraft | undefined {
  return drafts[row.id]
}

/** 写入草稿；内容回到开始编辑时的原文即视为没有修改，删除该条草稿。 */
export function writeMemoryDraft(drafts: MemoryDrafts, row: MemoryItem, content: string): MemoryDrafts {
  const previous = drafts[row.id]
  const baseContent = previous?.baseContent ?? row.content

  if (content === baseContent) {
    return clearMemoryDraft(drafts, row)
  }

  return {
    ...drafts,
    [row.id]: { baseContent, content, version: previous?.version ?? row.content_version }
  }
}

export function storeMemoryDraft(drafts: MemoryDrafts, row: MemoryItem, draft: MemoryDraft | undefined): MemoryDrafts {
  if (!draft) {
    return clearMemoryDraft(drafts, row)
  }

  return { ...drafts, [row.id]: draft }
}

export function clearMemoryDraft(drafts: MemoryDrafts, row: MemoryItem): MemoryDrafts {
  if (!(row.id in drafts)) {
    return drafts
  }

  const next = { ...drafts }
  delete next[row.id]

  return next
}

/** 服务端版本已前进且草稿仍未保存时，需要用户核对最新内容。 */
export function isMemoryConflict(row: MemoryItem, draft: MemoryDraft | undefined): boolean {
  return !!draft && row.content_version > draft.version
}

/** 核对后保留自己的输入，把编辑起点更新到服务端版本。 */
export function acceptMemoryConflict(row: MemoryItem, draft: MemoryDraft): MemoryDraft | undefined {
  if (draft.content === row.content) {
    return undefined
  }

  return { baseContent: row.content, content: draft.content, version: row.content_version }
}

export async function* readActiveMemories(
  api: RemoteApi,
  query: ApiInput<'resource_catalog.get_memories'>['query']
): AsyncGenerator<MemoryItem[]> {
  let cursor: string | null = null
  while (true) {
    const result: MemoryListResponse = await api.invoke('resource_catalog.get_memories', {
      query: { ...query, status: 'active', limit: 500, cursor }
    })
    yield result.memories
    cursor = result.next_cursor ?? null
    if (!result.memories.length || !cursor) {
      return
    }
  }
}

/** 写入结果不明时回读核对：按内容检索同一条，取回服务端保存的版本。 */
export async function findSavedMemory(
  api: RemoteApi,
  preset: string,
  row: MemoryItem,
  content: string
): Promise<MemoryItem | null> {
  for await (const page of readActiveMemories(api, {
    system_preset_id: preset,
    kind: row.kind,
    q: content.trim()
  })) {
    const saved = page.find(value => value.id === row.id)
    if (saved) {
      return saved
    }
  }

  return null
}

export async function saveMemoryDraft(
  api: RemoteApi,
  {
    preset,
    row,
    draft,
    isCurrent,
    onUnconfirmed
  }: {
    preset: string
    row: MemoryItem
    draft: MemoryDraft
    isCurrent: () => boolean
    onUnconfirmed: () => void | Promise<void>
  }
): Promise<MemoryItem> {
  return saveWithReadback({
    write: () =>
      api.invoke('resource_catalog.patch_memory', {
        path: { memory_id: row.id },
        body: {
          system_preset_id: preset,
          expected_version: draft.version,
          content: draft.content
        }
      }),
    read: () => findSavedMemory(api, preset, row, draft.content),
    matches: latest => latest.content === draft.content.trim(),
    isCurrent,
    onUnconfirmed: () => onUnconfirmed()
  })
}

export async function forgetMemory(api: RemoteApi, preset: string, row: MemoryItem): Promise<void> {
  await api.invoke('resource_catalog.delete_memory', {
    path: { memory_id: row.id },
    query: { system_preset_id: preset }
  })
}
