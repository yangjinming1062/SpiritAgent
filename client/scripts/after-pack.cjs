'use strict'

const path = require('node:path')
const { readWindowsExecutableArch } = require('./build-desktop-host.cjs')
const { prepareWindowsDpiManifest } = require('./windows-dpi-manifest.cjs')

exports.default = async function afterPack(context) {
  if (context.electronPlatformName !== 'win32') return
  const name = context.packager.platformSpecificBuildOptions.executableName || context.packager.appInfo.productFilename
  const executable = path.join(context.appOutDir, `${name}.exe`)
  const arch = { 1: 'x64', 3: 'arm64' }[context.arch]
  if (!arch || readWindowsExecutableArch(executable) !== arch)
    throw new Error('Windows Electron architecture does not match the desktop helper target')
  prepareWindowsDpiManifest(executable)
}
