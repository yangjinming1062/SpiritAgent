import { atom } from 'nanostores'

// 渲染层启动失败原因：失败浮层据此显示并提供重试；主进程启动进度不进入本状态。
export const $desktopBootError = atom<string | null>(null)

export function failDesktopBoot(message: string): void {
  $desktopBootError.set(message)
}

export function clearDesktopBootFailure(): void {
  $desktopBootError.set(null)
}
