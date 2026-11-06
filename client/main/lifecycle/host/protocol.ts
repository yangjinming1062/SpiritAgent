import type { RunningApplicationWindow } from '../../shared/desktop-applications'
import type { DesktopHostWindow } from '../../shared/desktop-host'

export interface PendingRequest {
  resolve: (value: unknown) => void
  reject: (error: Error) => void
  timer: ReturnType<typeof setTimeout>
}

export type ExternalFocusAuthorizationStatus = 'accepted' | 'deferred' | 'stale' | 'completed'

export function isRunningWindow(raw: unknown): raw is RunningApplicationWindow {
  if (!raw || typeof raw !== 'object') {
    return false
  }

  const item = raw as Partial<RunningApplicationWindow>

  return (
    typeof item.id === 'string' &&
    item.id.length > 0 &&
    item.id.length <= 256 &&
    typeof item.appId === 'string' &&
    item.appId.length > 0 &&
    (item.target === null || typeof item.target === 'string') &&
    typeof item.name === 'string' &&
    typeof item.title === 'string' &&
    typeof item.minimized === 'boolean' &&
    typeof item.onDesktopDisplay === 'boolean' &&
    Number.isSafeInteger(item.lastActive) &&
    typeof item.lastActive === 'number' &&
    item.lastActive >= 0
  )
}

export function parseExternalActivation(value: unknown): string {
  const activationId =
    value && typeof value === 'object' && !Array.isArray(value)
      ? (value as { activation_id?: unknown }).activation_id
      : undefined

  if (typeof activationId !== 'string' || !activationId || activationId.length > 256) {
    throw new Error('Desktop helper returned an invalid external activation identity')
  }

  return activationId
}

export function parseExternalFocusAuthorization(value: unknown): ExternalFocusAuthorizationStatus {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('Desktop helper returned an invalid foreground authorization status')
  }

  const status = (value as { status?: unknown }).status

  if (status !== 'accepted' && status !== 'deferred' && status !== 'stale' && status !== 'completed') {
    throw new Error('Desktop helper returned an invalid foreground authorization status')
  }

  return status
}

export function parseMessage(line: string): Record<string, unknown> {
  const value: unknown = JSON.parse(line)

  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('Desktop helper returned an invalid message')
  }

  return value as Record<string, unknown>
}

export function encodeHandle(buffer: Buffer): string {
  if (buffer.length !== 4 && buffer.length !== 8) {
    throw new Error('Desktop native window handle has an unsupported size')
  }

  const handle = buffer.length === 8 ? buffer.readBigUInt64LE() : BigInt(buffer.readUInt32LE())

  if (handle === 0n) {
    throw new Error('Desktop native window handle is invalid')
  }

  return handle.toString(16)
}

export function encodeWindow(window: DesktopHostWindow): {
  handle: string
  role: DesktopHostWindow['role']
  bounds: DesktopHostWindow['bounds']
} {
  const handle = encodeHandle(window.handle)
  const bounds = window.bounds

  if (!isPhysicalRect(bounds)) {
    throw new Error('Desktop native window handle or physical bounds are invalid')
  }

  return { bounds: { ...bounds }, handle, role: window.role }
}

export function isPhysicalRect(value: unknown): value is DesktopHostWindow['bounds'] {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return false
  }

  const bounds = value as Record<string, unknown>

  return (
    ['x', 'y', 'width', 'height'].every(key => typeof bounds[key] === 'number' && Number.isSafeInteger(bounds[key])) &&
    Number(bounds.width) > 0 &&
    Number(bounds.height) > 0 &&
    Number(bounds.width) <= 65_536 &&
    Number(bounds.height) <= 65_536 &&
    Math.abs(Number(bounds.x)) <= 1_000_000 &&
    Math.abs(Number(bounds.y)) <= 1_000_000
  )
}
