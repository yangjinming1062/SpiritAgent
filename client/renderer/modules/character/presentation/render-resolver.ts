import type { CompanionPresentation } from './types'

/** 归并资产就绪状态，得出当前应挂载的渲染层。
 * 视频包就绪才挂视频层；未就绪落通用兜底（程序化蛋），不伪造任何资源。 */
export function resolveCompanionPresentation(opts: { videoReady: boolean }): CompanionPresentation {
  if (opts.videoReady) {
    return { renderer: 'video', videoReady: true }
  }

  return { renderer: 'fallback', videoReady: false }
}
