import { atom } from 'nanostores'

// 开发者日志状态：网关事件路由写入，精灵窗覆盖层（app/windows/sprite/developer-overlay）渲染。
export const $devMode = atom<boolean>(false)
export const $devLogs = atom<{ time: string; type: string; details: string }[]>([])

export function pushDevLog(type: string, details: string): void {
  const time = new Date().toLocaleTimeString()
  const current = $devLogs.get()
  $devLogs.set([{ time, type, details }, ...current.slice(0, 49)])
}
