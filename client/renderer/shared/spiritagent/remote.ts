import { requestResource } from '@/shared/lib/resource-request'
import type { RemoteDevice, RemotePairing } from '@protocol'

export function createRemotePairing(): Promise<RemotePairing> {
  return requestResource('remote.create_pairing')
}

export function getRemotePairing(id: string): Promise<RemotePairing> {
  return requestResource('remote.pairing_status', { path: { pairing_id: id } })
}

export async function cancelRemotePairing(id: string): Promise<void> {
  await requestResource('remote.cancel_pairing', { path: { pairing_id: id } })
}

export function listRemoteDevices(): Promise<{ items: RemoteDevice[] }> {
  return requestResource('remote.list_remote_devices')
}

export async function revokeRemoteDevice(id: string): Promise<void> {
  await requestResource('remote.revoke_remote_device', { path: { device_id: id } })
}

export async function revokeAllRemoteDevices(): Promise<void> {
  await requestResource('remote.revoke_all_remote_devices')
}
