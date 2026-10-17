import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { errorMessage } from '@runtime'

import { assertUserSelectedPath } from './user-selected-paths'

export const DEFAULT_FETCH_TIMEOUT_MS = 15_000
export const ASSET_FETCH_TIMEOUT_MS = 60_000
export const DATA_URL_READ_MAX_BYTES = 16 * 1024 * 1024

function buildCspPolicy(scriptSrc: string): string {
  return [
    "default-src 'self'",
    `script-src ${scriptSrc}`,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob: https:",
    "media-src 'self' data: blob: https:",
    "connect-src 'self' data: blob: ws://127.0.0.1:* ws://localhost:* http://127.0.0.1:* http://localhost:* http: https: ws: wss:",
    "font-src 'self' data:",
    "worker-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'none'"
  ].join('; ')
}

export const DEFAULT_CSP_POLICY = buildCspPolicy("'self'")

// Dev-only：Vite 的 React Fast Refresh preamble 以内联脚本注入 HTML，DEFAULT_CSP_POLICY 不允许 inline 会导致白屏（HMR 重试还会烧 CPU）；HMR websocket 已被 connect-src 通配覆盖。生产仍用 DEFAULT_CSP_POLICY 锁紧。
export const DEV_CSP_POLICY = buildCspPolicy("'self' 'unsafe-inline' 'unsafe-eval'")

const SAFE_ENV_SUFFIXES: Set<string> = new Set(['dist', 'example', 'sample', 'template'])
const SENSITIVE_EXTENSIONS: Set<string> = new Set(['.kdbx', '.p12', '.pem', '.pfx'])

export interface SafeStorageApi {
  decryptString?: (encrypted: Buffer) => string
  encryptString: (plainText: string) => Buffer
  isEncryptionAvailable: () => boolean
}

function sensitiveFileBlockReason(filePath: string): null | string {
  const normalized = String(filePath || '')
    .replace(/\\/g, '/')
    .toLowerCase()

  const basename = path.basename(normalized)
  const ext = path.extname(basename)

  if (!basename) {
    return null
  }

  if (normalized.includes('/.ssh/')) {
    return 'SSH key/config files are blocked.'
  }

  if (normalized.includes('/.gnupg/')) {
    return 'GPG key material is blocked.'
  }

  if (normalized.endsWith('/.aws/credentials')) {
    return 'AWS credential files are blocked.'
  }

  if (basename === '.env') {
    return '.env files are blocked because they commonly contain secrets.'
  }

  if (basename.startsWith('.env.')) {
    const suffix = basename.slice('.env.'.length)

    if (!SAFE_ENV_SUFFIXES.has(suffix)) {
      return `${basename} is blocked because it appears to contain environment secrets.`
    }
  }

  if (/^id_(rsa|dsa|ecdsa|ed25519)(?:\..+)?$/.test(basename) && !basename.endsWith('.pub')) {
    return 'SSH private key files are blocked.'
  }

  if (SENSITIVE_EXTENSIONS.has(ext)) {
    return `${ext} key/certificate files are blocked.`
  }

  if (basename === '.npmrc' || basename === '.netrc' || basename === '.pypirc') {
    return `${basename} is blocked because it may include auth credentials.`
  }

  return null
}

function resolveRequestedFilePath(filePath: string, purpose = 'File read'): string {
  const raw = String(filePath || '').trim()

  if (!raw) {
    throw new Error(`${purpose} failed: file path is required.`)
  }

  if (raw.includes('\0')) {
    throw new Error(`${purpose} failed: file path is invalid.`)
  }

  if (/^file:/i.test(raw)) {
    try {
      return fileURLToPath(raw)
    } catch {
      throw new Error(`${purpose} failed: file URL is invalid.`)
    }
  }

  return path.resolve(raw)
}

interface ResolveReadableFileOptions {
  maxBytes?: null | number
  purpose?: string
}

export async function resolveReadableFileForIpc(
  filePath: string,
  options: ResolveReadableFileOptions = {}
): Promise<{ resolvedPath: string; stat: fs.Stats }> {
  const purpose = String(options.purpose || 'File read')
  const resolvedPath = resolveRequestedFilePath(filePath, purpose)

  const blockReason = sensitiveFileBlockReason(resolvedPath)

  if (blockReason) {
    throw new Error(`${purpose} blocked for sensitive file: ${blockReason}`)
  }

  let stat: fs.Stats

  try {
    stat = await fs.promises.stat(resolvedPath)
  } catch (error: unknown) {
    const code =
      typeof error === 'object' && error !== null && 'code' in error && typeof error.code === 'string' ? error.code : ''

    if (code === 'ENOENT' || code === 'ENOTDIR') {
      throw new Error(`${purpose} failed: file does not exist.`)
    }

    throw new Error(`${purpose} failed: ${errorMessage(error)}`)
  }

  if (stat.isDirectory()) {
    throw new Error(`${purpose} failed: path points to a directory.`)
  }

  if (!stat.isFile()) {
    throw new Error(`${purpose} failed: only regular files can be read.`)
  }

  const realPath = await fs.promises.realpath(resolvedPath)

  if (realPath !== resolvedPath) {
    const realBlockReason = sensitiveFileBlockReason(realPath)

    if (realBlockReason) {
      throw new Error(`${purpose} blocked for sensitive file (symlink target): ${realBlockReason}`)
    }
  }

  const { maxBytes } = options

  if (typeof maxBytes === 'number' && maxBytes > 0 && stat.size > maxBytes) {
    throw new Error(`${purpose} failed: file is too large (${stat.size} bytes; limit ${maxBytes} bytes).`)
  }

  try {
    await fs.promises.access(resolvedPath, fs.constants.R_OK)
  } catch {
    throw new Error(`${purpose} failed: file is not readable.`)
  }

  return { resolvedPath, stat }
}

/** 读取渲染层经选择器或拖拽登记过的文件：先核对白名单，再做敏感路径、类型、大小与可读性校验。 */
export async function readUserSelectedFile(
  filePath: string,
  options: { maxBytes: number; purpose: string }
): Promise<{ data: Buffer; resolvedPath: string }> {
  assertUserSelectedPath(filePath, options.purpose)

  const { resolvedPath } = await resolveReadableFileForIpc(filePath, options)

  return { data: await fs.promises.readFile(resolvedPath), resolvedPath }
}
