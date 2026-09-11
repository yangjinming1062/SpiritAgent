import { execFile } from 'node:child_process'
import crypto from 'node:crypto'
import fs from 'node:fs'
import fsp from 'node:fs/promises'
import path from 'node:path'
import { promisify } from 'node:util'

import { sleep } from '@runtime'
import YAML from 'yaml'

import { errorMessage } from '../shared/utils'

import type { BackendSessionLike } from './reverse-rpc'
import { venvPythonFor } from './venv'

const execFileP = promisify(execFile)

interface RunnerUpdateManifest {
  path: string
  runner: { server_py_sha256: string }
  sha512: string
  signature: string
  size?: number
}

interface PendingRunnerSentinel {
  attempt_count: number
  last_error?: string
  max_attempts: number
  prepared_at: string
  server_py_path: string
  version: string
  wheel_path: string
}

export interface MinimalRunnerBridge {
  start: (options: { backendSession?: BackendSessionLike | null; readyTimeoutMs?: number }) => Promise<unknown>
  stop: (options: { reason: string }) => Promise<unknown>
}

export interface RunnerUpdaterDeps {
  bridgeDeps: {
    spiritagentHome: string
    ensureBackendSession?: () => BackendSessionLike | null | undefined
    runnerBridge?: null | MinimalRunnerBridge
  }
  fetchImpl?: typeof globalThis.fetch
  log?: (level: string, message: string, ...args: unknown[]) => void
}

export class RunnerUpdater {
  private bridgeDeps: RunnerUpdaterDeps['bridgeDeps']
  private fetchImpl: typeof globalThis.fetch
  private log?: RunnerUpdaterDeps['log']

  constructor({ bridgeDeps, fetchImpl = globalThis.fetch, log }: RunnerUpdaterDeps) {
    this.bridgeDeps = bridgeDeps
    this.fetchImpl = fetchImpl
    this.log = log
  }

  // 阶段 1：在旧版 Electron 进程内预下载。
  async prefetchRunnerAssets({
    publicKeyPath,
    updateBaseUrl,
    version
  }: {
    publicKeyPath?: null | string
    updateBaseUrl: string
    version: string
  }): Promise<void> {
    const home = this.bridgeDeps.spiritagentHome
    const stagingDir = path.join(home, 'runner.staging')

    await fsp.rm(stagingDir, { force: true, recursive: true })
    await fsp.mkdir(stagingDir, { recursive: true })

    const MANIFEST_FETCH_ATTEMPTS = 3
    const MANIFEST_FETCH_BACKOFF_MS = 1500
    let manifest: null | RunnerUpdateManifest = null
    let primaryErr: unknown = null

    for (let attempt = 1; attempt <= MANIFEST_FETCH_ATTEMPTS; attempt++) {
      try {
        const text = await this.fetchText(`${updateBaseUrl}/api/update/latest-runner.yml`)
        manifest = YAML.parse(text)
        primaryErr = null

        break
      } catch (err) {
        primaryErr = err

        if (attempt < MANIFEST_FETCH_ATTEMPTS) {
          await sleep(MANIFEST_FETCH_BACKOFF_MS * attempt)
        }
      }
    }

    if (!manifest) {
      throw primaryErr ?? new Error('manifest fetch failed after retries')
    }

    if (!manifest.path || !manifest.signature || !manifest.runner) {
      throw new Error('manifest missing required fields')
    }

    const manifestSignatureOk = this.verifySignature({
      payload: `${manifest.path}|${manifest.sha512}`,
      publicKeyPath,
      signatureB64: manifest.signature
    })

    if (!manifestSignatureOk) {
      throw new Error('manifest signature verification failed')
    }

    const wheelUrl = `${updateBaseUrl}/api/update/${manifest.path}`
    const wheelStagingPath = path.join(stagingDir, 'wheel.whl')
    const serverPyUrlFinal = `${updateBaseUrl}/api/update/runner/server.py`
    const serverPyStagingPath = path.join(stagingDir, 'server.py')

    await Promise.all([
      this.fetchToFile(wheelUrl, wheelStagingPath),
      this.fetchToFile(serverPyUrlFinal, serverPyStagingPath)
    ])

    const wheelHash = (await hashOfFile(wheelStagingPath, 'sha512')).toUpperCase()

    if (wheelHash !== manifest.sha512) {
      await fsp.rm(stagingDir, { force: true, recursive: true })
      throw new Error('wheel sha512 mismatch')
    }

    const serverPyHash = await hashOfFile(serverPyStagingPath, 'sha256')

    if (serverPyHash !== manifest.runner.server_py_sha256) {
      await fsp.rm(stagingDir, { force: true, recursive: true })
      throw new Error('server.py sha256 mismatch')
    }

    const sentinel = {
      attempt_count: 0,
      max_attempts: 3,
      prepared_at: new Date().toISOString(),
      server_py_path: serverPyStagingPath,
      version,
      wheel_path: wheelStagingPath
    }

    const sentinelPath = path.join(home, '.pending-runner-update.json')
    await fsp.writeFile(sentinelPath, JSON.stringify(sentinel, null, 2), 'utf8')
  }

  // 阶段 2：在新版 Electron 进程内完成安装。
  async installPending(): Promise<{ error?: string; noop?: boolean; ok: boolean }> {
    const home = this.bridgeDeps.spiritagentHome
    const sentinelPath = path.join(home, '.pending-runner-update.json')

    if (!fs.existsSync(sentinelPath)) {
      return { noop: true, ok: true }
    }

    let sentinel: PendingRunnerSentinel

    try {
      sentinel = JSON.parse(await fsp.readFile(sentinelPath, 'utf8')) as PendingRunnerSentinel
    } catch (err: unknown) {
      const msg = errorMessage(err)
      this.log?.('error', '[updater] sentinel unreadable', msg)

      return { error: 'sentinel unreadable', ok: false }
    }

    if (sentinel.attempt_count >= sentinel.max_attempts) {
      await fsp.rm(sentinelPath, { force: true })
      this.log?.('error', `[updater] max attempts exceeded for ${sentinel.version}`)

      return { error: 'max-attempts-exceeded', ok: false }
    }

    const venvPython = venvPythonFor(home)

    let stopResult: unknown
    let startedNew = false

    const fail = async (reason: string, error?: unknown) => {
      await this.bumpAttempt(sentinel, sentinelPath, reason)
      this.log?.('error', `[updater] install failed: ${reason}`, error)

      return { error: reason, ok: false }
    }

    const stopIfBridged = () =>
      this.bridgeDeps?.runnerBridge
        ? this.bridgeDeps.runnerBridge.stop({ reason: 'update' })
        : Promise.resolve(undefined)

    try {
      if (!fs.existsSync(venvPython)) {
        await stopIfBridged()

        return await fail('venv-missing')
      }

      const [stopRes, venvOk] = await Promise.all([stopIfBridged(), this.probeVenvIntegrity(venvPython)])
      stopResult = stopRes

      if (!venvOk) {
        return await fail(
          'venv-integrity-precheck-failed',
          new Error(
            'Runner venv imports are broken — the desktop auto-update cannot repair this. ' +
              'Re-run the installer (its `uv venv --clear` rebuilds the venv) and retry.'
          )
        )
      }

      let rollbackMarker: string | null = null

      try {
        const { stdout } = await execFileP(venvPython, ['-m', 'pip', 'show', 'spiritagent-agent'], {
          maxBuffer: 1 * 1024 * 1024,
          timeout: 30_000
        })

        const m = /Name:\s*(\S+)[\s\S]+?Version:\s*(\S+)/.exec(stdout)

        if (m) {
          rollbackMarker = `${m[1]}==${m[2]}`
        }
      } catch (err) {
        this.log?.('debug', '[updater] no pre-existing wheel to snapshot', err)
      }

      try {
        await execFileP(venvPython, ['-m', 'pip', 'install', '--upgrade', sentinel.wheel_path], {
          maxBuffer: 16 * 1024 * 1024,
          timeout: 300_000
        })
      } catch (err) {
        await this.tryRollbackPip(venvPython, rollbackMarker, 'pip-failed')

        return await fail('pip-failed', err)
      }

      const serverPyDest = path.join(home, 'runner', 'server.py')
      await fsp.copyFile(sentinel.server_py_path, serverPyDest)

      try {
        await execFileP(
          venvPython,
          ['-c', 'import spiritagent_agent, importlib.util as u; assert u.find_spec("server") is not None'],
          { cwd: path.join(home, 'runner'), timeout: 30_000 }
        )
      } catch (err) {
        await this.tryRollbackPip(venvPython, rollbackMarker, 'smoke-test-failed')

        return await fail('smoke-test-failed', err)
      }

      if (this.bridgeDeps?.runnerBridge) {
        try {
          await this.bridgeDeps.runnerBridge.start({
            backendSession: this.bridgeDeps.ensureBackendSession?.(),
            readyTimeoutMs: 10_000
          })
          startedNew = true
        } catch (err) {
          await this.tryRollbackPip(venvPython, rollbackMarker, 'start-timeout')

          return await fail('start-timeout', err)
        }
      }

      await fsp.rm(sentinelPath, { force: true })
      await fsp.rm(path.join(home, 'runner.staging'), { force: true, recursive: true })

      return { ok: true }
    } catch (err) {
      return await fail('unknown', err)
    } finally {
      if (stopResult && !startedNew && this.bridgeDeps?.runnerBridge) {
        try {
          await this.bridgeDeps.runnerBridge.start({
            backendSession: this.bridgeDeps.ensureBackendSession?.(),
            readyTimeoutMs: 8_000
          })
        } catch (err: unknown) {
          this.log?.('error', '[updater] post-update restart failed', err)
        }
      }
    }
  }

  // 三个失败分支（pip-failed / smoke-test-failed / start-timeout）共用同一段回滚逻辑——
  // 回退到升级前的 wheel 版本，仅在有 rollbackMarker 时执行。
  private async tryRollbackPip(
    venvPython: string,
    rollbackMarker: string | null,
    reason: 'pip-failed' | 'smoke-test-failed' | 'start-timeout'
  ): Promise<void> {
    if (!rollbackMarker) {
      return
    }

    try {
      await execFileP(venvPython, ['-m', 'pip', 'install', '--upgrade', rollbackMarker], {
        maxBuffer: 16 * 1024 * 1024,
        timeout: 300_000
      })
      this.log?.('info', `[updater] rolled back to ${rollbackMarker} after ${reason} failure`)
    } catch (rollbackErr) {
      this.log?.('error', `[updater] rollback after ${reason} failure also failed`, rollbackErr)
    }
  }

  private async probeVenvIntegrity(venvPython: string): Promise<boolean> {
    try {
      await execFileP(
        venvPython,
        [
          '-c',
          'from typing_extensions import Sentinel; from annotated_types import BaseMetadata; from mcp.types import BaseModel'
        ],
        { timeout: 30_000 }
      )

      return true
    } catch {
      return false
    }
  }

  private async bumpAttempt(sentinel: PendingRunnerSentinel, sentinelPath: string, reason: string): Promise<void> {
    sentinel.attempt_count = (sentinel.attempt_count || 0) + 1
    sentinel.last_error = reason

    try {
      await fsp.writeFile(sentinelPath, JSON.stringify(sentinel, null, 2), 'utf8')
    } catch {
      // 尽力而为
    }
  }

  private verifySignature({
    payload,
    publicKeyPath,
    signatureB64
  }: {
    payload: string
    publicKeyPath?: null | string
    signatureB64: string
  }): boolean {
    if (!publicKeyPath || !fs.existsSync(publicKeyPath)) {
      return false
    }

    try {
      const pubKey = fs.readFileSync(publicKeyPath)
      const verifier = crypto.createVerify('SHA512')
      verifier.update(payload, 'utf8')
      verifier.end()

      return verifier.verify(pubKey, Buffer.from(signatureB64, 'base64'))
    } catch {
      return false
    }
  }

  private async fetchText(url: string): Promise<string> {
    const res = await this.fetchImpl(url, { redirect: 'follow', signal: AbortSignal.timeout(30_000) })

    if (!res.ok) {
      throw new Error(`${res.status} ${res.statusText}`)
    }

    return res.text()
  }

  private async fetchToFile(url: string, dest: string): Promise<void> {
    const res = await this.fetchImpl(url, { redirect: 'follow', signal: AbortSignal.timeout(60_000) })

    if (!res.ok) {
      throw new Error(`${res.status} ${res.statusText}`)
    }

    const file = fs.createWriteStream(dest)

    try {
      const body = res.body

      if (!body) {
        throw new Error('Response body is empty')
      }

      for await (const chunk of body) {
        const ok = file.write(chunk)

        if (!ok) {
          await new Promise<void>(r => file.once('drain', () => r()))
        }
      }
    } finally {
      await new Promise<void>((resolve, reject) => {
        file.end((err?: unknown) => (err ? reject(new Error(String(err))) : resolve()))
      })
    }
  }
}

async function hashOfFile(p: string, algorithm: string): Promise<string> {
  const h = crypto.createHash(algorithm)
  await new Promise<void>((resolve, reject) => {
    fs.createReadStream(p)
      .on('data', c => h.update(c))
      .on('end', () => resolve())
      .on('error', reject)
  })

  return h.digest('hex')
}
