'use strict'

const { spawnSync } = require('node:child_process')
const path = require('node:path')

try {
  const script = { win32: 'build-desktop-host.cjs', darwin: 'build-desktop-macos.cjs' }[process.platform]
  if (!script) throw new Error(`Desktop mode is unavailable on ${process.platform}`)
  const result = spawnSync(process.execPath, [path.join(__dirname, script), ...process.argv.slice(2)], {
    stdio: 'inherit',
    timeout: 20 * 60_000
  })
  if (result.error) throw result.error
  process.exitCode = result.status ?? 1
} catch (error) {
  console.error(`[build-native] ${error.message}`)
  process.exitCode = 1
}
