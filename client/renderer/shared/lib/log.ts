import { systemApi } from '@/shared/platform/desktop'

type LogLevel = 'error' | 'info' | 'warn'

function emit(level: LogLevel, scope: string, args: unknown[]): void {
  // 错误边界也会调用日志；preload 加载失败时不能再次抛错。
  void systemApi()?.log({ level, scope, args })
}

export const log = {
  warn: (scope: string, ...args: unknown[]): void => emit('warn', scope, args),
  error: (scope: string, ...args: unknown[]): void => emit('error', scope, args),
  info: (scope: string, ...args: unknown[]): void => emit('info', scope, args)
} as const
