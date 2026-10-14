import { execFile } from 'node:child_process'
import crypto from 'node:crypto'
import fs from 'node:fs'
import fsp from 'node:fs/promises'
import path from 'node:path'
import { Readable } from 'node:stream'
import { pipeline } from 'node:stream/promises'
import { promisify } from 'node:util'

import YAML from 'yaml'

import { errorMessage, sleep } from '@runtime'

import { venvPythonFor } from './venv'

const execFileP = promisify(execFile)

const MANIFEST_FETCH_ATTEMPTS = 3
const MANIFEST_FETCH_BACKOFF_MS = 1500
// 预取与安装两阶段经这两个 Home 下的名字交接。
const SENTINEL_FILE = '.pending-runner-update.json'
const STAGING_DIR = 'runner.staging'

interface RunnerUpdateManifest {
  path: string
  sha512: string
  signature: string
  size?: number
  version: string
}

interface PendingRunnerSentinel {
  attempt_count: number
  last_error?: string
  max_attempts: number
  prepared_at: string
  version: string
  wheel_path: string
  wheel_sha512: string
}

interface RunnerStopResult {
  errors?: string[]
  noop?: boolean
  ok: boolean
}

export interface MinimalRunnerBridge {
  getStatus: () => { phase: string }
  start: (options: { readyTimeoutMs?: number }) => Promise<unknown>
  stop: (options: { reason: string }) => Promise<RunnerStopResult>
}

// 其他入口已在启动、运行或收尾时 start 会拒绝；更新器不再拉起，避免把它记成安装失败。
function bridgeBusy(bridge: MinimalRunnerBridge): boolean {
  const phase = bridge.getStatus().phase

  return phase === 'starting' || phase === 'running' || phase === 'stopping'
}

export interface RunnerUpdaterDeps {
  runtime: {
    spiritagentHome: string
    getRunnerBridge?: () => null | MinimalRunnerBridge
  }
  fetchImpl?: typeof globalThis.fetch
  log?: (level: string, message: string, ...args: unknown[]) => void
}

export class RunnerUpdater {
  private runtime: RunnerUpdaterDeps['runtime']
  private fetchImpl: typeof globalThis.fetch
  private log?: RunnerUpdaterDeps['log']

  constructor({ runtime, fetchImpl = globalThis.fetch, log }: RunnerUpdaterDeps) {
    this.runtime = runtime
    this.fetchImpl = fetchImpl
    this.log = log
  }

  // 阶段 1：桌面安装包就绪后、重启更新前，在当前运行的 Electron 进程内预下载。updateBaseUrl 即桌面更新源（`< 后端 >/api/update`），资产路径直接相对它。
  async prefetchRunnerAssets({
    publicKeyPath,
    updateBaseUrl,
    version
  }: {
    publicKeyPath?: null | string
    updateBaseUrl: string
    version: string
  }): Promise<void> {
    const home = this.runtime.spiritagentHome
    const stagingDir = path.join(home, STAGING_DIR)

    await fsp.rm(stagingDir, { force: true, recursive: true })
    await fsp.mkdir(stagingDir, { recursive: true })

    const manifest = await this.fetchManifest(`${updateBaseUrl}/latest-runner.yml`)

    if (!manifest) {
      throw new Error('manifest fetch failed after retries')
    }

    if (!manifest.path || !manifest.signature || !manifest.version || !/^[A-Fa-f0-9]{128}$/.test(manifest.sha512)) {
      throw new Error('manifest missing required fields')
    }

    // 下载与预取之间可能发布了新版本；Runner 必须与已下载的桌面安装包同版本。
    if (manifest.version !== version) {
      throw new Error(`runner manifest version ${manifest.version} does not match desktop update ${version}`)
    }

    // 文件名参与签名，并保留原名供 uv/pip 解析发行包与版本。
    if (!/^runner\/spirit_agent-[A-Za-z0-9_.+-]+\.whl$/.test(manifest.path)) {
      throw new Error('manifest wheel path is invalid')
    }

    const wheelName = path.posix.basename(manifest.path)

    if (!wheelName.startsWith(`spirit_agent-${version}-`)) {
      throw new Error('manifest wheel version does not match desktop update')
    }

    const manifestSignatureOk = this.verifySignature({
      payload: `${manifest.path}|${manifest.sha512}`,
      publicKeyPath,
      signatureB64: manifest.signature
    })

    if (!manifestSignatureOk) {
      throw new Error('manifest signature verification failed')
    }

    const wheelUrl = `${updateBaseUrl}/${manifest.path}`
    const wheelStagingPath = path.join(stagingDir, wheelName)

    await this.fetchToFile(wheelUrl, wheelStagingPath)

    const wheelHash = (await hashOfFile(wheelStagingPath, 'sha512')).toUpperCase()

    if (wheelHash !== manifest.sha512.toUpperCase()) {
      await fsp.rm(stagingDir, { force: true, recursive: true })
      throw new Error('wheel sha512 mismatch')
    }

    if (manifest.size != null && (await fsp.stat(wheelStagingPath)).size !== manifest.size) {
      await fsp.rm(stagingDir, { force: true, recursive: true })
      throw new Error('wheel size mismatch')
    }

    const sentinel: PendingRunnerSentinel = {
      attempt_count: 0,
      max_attempts: 3,
      prepared_at: new Date().toISOString(),
      version,
      wheel_path: wheelStagingPath,
      wheel_sha512: wheelHash
    }

    await fsp.writeFile(path.join(home, SENTINEL_FILE), JSON.stringify(sentinel, null, 2), 'utf8')
  }

  // 阶段 2：更新后的 Electron 进程启动时完成安装。
  async installPending(appVersion: string): Promise<{ error?: string; noop?: boolean; ok: boolean }> {
    const home = this.runtime.spiritagentHome
    const sentinelPath = path.join(home, SENTINEL_FILE)

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

    // 当前运行版本与暂存版本不一致（下载后普通重启、未应用该更新）：保留暂存，待同版本桌面启动时再装。
    if (sentinel.version !== appVersion) {
      this.log?.('info', `[updater] pending runner ${sentinel.version} kept; running desktop is ${appVersion}`)

      return { noop: true, ok: true }
    }

    if (sentinel.attempt_count >= sentinel.max_attempts) {
      await fsp.rm(sentinelPath, { force: true })
      this.log?.('error', `[updater] max attempts exceeded for ${sentinel.version}`)

      return { error: 'max-attempts-exceeded', ok: false }
    }

    const venvPython = venvPythonFor(home)

    let stopResult: RunnerStopResult | undefined
    let startedNew = false

    const fail = async (reason: string, error?: unknown) => {
      await this.bumpAttempt(sentinel, sentinelPath, reason)
      this.log?.('error', `[updater] install failed: ${reason}`, error)

      return { error: reason, ok: false }
    }

    const stopIfBridged = () => {
      const bridge = this.runtime?.getRunnerBridge?.()

      return bridge ? bridge.stop({ reason: 'update' }) : Promise.resolve(undefined)
    }

    try {
      if (!fs.existsSync(venvPython)) {
        // 无法安装：只记安装失败，不启停 Runner。
        return await fail('venv-missing')
      }

      if ((await hashOfFile(sentinel.wheel_path, 'sha512')).toUpperCase() !== sentinel.wheel_sha512) {
        return await fail('staged-wheel-sha512-mismatch')
      }

      const [stopRes, venvOk] = await Promise.all([stopIfBridged(), this.probeVenvIntegrity(venvPython)])
      stopResult = stopRes

      if (stopRes && !stopRes.ok) {
        return await fail('runner-stop-failed', stopRes.errors)
      }

      if (!venvOk) {
        return await fail(
          'venv-integrity-precheck-failed',
          new Error(
            'Runner venv imports are broken — the desktop auto-update cannot repair this. ' +
              'Re-run the installer (its `uv venv --clear` rebuilds the venv) and retry.'
          )
        )
      }

      // 与 installer 一致：uv 装在 `$SPIRITAGENT_HOME/bin`，venv 由 `uv venv` 创建时不带 pip。
      const uvName = process.platform === 'win32' ? 'uv.exe' : 'uv'
      const managedUv = path.join(home, 'bin', uvName)
      const uvBin = fs.existsSync(managedUv) ? managedUv : uvName

      // 包内入口与实现随同一个 wheel 安装；构建期隔离安装门禁验证完整性。
      try {
        await execFileP(uvBin, ['pip', 'install', '--python', venvPython, '--upgrade', sentinel.wheel_path], {
          maxBuffer: 16 * 1024 * 1024,
          timeout: 300_000
        })
      } catch (err) {
        return await fail('pip-failed', err)
      }

      if (!(await this.probeVenvIntegrity(venvPython, sentinel.version))) {
        return await fail('installed-runner-check-failed')
      }

      const bridge = this.runtime?.getRunnerBridge?.()

      if (bridge && bridgeBusy(bridge)) {
        this.log?.(
          'warn',
          '[updater] runner bridge is already starting, running or stopping; skipping post-install start'
        )
      } else if (bridge) {
        try {
          await bridge.start({
            readyTimeoutMs: 10_000
          })
          startedNew = true
        } catch (err) {
          // 已切到新版本但拉起失败：保留新版本并上报，不回退旧 runner。
          return await fail('start-timeout', err)
        }
      }

      await fsp.rm(sentinelPath, { force: true })
      await fsp.rm(path.join(home, STAGING_DIR), { force: true, recursive: true })

      return { ok: true }
    } catch (err) {
      return await fail('unknown', err)
    } finally {
      const bridge = this.runtime?.getRunnerBridge?.()

      if (stopResult?.ok && !stopResult.noop && !startedNew && bridge && !bridgeBusy(bridge)) {
        try {
          await bridge.start({
            readyTimeoutMs: 8_000
          })
        } catch (err: unknown) {
          this.log?.('error', '[updater] post-update restart failed', err)
        }
      }
    }
  }

  private async probeVenvIntegrity(venvPython: string, expectedVersion?: string): Promise<boolean> {
    try {
      await execFileP(venvPython, ['-I', '-m', 'spirit_agent_runner', '--check'], { timeout: 30_000 })

      if (expectedVersion) {
        const { stdout } = await execFileP(venvPython, ['-I', '-m', 'spirit_agent_runner', '--version'], {
          timeout: 30_000
        })

        if (stdout.trim() !== expectedVersion) {
          throw new Error(`installed runner version ${stdout.trim()} does not match ${expectedVersion}`)
        }
      }

      return true
    } catch (err: unknown) {
      this.log?.('error', '[updater] venv integrity probe failed', err)

      return false
    }
  }

  private async bumpAttempt(sentinel: PendingRunnerSentinel, sentinelPath: string, reason: string): Promise<void> {
    sentinel.attempt_count = (sentinel.attempt_count || 0) + 1
    sentinel.last_error = reason

    try {
      await fsp.writeFile(sentinelPath, JSON.stringify(sentinel, null, 2), 'utf8')
    } catch (err: unknown) {
      this.log?.('error', '[updater] failed to record install attempt', err)
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
    if (!publicKeyPath) {
      return false
    }

    try {
      return crypto.verify(
        'sha512',
        Buffer.from(payload, 'utf8'),
        fs.readFileSync(publicKeyPath),
        Buffer.from(signatureB64, 'base64')
      )
    } catch {
      return false
    }
  }

  // 清单抓取（含 YAML 解析）失败时线性退避重试，末次失败原样抛出。
  private async fetchManifest(url: string): Promise<null | RunnerUpdateManifest> {
    for (let attempt = 1; ; attempt++) {
      try {
        return YAML.parse(await this.fetchText(url))
      } catch (err) {
        if (attempt >= MANIFEST_FETCH_ATTEMPTS) {
          throw err
        }

        await sleep(MANIFEST_FETCH_BACKOFF_MS * attempt)
      }
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
    const signal = AbortSignal.timeout(60_000)
    const res = await this.fetchImpl(url, { redirect: 'follow', signal })

    if (!res.ok) {
      throw new Error(`${res.status} ${res.statusText}`)
    }

    if (!res.body) {
      throw new Error('Response body is empty')
    }

    // pipeline 传递写盘错误并在失败时销毁两端；同一超时覆盖响应体读取。
    await pipeline(Readable.fromWeb(res.body), fs.createWriteStream(dest), { signal })
  }
}

async function hashOfFile(p: string, algorithm: string): Promise<string> {
  const hash = crypto.createHash(algorithm)

  for await (const chunk of fs.createReadStream(p)) {
    hash.update(chunk)
  }

  return hash.digest('hex')
}
