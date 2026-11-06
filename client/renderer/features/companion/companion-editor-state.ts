import { atom } from 'nanostores'

import { registerStorageClearHandler } from '@/shared/lib/storage'
import type {
  CompanionEditorUiSnapshot,
  CompanionMemoryBrowser,
  CompanionMemoryDraft
} from '@client-shared/ipc/ui-state'

const FALLBACK: CompanionEditorUiSnapshot = {
  persona: null,
  memory: { presetId: 'companion', section: 'recall', browsers: {}, drafts: {}, profileDrafts: {} }
}

// 资料与记忆的编辑草稿是会话级状态：跨切页与模式交接保留（经主进程快照）、换号清空，不落盘。
const state = atom<CompanionEditorUiSnapshot>(FALLBACK)

export const $companionEditors = state
export const updateCompanionEditors = (patch: Partial<CompanionEditorUiSnapshot>): void =>
  state.set({ ...state.get(), ...patch })
export const captureCompanionEditorUiSnapshot = (): CompanionEditorUiSnapshot => structuredClone(state.get())

export function applyCompanionEditorUiSnapshot(snapshot: CompanionEditorUiSnapshot): void {
  state.set(structuredClone(snapshot))
}

export function memoryBrowser(presetId: string): CompanionMemoryBrowser {
  return (
    state.get().memory.browsers[presetId] ?? {
      status: 'active',
      query: '',
      page: 0,
      cursors: [null],
      scrollTop: 0
    }
  )
}

export function updateMemoryBrowser(presetId: string, update: Partial<CompanionMemoryBrowser>): void {
  const memory = state.get().memory
  const current = memoryBrowser(presetId)

  if (Object.entries(update).every(([key, value]) => current[key as keyof CompanionMemoryBrowser] === value)) {
    return
  }

  state.set({
    ...state.get(),
    memory: { ...memory, browsers: { ...memory.browsers, [presetId]: { ...current, ...update } } }
  })
}

export function editMemoryDraft(
  previous: CompanionMemoryDraft | undefined,
  content: string,
  row?: { content: string; content_version: number }
): CompanionMemoryDraft | undefined {
  const baseContent = previous?.baseContent ?? row?.content ?? ''

  return content === baseContent
    ? undefined
    : { content, baseContent, baseVersion: previous?.baseVersion ?? row?.content_version ?? 0 }
}

export function setMemoryDraft(
  kind: 'drafts' | 'profileDrafts',
  key: string,
  draft: CompanionMemoryDraft | undefined
): void {
  const memory = state.get().memory
  const next = { ...memory[kind] }

  if (!draft && !next[key]) {
    return
  }

  if (draft) {
    next[key] = draft
  } else {
    delete next[key]
  }

  state.set({ ...state.get(), memory: { ...memory, [kind]: next } })
}

export function completeMemoryDraft(
  kind: 'drafts' | 'profileDrafts',
  key: string,
  submitted: CompanionMemoryDraft,
  saved?: { content: string; content_version: number }
): void {
  const current = state.get().memory[kind][key]

  if (!current || current.baseVersion !== submitted.baseVersion) {
    return
  }

  if (current.content === submitted.content) {
    setMemoryDraft(kind, key, undefined)
  } else if (saved) {
    setMemoryDraft(
      kind,
      key,
      current.content === saved.content
        ? undefined
        : { content: current.content, baseContent: saved.content, baseVersion: saved.content_version }
    )
  }
}

registerStorageClearHandler(() => {
  state.set(structuredClone(FALLBACK))
})
