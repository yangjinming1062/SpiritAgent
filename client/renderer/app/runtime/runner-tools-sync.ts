import { log } from '@/shared/lib/log'
import type { SpiritAgentGateway } from '@/shared/spiritagent'
import { errorMessage } from '@runtime'

export function createRunnerToolsSync({
  gateway,
  getTools,
  isAlive
}: {
  gateway: Pick<SpiritAgentGateway, 'connectionState' | 'request'>
  getTools: () => Promise<Record<string, unknown>[]>
  isAlive: () => boolean
}) {
  let toolsSyncGeneration = 0
  let toolsConnectionEpoch = 0
  let lastSyncedTools: string | null = null
  let toolsSyncTail: Promise<void> = Promise.resolve()
  const gatewayOpen = (): boolean => gateway.connectionState === 'open'

  // 清单获取不阻塞撤销；发送串行，空清单始终排在已发出的旧清单之后。
  const syncTools = async (readyTools?: Record<string, unknown>[]): Promise<void> => {
    const generation = ++toolsSyncGeneration
    const epoch = toolsConnectionEpoch

    const sameConnection = (): boolean => isAlive() && epoch === toolsConnectionEpoch && gatewayOpen()

    const isCurrent = (): boolean => sameConnection() && generation === toolsSyncGeneration

    try {
      const tools = readyTools ?? (await getTools())

      if (!isCurrent()) {
        return
      }

      const signature = JSON.stringify(tools)

      const task = toolsSyncTail.then(async () => {
        if (!isCurrent() || signature === lastSyncedTools) {
          return
        }

        const hasFileTools = tools.some(tool => tool.name === 'read_file' || tool.name === 'list_directory')

        if (!hasFileTools) {
          log.warn('gateway-boot', 'tools.sync: LLM will lack file tools in this session')
        }

        const res = await gateway.request<{ count: number }>('tools.sync', { tools, skill_scope_version: 1 })

        // 已交付的成功事实不随较新的意图作废；换连接后才丢弃，避免误跳过后续撤销。
        if (sameConnection()) {
          lastSyncedTools = signature
        }

        log.info(
          'gateway-boot',
          `tools.sync: synced ${res.count} runner tools to gateway (hasFileTools=${hasFileTools})`
        )
      })

      toolsSyncTail = task.catch(() => {})
      await task
    } catch (error) {
      log.error('gateway-boot', `tools.sync failed: ${errorMessage(error)}`)
    }
  }

  const invalidateConnection = (): void => {
    toolsSyncGeneration++
    toolsConnectionEpoch++
    lastSyncedTools = null
  }

  return { syncTools, invalidateConnection }
}
