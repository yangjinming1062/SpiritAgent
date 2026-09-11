import type { DesktopAuthBroadcast, DesktopAuthSnapshot } from '@ipc/contracts'
import { atom } from 'nanostores'

import { clearCompanionStorage } from '@/shared/lib/storage'

import { tearDownPrimaryGateway } from './gateway'

type AuthState =
  | { kind: 'pending' }
  | { error?: string; kind: 'unauthenticated' }
  | { kind: 'authenticated'; snapshot: DesktopAuthSnapshot }

export const $auth = atom<AuthState>({ kind: 'pending' })

function isExpiredSnapshot(snapshot: DesktopAuthSnapshot | null | undefined): boolean {
  const expiresAt = snapshot?.tokenExpiresAt

  return typeof expiresAt !== 'number' || !Number.isFinite(expiresAt) || expiresAt <= Date.now()
}

export async function hydrateAuth(): Promise<void> {
  try {
    const snapshot = await window.spiritagent.getSession()

    if (snapshot && snapshot.hasToken && !isExpiredSnapshot(snapshot)) {
      $auth.set({ kind: 'authenticated', snapshot })
    } else {
      $auth.set({ kind: 'unauthenticated' })
    }
  } catch (error) {
    $auth.set({
      error: error instanceof Error ? error.message : String(error),
      kind: 'unauthenticated'
    })
  }
}

// 鉴权广播应用：clearCompanionStorage 与 $auth.set 严格串行——避免新用户的
// hydrate 在 OPFS / localStorage 清完之前读到旧用户残留（renderMode / 模型缓存）。
// 代价是广播回调延迟 ~5–30ms，仍在合理范围。
export async function applyAuthBroadcast(payload: DesktopAuthBroadcast): Promise<void> {
  const { snapshot } = payload

  if (payload.authenticated && snapshot && snapshot.hasToken && !isExpiredSnapshot(snapshot)) {
    $auth.set({ kind: 'authenticated', snapshot })
  } else {
    // 仅在「从 authenticated → unauthenticated」时清存储：token 过期首启动（pending → unauthenticated）
    // 不该抹除已登录用户留下的窗口/面板偏好；clearCompanionStorage 是登出副作用，不是
    // 鉴权初始化的副作用。
    if ($auth.get().kind === 'authenticated') {
      await clearCompanionStorage()
    }

    $auth.set({ kind: 'unauthenticated' })
  }
}

export async function activate(payload: { code: string }): Promise<void> {
  try {
    const snapshot = await window.spiritagent.activate(payload)
    $auth.set({ kind: 'authenticated', snapshot })
  } catch (error) {
    $auth.set({
      error: error instanceof Error ? error.message : String(error),
      kind: 'unauthenticated'
    })
    throw error
  }
}

export async function refreshSession(): Promise<void> {
  // 刷新失败并不会自动登出（与 login() 不同）：旧 JWT 可能仍然有效，
  // 是否把错误抛出由调用方决定。
  const snapshot = await window.spiritagent.refreshSession()
  $auth.set({ kind: 'authenticated', snapshot })
}

export async function logout(): Promise<void> {
  try {
    await window.spiritagent.logout()
  } finally {
    tearDownPrimaryGateway()
    // 不在这里调 clearCompanionStorage：IPC logout 返回后主进程会广播
    // authenticated:false，applyAuthBroadcast 那条路径才是清空的唯一入口。
    // 重复调用会被 idempotent handler 吞掉，但避免双触发 React 重渲染。
    $auth.set({ kind: 'unauthenticated' })
  }
}
