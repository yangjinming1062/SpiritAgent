'use strict'

const path = require('node:path')
const fs = require('node:fs')
const { assertStaticRuntime, readWindowsExecutableArch, windowsArchFromBuilder } = require('./build-desktop-host.cjs')
const { prepareWindowsDpiManifest } = require('./windows-dpi-manifest.cjs')

exports.default = async function afterPack(context) {
  if (context.electronPlatformName !== 'win32') return
  const name = context.packager.platformSpecificBuildOptions.executableName || context.packager.appInfo.productFilename
  const executable = path.join(context.appOutDir, `${name}.exe`)
  const arch = windowsArchFromBuilder(context.arch)
  if (readWindowsExecutableArch(executable) !== arch)
    throw new Error('Windows Electron architecture does not match the desktop helper target')
  const resources = path.join(context.appOutDir, 'resources')
  const helper = path.join(resources, 'desktop-host.exe')
  const manifest = JSON.parse(fs.readFileSync(path.join(resources, 'desktop-tray-style.json'), 'utf8'))
  if (
    manifest.version !== 1 ||
    typeof manifest.file !== 'string' ||
    !/^desktop-tray-style-[a-f0-9]{32}\.dll$/.test(manifest.file)
  )
    throw new Error('Windows desktop tray style manifest is invalid')
  for (const component of [helper, path.join(resources, manifest.file)]) {
    if (readWindowsExecutableArch(component) !== arch)
      throw new Error('Packaged Windows desktop component architecture does not match Electron')
    assertStaticRuntime(component)
  }
  prepareWindowsDpiManifest(executable)
}
