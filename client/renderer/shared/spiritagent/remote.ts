import { $auth } from '@/shared/store/auth'
import type { SpiritAgentApiRequest } from '@ipc/contracts'
import type { RemoteDevice, RemotePairing } from '@protocol'

function remoteApi<T>(request: SpiritAgentApiRequest): Promise<T> {
  const auth = $auth.get()

  if (auth.kind !== 'authenticated') {
    return Promise.reject(new Error('Account is not signed in'))
  }

  return window.spiritagent.api<T>({ ...request, authSessionId: auth.snapshot.sessionId })
}

export function createRemotePairing(): Promise<RemotePairing> {
  return remoteApi({ method: 'POST', path: '/api/remote/pairings' })
}

export function getRemotePairing(id: number): Promise<RemotePairing> {
  return remoteApi({ path: `/api/remote/pairings/${id}` })
}

export function cancelRemotePairing(id: number): Promise<void> {
  return remoteApi({ method: 'DELETE', path: `/api/remote/pairings/${id}` })
}

export function listRemoteDevices(): Promise<{ items: RemoteDevice[] }> {
  return remoteApi({ path: '/api/remote/devices' })
}

export function revokeRemoteDevice(id: number): Promise<void> {
  return remoteApi({ method: 'DELETE', path: `/api/remote/devices/${id}` })
}

export function revokeAllRemoteDevices(): Promise<void> {
  return remoteApi({ method: 'DELETE', path: '/api/remote/devices' })
}
