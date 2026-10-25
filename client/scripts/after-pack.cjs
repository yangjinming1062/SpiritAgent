'use strict'

const path = require('node:path')
const fs = require('node:fs')
const { windowsArchFromBuilder } = require('./build-desktop-host.cjs')
const { macosArchFromBuilder, readMacosArchitectures } = require('./build-desktop-macos.cjs')
const { prepareMacosAppIdentity } = require('./macos-app-identity.cjs')
const { prepareWindowsDpiManifest } = require('./windows-dpi-manifest.cjs')
const { assertStaticRuntime, readWindowsExecutableArch } = require('./windows-native-image.cjs')

exports.default = async function afterPack(context) {
  if (context.electronPlatformName === 'darwin') {
    const arch = macosArchFromBuilder(context.arch)
    const app = path.join(context.appOutDir, `${context.packager.appInfo.productFilename}.app`, 'Contents')
    const name =
      context.packager.platformSpecificBuildOptions.executableName || context.packager.appInfo.productFilename
    const executable = path.join(app, 'MacOS', name)
    const component = path.join(app, 'Resources', 'desktop_macos.node')
    if (!readMacosArchitectures(executable).includes(arch) || readMacosArchitectures(component).join() !== arch) {
      throw new Error('Packaged macOS desktop component architecture does not match Electron')
    }
    prepareMacosAppIdentity(executable, context.packager.appInfo.id)
    return
  }
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
