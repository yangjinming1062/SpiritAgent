import { atom } from 'nanostores'

import { captureAuthScope } from '@/shared/lib/authed-api'
import { registerStorageClearHandler } from '@/shared/lib/storage'
import { isVisualsUiSnapshot, type StudioUiSnapshot, type VisualsUiSnapshot } from '@ipc/contracts'

import { InterpretationGuard } from '../../../../../shared/interpretation-guard'
import { VISUAL_CREATION_INTENTS, type VisualLibrarySection } from '../../../../../shared/visuals'

type SectionSnapshot = VisualsUiSnapshot['sections'][VisualLibrarySection]

function initialSection(section: VisualLibrarySection): SectionSnapshot {
  return {
    query: '',
    mediaKind: 'all',
    source: '',
    page: 0,
    cursors: [null],
    studio: {
      mode: 'idle',
      intent: VISUAL_CREATION_INTENTS[section][0],
      description: '',
      preview: null,
      refinement: ''
    }
  }
}

const FALLBACK: VisualsUiSnapshot = {
  section: 'complete',
  sections: { complete: initialSection('complete'), sprite: initialSection('sprite') }
}

// 影像筛选与制作草稿是会话级状态：跨切页保留、换号清空，不落盘；详情随组件生命周期。
const ui = atom<VisualsUiSnapshot>(FALLBACK)

const interpretation = new InterpretationGuard()
const studioDraftRevisions: Record<VisualLibrarySection, number> = { complete: 0, sprite: 0 }

export const $visualsUi = ui

export function updateVisualsUi(patch: Pick<VisualsUiSnapshot, 'analysisNotes' | 'analysisSelections'>): void {
  ui.set({ ...ui.get(), ...patch })
}

export function selectVisualsSection(section: VisualLibrarySection): void {
  const state = ui.get()

  if (section !== state.section) {
    interpretation.revise()
    ui.set({ ...state, section })
  }
}

/** 同一分区内的筛选与分页不影响在途整理。 */
export function updateVisualsFilters(patch: Partial<Omit<SectionSnapshot, 'studio'>>): void {
  const next = patch.page === 0 ? { ...patch, cursors: [null] } : patch
  const state = ui.get()
  ui.set({
    ...state,
    sections: { ...state.sections, [state.section]: { ...state.sections[state.section], ...next } }
  })
}

/** 制作草稿唯一写入口：任何变化都推进整理修订，在途整理的响应不落地。 */
export function updateStudio(section: VisualLibrarySection, next: Partial<StudioUiSnapshot>): void {
  const state = ui.get()
  const current = state.sections[section]

  if (section === state.section) {
    interpretation.revise()
  }

  if (Object.keys(next).some(key => key !== 'mode')) {
    studioDraftRevisions[section] += 1
  }

  ui.set({
    ...state,
    sections: { ...state.sections, [section]: { ...current, studio: { ...current.studio, ...next } } }
  })
}

/** 已提交的草稿离开编辑器后仍可结算，但仅限原账户与未编辑的草稿。 */
export function captureStudioDraft(
  section: VisualLibrarySection,
  studio: StudioUiSnapshot
): { scope: () => boolean; currentDraft: () => boolean } | null {
  const scope = captureAuthScope()
  const state = ui.get()

  if (!scope || state.section !== section || state.sections[section].studio !== studio) {
    return null
  }

  const revision = studioDraftRevisions[section]

  return { scope, currentDraft: () => scope() && studioDraftRevisions[section] === revision }
}

/** 发起一次影像描述整理，返回响应回来时的落地校验。 */
export function beginStudioInterpretation(): () => boolean {
  return interpretation.begin()
}

export function exportVisualsUiSnapshot(): VisualsUiSnapshot {
  return structuredClone(ui.get())
}

export function importVisualsUiSnapshot(snapshot: VisualsUiSnapshot): void {
  if (isVisualsUiSnapshot(snapshot)) {
    interpretation.revise()
    studioDraftRevisions.complete += 1
    studioDraftRevisions.sprite += 1
    ui.set(structuredClone(snapshot))
  }
}

registerStorageClearHandler(() => {
  interpretation.revise()
  ui.set(FALLBACK)
})
