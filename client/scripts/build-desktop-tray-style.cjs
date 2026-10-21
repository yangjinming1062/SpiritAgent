'use strict'

const { spawnSync } = require('node:child_process')
const { createHash, randomUUID } = require('node:crypto')
const fs = require('node:fs')
const path = require('node:path')
const { assertStaticRuntime, readWindowsExecutableArch } = require('./windows-native-image.cjs')

function sourceFingerprint(sourceRoot, arch) {
  const hash = createHash('sha256').update(`desktop-tray-style\0${arch}\0`)
  const addFile = (name, file) => hash.update(`${name}\0`).update(fs.readFileSync(file)).update('\0')
  addFile('build-desktop-tray-style.cjs', __filename)
  addFile('windows-native-image.cjs', path.join(__dirname, 'windows-native-image.cjs'))
  const visit = directory => {
    for (const entry of fs
      .readdirSync(directory, { withFileTypes: true })
      .sort((a, b) => a.name.localeCompare(b.name))) {
      const file = path.join(directory, entry.name)
      if (entry.isDirectory()) visit(file)
      else if (entry.isFile() && /\.(?:cpp|h|hpp|def)$/.test(entry.name))
        addFile(path.relative(sourceRoot, file).replaceAll('\\', '/'), file)
    }
  }
  visit(sourceRoot)
  return hash.digest('hex').slice(0, 32)
}

function findToolchain(arch) {
  const programFiles = process.env['ProgramFiles(x86)'] || process.env.ProgramFiles
  if (!programFiles) throw new Error('Windows 桌面托盘组件需要 Visual Studio 2022 以上的 C++ 工具与 Windows SDK。')
  const vswhere = path.join(programFiles, 'Microsoft Visual Studio', 'Installer', 'vswhere.exe')
  const installation = spawnSync(
    vswhere,
    ['-latest', '-products', '*', '-version', '[17.0,)', '-property', 'installationPath'],
    { encoding: 'utf8', timeout: 30_000, windowsHide: true }
  )
  if (installation.error || installation.status !== 0 || !installation.stdout.trim())
    throw new Error(
      '未找到 Visual Studio 2022 以上的 C++ 工具，请通过 Visual Studio Installer 安装桌面 C++ 和 Windows SDK。'
    )
  const visualStudio = installation.stdout.trim()
  const versionFile = path.join(visualStudio, 'VC', 'Auxiliary', 'Build', 'Microsoft.VCToolsVersion.default.txt')
  if (!fs.existsSync(versionFile)) throw new Error('Visual Studio 未安装 MSVC C++ 工具。')
  const msvc = path.join(visualStudio, 'VC', 'Tools', 'MSVC', fs.readFileSync(versionFile, 'utf8').trim())
  const hosts = process.arch === 'arm64' ? ['Hostarm64', 'Hostx64'] : ['Hostx64']
  const compilerDirectory = hosts
    .map(host => path.join(msvc, 'bin', host, arch))
    .find(dir => fs.existsSync(path.join(dir, 'cl.exe')))
  if (!compilerDirectory || !fs.existsSync(path.join(msvc, 'lib', arch, 'libcmt.lib')))
    throw new Error(`Visual Studio 未安装 ${arch} MSVC 编译器与静态运行库，请安装对应目标的 C++ 工具。`)

  const registeredSdk = spawnSync(
    'reg.exe',
    ['query', 'HKLM\\SOFTWARE\\Microsoft\\Windows Kits\\Installed Roots', '/v', 'KitsRoot10', '/reg:32'],
    { encoding: 'utf8', timeout: 30_000, windowsHide: true }
  )
  const sdk =
    process.env.WindowsSdkDir ||
    registeredSdk.stdout?.match(/KitsRoot10\s+REG_SZ\s+([^\r\n]+)/)?.[1].trim() ||
    path.join(programFiles, 'Windows Kits', '10')
  const sdkInclude = path.join(sdk, 'Include')
  if (!fs.existsSync(sdkInclude)) throw new Error('未找到 Windows 10／11 SDK。')
  const sdkVersion = fs
    .readdirSync(sdkInclude, { withFileTypes: true })
    .filter(entry => entry.isDirectory() && /^10\.\d+\.\d+\.\d+$/.test(entry.name))
    .map(entry => entry.name)
    .sort((a, b) => b.localeCompare(a, 'en', { numeric: true }))
    .find(version =>
      [
        path.join(sdkInclude, version, 'um', 'windows.h'),
        path.join(sdkInclude, version, 'cppwinrt', 'winrt', 'Windows.UI.Xaml.h'),
        path.join(sdk, 'Lib', version, 'ucrt', arch, 'libucrt.lib'),
        path.join(sdk, 'Lib', version, 'um', arch, 'windowsapp.lib')
      ].every(file => fs.existsSync(file))
    )
  if (!sdkVersion) throw new Error(`Windows SDK 未包含 ${arch} 的 C++/WinRT 头文件与静态链接库。`)
  const sdkBin = (process.arch === 'arm64' ? ['arm64', 'x64'] : ['x64'])
    .map(host => path.join(sdk, 'bin', sdkVersion, host))
    .find(directory => fs.existsSync(path.join(directory, 'rc.exe')) && fs.existsSync(path.join(directory, 'mt.exe')))
  if (!sdkBin) throw new Error('Windows SDK 未包含本机资源编译器与 manifest 工具。')

  const env = { ...process.env }
  for (const key of Object.keys(env)) {
    if (['path', 'include', 'lib', 'libpath', 'cl', '_cl_', 'link'].includes(key.toLowerCase())) delete env[key]
  }
  env.Path = `${compilerDirectory};${sdkBin};${process.env.Path || process.env.PATH || ''}`
  env.INCLUDE = [
    path.join(msvc, 'include'),
    ...['ucrt', 'shared', 'um', 'winrt', 'cppwinrt'].map(name => path.join(sdkInclude, sdkVersion, name))
  ].join(';')
  env.LIB = [
    path.join(msvc, 'lib', arch),
    ...['ucrt', 'um'].map(name => path.join(sdk, 'Lib', sdkVersion, name, arch))
  ].join(';')
  return { compiler: path.join(compilerDirectory, 'cl.exe'), env }
}

function assertTrayStyle(file, arch) {
  if (readWindowsExecutableArch(file) !== arch)
    throw new Error(`Windows desktop tray style architecture does not match ${arch}`)
  assertStaticRuntime(file)
}

function writeManifest(manifest, file) {
  const value = `${JSON.stringify({ version: 1, file }, null, 2)}\n`
  if (fs.existsSync(manifest) && fs.readFileSync(manifest, 'utf8') === value) return
  const temporary = `${manifest}.${randomUUID()}.tmp`
  try {
    fs.writeFileSync(temporary, value, { encoding: 'utf8', flag: 'wx' })
    fs.renameSync(temporary, manifest)
  } finally {
    fs.rmSync(temporary, { force: true })
  }
}

function preparePackaging(buildRoot, file, arch) {
  const staging = path.resolve(buildRoot, 'tray-style-resources')
  if (path.dirname(staging) !== path.resolve(buildRoot)) throw new Error('Invalid desktop tray style staging directory')
  fs.mkdirSync(staging, { recursive: true })
  const target = path.join(staging, file)
  if (!fs.existsSync(target)) fs.copyFileSync(path.join(buildRoot, file), target, fs.constants.COPYFILE_EXCL)
  assertTrayStyle(target, arch)
  writeManifest(path.join(staging, 'desktop-tray-style.json'), file)
  // 此目录只用于打包，不含可执行 host，不会作为 Explorer 模块加载路径。
  for (const entry of fs.readdirSync(staging, { withFileTypes: true })) {
    if (entry.isFile() && entry.name !== file && /^desktop-tray-style-[a-f0-9]{32}\.dll$/.test(entry.name))
      fs.rmSync(path.join(staging, entry.name))
  }
}

function buildDesktopTrayStyle(arch = process.arch) {
  if (!['x64', 'arm64'].includes(arch)) throw new Error(`Unsupported Windows desktop tray style architecture: ${arch}`)
  const clientRoot = path.resolve(__dirname, '..')
  const sourceRoot = path.join(clientRoot, 'desktop-windows', 'tray-style')
  const buildRoot = path.join(clientRoot, 'build', arch)
  const fingerprint = sourceFingerprint(sourceRoot, arch)
  const file = `desktop-tray-style-${fingerprint}.dll`
  const packaged = path.join(buildRoot, file)
  const manifest = path.join(buildRoot, 'desktop-tray-style.json')
  fs.mkdirSync(buildRoot, { recursive: true })

  if (fs.existsSync(packaged)) {
    assertTrayStyle(packaged, arch)
    writeManifest(manifest, file)
    preparePackaging(buildRoot, file, arch)
    console.log(`[desktop-tray-style] reusing ${arch} module: ${packaged}`)
    return packaged
  }
  if (process.platform !== 'win32') throw new Error('Windows 桌面托盘组件只能在 Windows 上编译。')
  const { compiler, env } = findToolchain(arch)
  const staging = fs.mkdtempSync(path.join(buildRoot, '.tray-style-'))
  const stagedDll = path.join(staging, 'desktop-tray-style.dll')
  try {
    const args = [
      '/nologo',
      '/std:c++20',
      '/EHsc',
      '/MT',
      '/O2',
      '/W4',
      '/LD',
      '/utf-8',
      '/permissive-',
      '/Zc:__cplusplus',
      '/DUNICODE',
      '/D_UNICODE',
      '/DWIN32_LEAN_AND_MEAN',
      '/DNOMINMAX',
      '/DWINVER=0x0A00',
      '/D_WIN32_WINNT=0x0A00',
      `/Fo${path.join(staging, 'tray-style.obj')}`,
      `/Fd${path.join(staging, 'tray-style.pdb')}`,
      `/Fe${stagedDll}`,
      path.join(sourceRoot, 'tray-style.cpp'),
      '/link',
      '/INCREMENTAL:NO',
      '/DYNAMICBASE',
      '/NXCOMPAT',
      '/OPT:REF',
      '/OPT:ICF',
      '/MANIFEST:EMBED',
      `/MACHINE:${arch === 'arm64' ? 'ARM64' : 'X64'}`,
      'windowsapp.lib',
      'ole32.lib',
      'oleaut32.lib',
      'user32.lib',
      'advapi32.lib',
      'comctl32.lib'
    ]
    // MSVC 使用 Unicode response file，路径不经过命令行 shell 或环境变量插值。
    const quote = arg => `"${arg.replace(/(\\*)"/g, '$1$1\\"').replace(/\\+$/g, '$&$&')}"`
    const response = path.join(staging, 'compile.rsp')
    fs.writeFileSync(
      response,
      Buffer.concat([Buffer.from([0xff, 0xfe]), Buffer.from(`${args.map(quote).join(' ')}\r\n`, 'utf16le')])
    )
    const result = spawnSync(compiler, [`@${response}`], {
      cwd: staging,
      stdio: 'inherit',
      timeout: 5 * 60_000,
      windowsHide: true,
      env
    })
    if (result.error) throw new Error(`Windows 桌面托盘组件编译失败：${result.error.message}`)
    if (result.status !== 0)
      throw new Error(`Windows 桌面托盘组件编译失败（退出码 ${result.status ?? '未知'}），请检查上方 MSVC 诊断。`)
    if (sourceFingerprint(sourceRoot, arch) !== fingerprint)
      throw new Error('Windows 桌面托盘源码或构建输入在编译期间变化，请重新构建。')
    assertTrayStyle(stagedDll, arch)
    try {
      // Explorer 可能继续映射旧模块；相同 hash 的产物也不能覆盖或删除。
      fs.copyFileSync(stagedDll, packaged, fs.constants.COPYFILE_EXCL)
    } catch (error) {
      if (error.code !== 'EEXIST') throw error
      assertTrayStyle(packaged, arch)
    }
    writeManifest(manifest, file)
    preparePackaging(buildRoot, file, arch)
    console.log(`Windows desktop tray style built for ${arch}: ${packaged}`)
    return packaged
  } finally {
    fs.rmSync(staging, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 })
  }
}

module.exports = { buildDesktopTrayStyle }

if (require.main === module) {
  try {
    const args = process.argv.slice(2)
    if (args.length && (args.length !== 2 || args[0] !== '--arch' || !['x64', 'arm64'].includes(args[1])))
      throw new Error('Usage: build-desktop-tray-style.cjs [--arch x64|arm64]')
    buildDesktopTrayStyle(args[1])
  } catch (error) {
    console.error(`[desktop-tray-style] ${error.message}`)
    process.exitCode = 1
  }
}
