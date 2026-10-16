import { accountStorageKey, definePersistedAtom, storedJson } from '@/shared/lib/storage'
import type {
  CompanionEditorUiSnapshot,
  CompanionMemoryBrowser,
  CompanionMemoryDraft
} from '@client-shared/ipc/ui-state'
import { isCompanionEditorUiSnapshot } from '@client-shared/ipc/ui-state'

const KEY = 'da.companion.editors.v1'

const FALLBACK: CompanionEditorUiSnapshot = {
  persona: null,
  characterCards: {},
  memory: { presetId: 'companion', section: 'recall', browsers: {}, drafts: {}, profileDrafts: {} }
}

const state = definePersistedAtom<CompanionEditorUiSnapshot>({
  key: KEY,
  fallback: FALLBACK,
  isPersistable: isCompanionEditorUiSnapshot
})

export const $companionEditors = state.$atom
export const updateCompanionEditors = state.set
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
      selectedId: null,
      editorOpen: false,
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

  state.set({ memory: { ...memory, [kind]: next } })
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

window.addEventListener('storage', event => {
  if (event.key === accountStorageKey(KEY)) {
    state.$atom.set(storedJson(KEY, FALLBACK, isCompanionEditorUiSnapshot))
  }
})
