'use strict'

const fs = require('node:fs')
const path = require('node:path')
const { execFileSync } = require('node:child_process')

function cleanStaleAppOutDir(appOutDir) {
  if (!appOutDir || typeof appOutDir !== 'string') {
    return false
  }
  if (!fs.existsSync(appOutDir)) {
    return false
  }
  // 半成品目录会阻断 electron-builder 下次暂存；Windows 的杀毒扫描可能暂时占用它。
  fs.rmSync(appOutDir, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 })
  return true
}

exports.default = async function beforePack(context) {
  if (context.electronPlatformName === 'win32') {
    const targets = { 1: 'x86_64-pc-windows-msvc', 3: 'aarch64-pc-windows-msvc' }
    const target = targets[context.arch]
    if (!target) throw new Error(`Unsupported Windows desktop architecture: ${context.arch}`)
    execFileSync(process.execPath, [path.join(__dirname, 'build-desktop-host.cjs'), '--target', target], {
      stdio: 'inherit',
      timeout: 11 * 60_000
    })
    const arch = context.arch === 3 ? 'arm64' : 'x64'
    if (!fs.existsSync(path.join(__dirname, '..', 'build', arch, 'desktop-host.exe')))
      throw new Error('Windows desktop helper is missing')
  }
  const appOutDir = context && context.appOutDir
  try {
    if (cleanStaleAppOutDir(appOutDir)) {
      console.log(`[before-pack] removed stale unpacked dir before staging: ${appOutDir}`)
    }
  } catch (err) {
    console.warn(`[before-pack] could not clean ${appOutDir} (${err.message}); continuing`)
  }
}
