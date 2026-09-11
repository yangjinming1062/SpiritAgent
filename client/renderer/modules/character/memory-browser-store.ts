import { atom } from 'nanostores'

export type MemoryTab = 'recall' | 'auto_inject'

export const $memoryBrowserTab = atom<MemoryTab>('recall')

export function setMemoryBrowserTab(tab: MemoryTab): void {
  $memoryBrowserTab.set(tab)
}
