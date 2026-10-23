import type { VisualJobCommand, VisualJobState } from './protocol'

/** 两端共用工作项的状态操作；具体失败原因仍由服务端校验。 */
export function visualJobCommands(state: VisualJobState): VisualJobCommand[] {
  switch (state) {
    case 'queued':
    case 'running':
      return ['pause', 'cancel']
    case 'paused':
      return ['resume', 'cancel']
    case 'failed':
      return ['retry', 'cancel']
    case 'unknown':
      return ['cancel']
    case 'succeeded':
    case 'cancelled':
      return []
  }
}
