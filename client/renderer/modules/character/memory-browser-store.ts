import { atom } from 'nanostores'

export type MemoryTab = 'active' | 'candidate' | 'invalidated' | 'expired'

export const $memoryBrowserTab = atom<MemoryTab>('active')

export function setMemoryBrowserTab(tab: MemoryTab): void {
  $memoryBrowserTab.set(tab)
}
