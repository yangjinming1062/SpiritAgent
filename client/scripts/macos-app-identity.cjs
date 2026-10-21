'use strict'

const { createHash } = require('node:crypto')
const { spawnSync } = require('node:child_process')
const fs = require('node:fs')

// 对新展开的 Electron 主执行文件调用一次，随后交给 builder 签名。
// 不同应用复用 stock LC_UUID 会混淆 macOS 本地网络授权；不改 Framework 的调试标识。
function prepareMacosAppIdentity(executable, appId) {
  if (typeof appId !== 'string' || !appId.trim()) throw new Error('Missing macOS application identifier')
  const image = fs.readFileSync(executable)
  if (image.length < 32 || image.readUInt32LE(0) !== 0xfeedfacf)
    throw new Error('Expected a thin 64-bit macOS main executable')
  const commandCount = image.readUInt32LE(16)
  const end = 32 + image.readUInt32LE(20)
  if (end > image.length || !commandCount || commandCount > (end - 32) / 8)
    throw new Error('Invalid macOS executable load commands')

  let offset = 32
  let uuidOffset = null
  for (let index = 0; index < commandCount; index++) {
    if (offset + 8 > end) throw new Error('Truncated macOS executable load command')
    const command = image.readUInt32LE(offset)
    const size = image.readUInt32LE(offset + 4)
    if (size < 8 || size % 8 !== 0 || offset + size > end) throw new Error('Invalid macOS executable load command size')
    if (command === 0x1b) {
      if (size !== 24 || uuidOffset !== null) throw new Error('Invalid or duplicate macOS executable UUID')
      uuidOffset = offset + 8
    }
    offset += size
  }
  if (offset !== end || uuidOffset === null) throw new Error('Missing macOS executable UUID')

  const uuid = createHash('sha256')
    .update('spiritagent-macos-main-uuid-v1\0')
    .update(appId)
    .update('\0')
    .update(image.subarray(uuidOffset, uuidOffset + 16))
    .digest()
    .subarray(0, 16)
  uuid[6] = (uuid[6] & 0x0f) | 0x80
  uuid[8] = (uuid[8] & 0x3f) | 0x80
  uuid.copy(image, uuidOffset)
  fs.writeFileSync(executable, image)

  // 无证书的本地打包会跳过 builder 签名，也必须修复被 UUID 改动破坏的 stock 签名。
  const signed = spawnSync('codesign', ['--force', '--sign', '-', '--identifier', appId, executable], {
    encoding: 'utf8',
    timeout: 30_000
  })
  if (signed.error || signed.status !== 0)
    throw new Error(`Signing macOS application identity failed: ${signed.error?.message || signed.stderr}`)
}

module.exports = { prepareMacosAppIdentity }
