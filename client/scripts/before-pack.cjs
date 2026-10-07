'use strict'

const fs = require('node:fs')
const path = require('node:path')
const { checkDistBuilt } = require('./assert-dist-built.cjs')
const { buildDesktopHost, targets, windowsArchFromBuilder } = require('./build-desktop-host.cjs')

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
  const result = checkDistBuilt(path.join(context.packager.projectDir, 'dist'))
  if (!result.ok) throw new Error(`[before-pack] ${result.error}; run pnpm build before packaging`)
  if (context.electronPlatformName === 'win32') {
    const arch = windowsArchFromBuilder(context.arch)
    buildDesktopHost(targets[arch])
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
