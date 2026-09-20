import type { CharacterRenderMode, CompanionPresentation } from './types'

/** 归并偏好模式与资产就绪状态，得出当前应挂载的渲染层。
 * 偏好模式不改变裁决：视频包就绪才挂视频层；模型就绪或生成中挂模型层；
 * 两者都不可用时落通用兜底（程序化蛋），不伪造任何资源。 */
export function resolveCompanionPresentation(opts: {
  modelGenerating: boolean
  modelReady: boolean
  mode: CharacterRenderMode
  videoReady: boolean
}): CompanionPresentation {
  if (opts.videoReady) {
    return { mode: opts.mode, renderer: 'video', videoReady: true }
  }

  return {
    mode: opts.mode,
    renderer: opts.modelReady || opts.modelGenerating ? 'model' : 'fallback',
    videoReady: false
  }
}
