import { systemApi } from '@/shared/platform/desktop'

type LogLevel = 'error' | 'info' | 'warn'

function emit(level: LogLevel, scope: string, args: unknown[]): void {
  void systemApi()?.log?.({ level, scope, args })
}

export const log = {
  warn: (scope: string, ...args: unknown[]): void => emit('warn', scope, args),
  error: (scope: string, ...args: unknown[]): void => emit('error', scope, args),
  info: (scope: string, ...args: unknown[]): void => emit('info', scope, args)
} as const
