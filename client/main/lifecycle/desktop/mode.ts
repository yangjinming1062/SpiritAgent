import { existsSync, unlinkSync } from 'node:fs'
import path from 'node:path'

import { errorMessage } from '@runtime'

import { atomicWriteFile } from '../../shared/utils'

export interface RecoveryResult {
  suppressed: boolean
  failureReason: string | null
}

export interface RecoveryJournal {
  journalPath: string
  hasJournal: () => boolean
  markMacosActive: () => Promise<void>
  markMainInterruption: () => Promise<void>
  clearMacosActive: () => void
  clearMainInterruption: () => void
  recover: (recoverDesktop: () => Promise<boolean>) => Promise<RecoveryResult>
}

/** 恢复标记只描述「上一次运行是否被异常中断」，不参与模式选择。 */
export function createRecoveryJournal(userData: string, log: (message: string) => void): RecoveryJournal {
  const journalFile = path.join(userData, 'desktop-shell-recovery.json')
  const interruptedFile = `${journalFile}.interrupted`
  const mainInterruptedFile = `${journalFile}.main-interrupted`
  const macosActiveFile = path.join(userData, 'desktop-macos-active.json')

  const remove = (file: string, label: string): void => {
    if (!existsSync(file)) {
      return
    }

    try {
      unlinkSync(file)
    } catch (error) {
      log(`[desktop] ${label}: ${errorMessage(error)}`)
    }
  }

  const interrupted = (): boolean =>
    existsSync(interruptedFile) || existsSync(mainInterruptedFile) || (process.platform === 'darwin' && existsSync(macosActiveFile))

  return {
    journalPath: journalFile,
    hasJournal: () => existsSync(journalFile),
    markMacosActive: async () => {
      await atomicWriteFile(macosActiveFile, JSON.stringify({ pid: process.pid }))
    },
    markMainInterruption: async () => {
      try {
        await atomicWriteFile(mainInterruptedFile, 'main_failure')
      } catch (error) {
        const message = `[desktop] interruption marker write: ${errorMessage(error)}`

        try {
          log(message)
        } catch {
          console.warn(message)
        }
      }
    },
    clearMacosActive: () => remove(macosActiveFile, 'macOS active marker cleanup'),
    clearMainInterruption: () => remove(mainInterruptedFile, 'main interruption marker cleanup'),
    recover: async recoverDesktop => {
      let interruptedNow = interrupted()
      let suppressed = interruptedNow
      let failureReason: string | null = null

      if (process.platform === 'win32' && existsSync(journalFile)) {
        try {
          suppressed = (await recoverDesktop()) || suppressed
        } catch (error) {
          suppressed = true
          failureReason = `系统桌面恢复失败：${errorMessage(error)}`
          log(`[desktop] startup recovery: ${errorMessage(error)}`)
        }

        if (suppressed && !failureReason) {
          failureReason = '上次桌面异常退出，已恢复系统桌面。请重新选择桌面模式。'
        }
      }

      // 残留 guardian 在上个主进程退出后才写 .interrupted，且与 recover() 争用同一恢复锁；首次检查可能早于其写入，recover 后须复检。
      interruptedNow ||= existsSync(interruptedFile) || existsSync(mainInterruptedFile)
      suppressed ||= interruptedNow

      if (interruptedNow && !failureReason) {
        failureReason = '上次桌面异常中断，已保留窗口模式。请重新选择桌面模式。'
      }

      if (interruptedNow && !existsSync(journalFile)) {
        remove(interruptedFile, 'interruption marker cleanup')
        remove(mainInterruptedFile, 'interruption marker cleanup')

        if (process.platform === 'darwin') {
          remove(macosActiveFile, 'interruption marker cleanup')
        }
      }

      return { suppressed, failureReason }
    }
  }
}
