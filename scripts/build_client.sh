#!/usr/bin/env bash
# 打包 SpiritAgent 客户端安装器（macOS）。Backend 由 Docker 单独构建，不在此入口内。
# 编排顺序：构建 runner wheel → electron-builder 桌面端 → 暂存 payload → 临时改 tauri.conf.json → Tauri 构建 → 还原配置 → 拷贝最终安装器。
# 用法：scripts/build_client.sh --version X.Y.Z [--target mac] [--skip-runner] [--skip-desktop] [--sign-identity ID] [--notary-profile NAME] [--output DIR]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

VERSION=""
TARGET=""
SKIP_RUNNER=0
SKIP_DESKTOP=0
SIGN_IDENTITY=""
NOTARY_PROFILE=""
OUTPUT_DIR="$REPO_ROOT/release"

DESKTOP_PNPM_TARGET=""
DESKTOP_ARTIFACT_GLOB=""
DESKTOP_FORMAT=""
TAURI_BUNDLE_DIR=""

usage() {
  cat <<EOF
Usage: $0 --version X.Y.Z [--target mac] [--skip-runner] [--skip-desktop] \\
       [--sign-identity ID] [--notary-profile NAME] [--output DIR]

Backend (Docker) is built separately. This script only builds the client
(runner + desktop) and bundles them into the Tauri installer.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --version)         VERSION="$2"; shift 2 ;;
    --target)
      TARGET="$2"
      case "$TARGET" in
        mac) ;;
        *) echo "error: --target must be 'mac' (got '$TARGET')" >&2; exit 2 ;;
      esac
      shift 2 ;;
    --skip-runner)     SKIP_RUNNER=1; shift ;;
    --skip-desktop)    SKIP_DESKTOP=1; shift ;;
    --sign-identity)   SIGN_IDENTITY="$2"; shift 2 ;;
    --notary-profile)  NOTARY_PROFILE="$2"; shift 2 ;;
    --output)          OUTPUT_DIR="$2"; shift 2 ;;
    -h|--help)         usage; exit 0 ;;
    *)                 echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$VERSION" ]]; then
  echo "error: --version X.Y.Z is required" >&2
  usage
  exit 2
fi

# 缺省从 uname 推断目标平台
HOST_OS="$(uname -s)"
if [[ -z "$TARGET" ]]; then
  case "$HOST_OS" in
    Darwin)  TARGET="mac" ;;
    *)       echo "error: cannot infer target from host OS '$HOST_OS'. Pass --target." >&2; exit 1 ;;
  esac
fi

# 不允许跨主机构建；macOS 只能在 Darwin 上产出。
if [[ "$TARGET" == "mac" && "$HOST_OS" != "Darwin" ]]; then
  echo "error: --target mac requires a macOS host (got '$HOST_OS')" >&2
  exit 1
fi
DESKTOP_PNPM_TARGET="dist:mac:dmg"
DESKTOP_ARTIFACT_GLOB="SpiritAgent-${VERSION}-mac-*.dmg"
DESKTOP_FORMAT="dmg"
TAURI_BUNDLE_DIR="dmg"

# 通用构建依赖；缺则报错并打印缺哪一个。
for cmd in uv pnpm node python3; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "error: required build dep '$cmd' not found in PATH" >&2
    exit 1
  fi
done

# macOS 专属依赖
for cmd in hdiutil codesign; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "error: required command '$cmd' not found in PATH" >&2
    exit 1
  fi
done

set_version() {
  python3 "$SCRIPT_DIR/lib/build_helpers.py" set-version "$1"
}

stage_payload() {
  python3 "$SCRIPT_DIR/lib/build_helpers.py" stage-payload --target mac
}

patch_tauri_config() {
  python3 "$SCRIPT_DIR/lib/build_helpers.py" patch-tauri-config
}

restore_tauri_config() {
  python3 "$SCRIPT_DIR/lib/build_helpers.py" restore-tauri-config
}

# 退出时无论成败始终还原 tauri.conf.json
trap restore_tauri_config EXIT

cd "$REPO_ROOT"

set_version "$VERSION"

if [[ $SKIP_RUNNER -eq 0 ]]; then
  echo "==> Building runner (uv build wheel → dist/spiritagent-agent-*.whl)"
  ( cd runner && \
      uv sync --frozen --extra dev && \
      uv build --wheel --out-dir dist ) \
    || { echo "FAIL: runner build failed" >&2; exit 1; }
else
  echo "==> Skipping runner build (--skip-runner)"
fi

if [[ $SKIP_DESKTOP -eq 0 ]]; then
  echo "==> Building client (electron-builder → release/SpiritAgent-${VERSION}-${TARGET}*)"
  ( cd client && pnpm install --frozen-lockfile && pnpm run $DESKTOP_PNPM_TARGET ) \
    || { echo "FAIL: client build failed" >&2; exit 1; }
else
  echo "==> Skipping client build (--skip-desktop)"
fi

DESKTOP_ARTIFACT="$(ls -1 client/release/${DESKTOP_ARTIFACT_GLOB} 2>/dev/null | head -1 || true)"
if [[ -z "$DESKTOP_ARTIFACT" ]]; then
  echo "error: no desktop artifact matching '$DESKTOP_ARTIFACT_GLOB' found in client/release/" >&2
  exit 1
fi
echo "==> Desktop artifact: $DESKTOP_ARTIFACT"

stage_payload
cp "$DESKTOP_ARTIFACT" "installer/payload/client/"

# macOS 签名 + 公证
if [[ -n "$SIGN_IDENTITY" ]]; then
  echo "==> Code-signing $DESKTOP_ARTIFACT"
  cp "$DESKTOP_ARTIFACT" "${DESKTOP_ARTIFACT}.unsigned"
  codesign --deep --force --options runtime --sign "$SIGN_IDENTITY" "$DESKTOP_ARTIFACT"
  if [[ -n "$NOTARY_PROFILE" ]]; then
    echo "==> Notarizing $DESKTOP_ARTIFACT"
    xcrun notarytool submit "$DESKTOP_ARTIFACT" --keychain-profile "$NOTARY_PROFILE" --wait
    xcrun stapler staple "$DESKTOP_ARTIFACT"
  fi
  # 重新拷回 payload，覆盖未签名版本。
  cp "$DESKTOP_ARTIFACT" "installer/payload/client/"
fi

patch_tauri_config

echo "==> Tauri build"
( cd installer && pnpm install --frozen-lockfile && pnpm run tauri:build )

FINAL_DIR="installer/src-tauri/target/release/bundle/$TAURI_BUNDLE_DIR"
FINAL_GLOB="SpiritAgent-Setup_${VERSION}_*.dmg"
FINAL="$(ls -1 $FINAL_DIR/$FINAL_GLOB 2>/dev/null | head -1 || true)"
if [[ -z "$FINAL" ]]; then
  FINAL_GLOB="*.dmg"
  FINAL="$(ls -1 $FINAL_DIR/$FINAL_GLOB 2>/dev/null | head -1 || true)"
fi
if [[ -z "$FINAL" ]]; then
  echo "error: Tauri build did not produce $FINAL_DIR/$FINAL_GLOB" >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR"
FINAL_NAME="$(basename "$FINAL")"
cp "$FINAL" "$OUTPUT_DIR/$FINAL_NAME"
# 同时生成标准命名的别名副本，方便统一消费
cp "$FINAL" "$OUTPUT_DIR/SpiritAgent-Setup-${VERSION}.dmg"
echo ""
echo "==> Final installer: $OUTPUT_DIR/SpiritAgent-Setup-${VERSION}.dmg (also $OUTPUT_DIR/$FINAL_NAME)"
