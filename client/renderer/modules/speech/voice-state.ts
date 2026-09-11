import { atom } from 'nanostores'

// 跨 companion renderer 共享的语音准备状态。引用计数持有：begin/end 成对调用，
// 仅在计数归零时发出 false——直接 set(true) 会让订阅者误判为「永远在准备」
// （onboarding-audio / tts 的 finally 因 isLatestGen 被 playGen 污染而失效的根因）。

export const $voicePreparing = atom<boolean>(false)

let activeCount = 0

export function beginVoicePreparing(): void {
  activeCount++

  if (activeCount === 1) {
    $voicePreparing.set(true)
  }
}

export function endVoicePreparing(): void {
  if (activeCount === 0) {
    return
  }

  activeCount--

  if (activeCount === 0) {
    $voicePreparing.set(false)
  }
}
