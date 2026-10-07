'use strict'

const path = require('node:path')
const { readWindowsExecutableArch, windowsArchFromBuilder } = require('./build-desktop-host.cjs')
const { prepareWindowsDpiManifest } = require('./windows-dpi-manifest.cjs')

exports.default = async function afterPack(context) {
  if (context.electronPlatformName !== 'win32') return
  const name = context.packager.platformSpecificBuildOptions.executableName || context.packager.appInfo.productFilename
  const executable = path.join(context.appOutDir, `${name}.exe`)
  const arch = windowsArchFromBuilder(context.arch)
  if (readWindowsExecutableArch(executable) !== arch)
    throw new Error('Windows Electron architecture does not match the desktop helper target')
  prepareWindowsDpiManifest(executable)
}
