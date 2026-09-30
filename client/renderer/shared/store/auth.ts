import type { DesktopActivatePayload, DesktopAuthBroadcast, DesktopAuthSnapshot } from '@ipc/contracts'
import { atom } from 'nanostores'

import { setStorageAccount } from '@/shared/lib/storage'

import { tearDownPrimaryGateway } from './gateway'

type AuthState =
  | { kind: 'pending' }
  | { kind: 'switching' }
  | { error?: string; kind: 'unauthenticated' }
  | { kind: 'authenticated'; snapshot: DesktopAuthSnapshot }

export const $auth = atom<AuthState>({ kind: 'pending' })
let broadcastQueue: Promise<void> = Promise.resolve()

function isExpiredSnapshot(snapshot: DesktopAuthSnapshot | null | undefined): boolean {
  const expiresAt = snapshot?.tokenExpiresAt

  return typeof expiresAt !== 'number' || !Number.isFinite(expiresAt) || expiresAt <= Date.now()
}

export async function hydrateAuth(): Promise<void> {
  try {
    const snapshot = await window.spiritagent.getSession()

    if ($auth.get().kind !== 'pending') {
      return
    }

    if (snapshot && snapshot.hasToken && !isExpiredSnapshot(snapshot)) {
      await applyAuthBroadcast({ authenticated: true, snapshot })
    } else {
      await applyAuthBroadcast({ authenticated: false, snapshot: null })
    }
  } catch (error) {
    if ($auth.get().kind !== 'pending') {
      return
    }

    $auth.set({
      error: error instanceof Error ? error.message : String(error),
      kind: 'unauthenticated'
    })
  }
}

// 串行清理账户数据和更新鉴权状态，避免水合读取其他账户的数据。
export async function applyAuthBroadcast(payload: DesktopAuthBroadcast): Promise<void> {
  const apply = async () => {
    const { snapshot } = payload
    const previous = $auth.get()

    const next =
      payload.authenticated && snapshot && snapshot.hasToken && !isExpiredSnapshot(snapshot) ? snapshot : null

    const sessionChanged = previous.kind === 'authenticated' && previous.snapshot.sessionId !== next?.sessionId

    if (sessionChanged) {
      tearDownPrimaryGateway()
    }

    if (previous.kind !== 'authenticated' || previous.snapshot.accountId !== next?.accountId) {
      $auth.set({ kind: 'switching' })
    }

    await setStorageAccount(next?.accountId ?? null, payload.removedAccountId)
    $auth.set(next ? { kind: 'authenticated', snapshot: next } : { kind: 'unauthenticated' })
  }

  const next = broadcastQueue.then(apply, apply)
  broadcastQueue = next.catch(() => {})

  return next
}

export async function activate(payload: DesktopActivatePayload): Promise<void> {
  await window.spiritagent.activate(payload)
}

export async function refreshSession(): Promise<void> {
  await window.spiritagent.refreshSession()
}

export async function expireSession(sessionId: string): Promise<void> {
  await window.spiritagent.logout({ expectedSessionId: sessionId, reason: 'expired' })
}
