import { atom } from 'nanostores'

// 账户快照恢复前不初始化默认会话，避免覆盖交接中的选择。
export const $uiStateReady = atom(false)
export const $uiStateError = atom<string | null>(null)
export const $uiStateRetry = atom(0)

export function retryUiState(): void {
  $uiStateRetry.set($uiStateRetry.get() + 1)
}
