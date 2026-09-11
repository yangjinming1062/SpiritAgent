// 返回 `window` 上可用的 AudioContext 构造函数，回退到 Safari 的
// 带前缀 `webkitAudioContext`。两者都不存在时返回 `null`——调用方
// 跳过音频分析 / 录音逻辑，而不是崩溃。

export function getAudioContextCtor(): typeof AudioContext | null {
  const Window = window as unknown as { webkitAudioContext?: typeof AudioContext }

  return window.AudioContext ?? Window.webkitAudioContext ?? null
}
