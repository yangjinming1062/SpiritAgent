#!/usr/bin/env bash
# SpiritAgent 安装脚本（macOS/bash）。协议：-Manifest 输出阶段列表；-Stage NAME 执行单阶段。payload 经 SPIRITAGENT_BUNDLED_* 传入。

set -euo pipefail
shopt -s nullglob

PROTOCOL_VERSION=2
PYTHON_VERSION="3.13"

if [[ "$(uname -s)" == "Darwin" ]]; then
  DEFAULT_SPIRITAGENT_HOME="$HOME/Library/Application Support/SpiritAgent"
else
  DEFAULT_SPIRITAGENT_HOME="$HOME/.spiritagent"
fi

SPIRITAGENT_HOME="${SPIRITAGENT_HOME:-$DEFAULT_SPIRITAGENT_HOME}"
BUNDLED_RUNNER_DIR="${SPIRITAGENT_BUNDLED_RUNNER_DIR:-}"
BUNDLED_DESKTOP_DIR="${SPIRITAGENT_BUNDLED_DESKTOP_DIR:-}"
BUNDLED_SKILLS_DIR="${SPIRITAGENT_BUNDLED_SKILLS_DIR:-}"
BUNDLED_ONBOARDING_AUDIO_DIR="${SPIRITAGENT_BUNDLED_ONBOARDING_AUDIO_DIR:-}"
DESKTOP_FORMAT="${SPIRITAGENT_INSTALLER_FORMAT:-dmg}"
UV_CMD="${UV_CMD:-}"

emit_manifest() {
  printf '__SPIRITAGENT_MANIFEST__:{"protocol_version": %s, "stages": [{"name": "welcome", "title": "准备安装"}, {"name": "install-python", "title": "准备运行环境"}, {"name": "unpack-runner", "title": "安装电脑助手"}, {"name": "unpack-desktop", "title": "安装唤生应用"}, {"name": "install-skills", "title": "准备常用功能"}, {"name": "finalize", "title": "完成安装设置"}]}\n' "$PROTOCOL_VERSION"
}

install_uv() {
  local managed_uv="$SPIRITAGENT_HOME/bin/uv"
  [[ -f "$managed_uv" ]] && { UV_CMD="$managed_uv"; return 0; }

  mkdir -p "$SPIRITAGENT_HOME/bin"
  if command -v curl >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | UV_INSTALL_DIR="$SPIRITAGENT_HOME/bin" sh 2>/dev/null
  elif command -v wget >/dev/null 2>&1; then
    wget -qO- https://astral.sh/uv/install.sh | UV_INSTALL_DIR="$SPIRITAGENT_HOME/bin" sh 2>/dev/null
  else
    return 1
  fi

  [[ -f "$managed_uv" ]] && UV_CMD="$managed_uv"
}

install_officecli() {
  command -v officecli >/dev/null 2>&1 && return 0
  local managed_officecli="$SPIRITAGENT_HOME/bin/officecli"
  [[ -f "$managed_officecli" ]] && return 0

  mkdir -p "$SPIRITAGENT_HOME/bin"
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL https://d.officecli.ai/install.sh | bash 2>/dev/null || true
  elif command -v wget >/dev/null 2>&1; then
    wget -qO- https://d.officecli.ai/install.sh | bash 2>/dev/null || true
  fi

  if [[ -f "$HOME/.local/bin/officecli" && ! -f "$managed_officecli" ]]; then
    cp "$HOME/.local/bin/officecli" "$managed_officecli" 2>/dev/null || true
  fi
}

test_python() {
  if [[ -z "$UV_CMD" ]] && ! install_uv; then
    return 1
  fi

  local ver found
  for ver in "$PYTHON_VERSION" "3.14"; do
    found=$("$UV_CMD" python find "$ver" 2>/dev/null || true)
    if [[ -n "$found" ]]; then
      PYTHON_VERSION="$ver"
      return 0
    fi
  done

  # 冷缓存：安装首选版本后只再查该版本
  "$UV_CMD" python install "$PYTHON_VERSION" 2>/dev/null || true
  found=$("$UV_CMD" python find "$PYTHON_VERSION" 2>/dev/null || true)
  [[ -n "$found" ]]
}

# 阶段 1：welcome
stage_welcome() {
  # mkdir 失败不中断，由下方 -d 检查统一发错误帧，避免 set -e 无结果帧退出
  mkdir -p "$SPIRITAGENT_HOME/bin" "$SPIRITAGENT_HOME/skills" "$SPIRITAGENT_HOME/logs" || true

  if [[ ! -d "$SPIRITAGENT_HOME" ]]; then
    echo "could not create SPIRITAGENT_HOME: $SPIRITAGENT_HOME" >&2
    return 1
  fi
  if [[ ! -w "$SPIRITAGENT_HOME" ]]; then
    echo "SPIRITAGENT_HOME not writable: $SPIRITAGENT_HOME" >&2
    return 1
  fi
}

# 阶段 2：安装 Python
stage_install_python() {
  if ! test_python; then
    echo "Python $PYTHON_VERSION is required but could not be installed. Install Python manually from https://www.python.org/downloads/ and re-run." >&2
    return 1
  fi
}

# 阶段 3：解包运行器
stage_unpack_runner() {
  # 每阶段独立进程，须重新推导 uv 与 Python 版本（可能落在 3.14 回退）
  if [[ -z "$UV_CMD" ]] && ! test_python; then
    echo "Python runtime not available (uv or Python missing)" >&2
    return 1
  fi

  if [[ -z "$BUNDLED_RUNNER_DIR" ]]; then
    echo "SPIRITAGENT_BUNDLED_RUNNER_DIR is required" >&2
    return 1
  fi
  if [[ ! -d "$BUNDLED_RUNNER_DIR" ]]; then
    echo "bundled runner dir not found: $BUNDLED_RUNNER_DIR" >&2
    return 1
  fi

  local wheel
  wheel=("$BUNDLED_RUNNER_DIR"/spirit_agent-*.whl)
  if [[ ${#wheel[@]} -ne 1 || ! -f "${wheel[0]}" ]]; then
    echo "expected one runner wheel in $BUNDLED_RUNNER_DIR" >&2
    return 1
  fi
  wheel="${wheel[0]}"

  local runner_dir="$SPIRITAGENT_HOME/runner"
  mkdir -p "$runner_dir"

  # --clear 重建 venv，缺省遇已存在目录会报错并留下损坏环境
  "$UV_CMD" venv "$runner_dir/.venv" --python "$PYTHON_VERSION" --clear 2>/dev/null || {
    echo "uv venv failed" >&2
    return 1
  }

  # 安装 wheel；PyPI 不稳时回退镜像（SPIRITAGENT_PYPI_INDEX_URL / PIP_INDEX_URL，缺省阿里云）
  if ! "$UV_CMD" pip install --python "$runner_dir/.venv/bin/python" "$wheel" 2>/dev/null; then
    local index_url="${SPIRITAGENT_PYPI_INDEX_URL:-${PIP_INDEX_URL:-https://mirrors.aliyun.com/pypi/simple/}}"
    if ! "$UV_CMD" pip install --python "$runner_dir/.venv/bin/python" \
        --index-url "$index_url" \
        "$wheel" 2>/dev/null; then
      echo "uv pip install failed (PyPI + fallback mirror)" >&2
      return 1
    fi
  fi

  if ! "$runner_dir/.venv/bin/python" -I -m spirit_agent_runner --check; then
    echo "runner health check failed" >&2
    return 1
  fi

  # onboarding 音频按语言子目录映射至 audio/onboarding/<lang>/
  if [[ -n "$BUNDLED_ONBOARDING_AUDIO_DIR" && -d "$BUNDLED_ONBOARDING_AUDIO_DIR" ]]; then
    for lang_dir in "$BUNDLED_ONBOARDING_AUDIO_DIR"/*/; do
      [[ -d "$lang_dir" ]] || continue
      mkdir -p "$SPIRITAGENT_HOME/audio/onboarding/$(basename "$lang_dir")"
      cp -R "$lang_dir"/. "$SPIRITAGENT_HOME/audio/onboarding/$(basename "$lang_dir")"/
    done
  fi
}

# 阶段 4：解包桌面端
stage_unpack_desktop() {
  if [[ -z "$BUNDLED_DESKTOP_DIR" ]]; then
    echo "SPIRITAGENT_BUNDLED_DESKTOP_DIR is required" >&2
    return 1
  fi
  if [[ ! -d "$BUNDLED_DESKTOP_DIR" ]]; then
    echo "bundled desktop dir not found: $BUNDLED_DESKTOP_DIR" >&2
    return 1
  fi

  if [[ "$DESKTOP_FORMAT" != "dmg" ]]; then
    echo "desktop format '$DESKTOP_FORMAT' is not supported on this platform (use install.ps1 on Windows)" >&2
    return 1
  fi

  local artifact
  artifact=("$BUNDLED_DESKTOP_DIR"/*.dmg)
  if [[ ${#artifact[@]} -ne 1 || ! -f "${artifact[0]}" ]]; then
    echo "no desktop artifact found in $BUNDLED_DESKTOP_DIR (format=$DESKTOP_FORMAT)" >&2
    return 1
  fi
  artifact="${artifact[0]}"

  if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "dmg format requires macOS host" >&2
    return 1
  fi

  # 挂载 DMG，拷 SpiritAgent.app 到 /Applications，卸载并清 xattr
  local mount_point
  mount_point=$(hdiutil attach -nobrowse -readonly "$artifact" 2>/dev/null | awk '/\/Volumes/{print $3; exit}')
  if [[ -z "$mount_point" ]]; then
    echo "failed to mount $artifact" >&2
    return 1
  fi
  if [[ ! -d "$mount_point/SpiritAgent.app" ]]; then
    hdiutil detach "$mount_point" 2>/dev/null || true
    echo "SpiritAgent.app not found in DMG $artifact" >&2
    return 1
  fi
  rm -rf /Applications/SpiritAgent.app
  # 拷贝失败须先卸载 DMG，避免 set -e 退出时挂载点泄漏
  if ! cp -R "$mount_point/SpiritAgent.app" /Applications/SpiritAgent.app; then
    hdiutil detach "$mount_point" 2>/dev/null || true
    echo "failed to copy SpiritAgent.app from $artifact" >&2
    return 1
  fi
  hdiutil detach "$mount_point" 2>/dev/null || true
  xattr -cr /Applications/SpiritAgent.app 2>/dev/null || true
}

# 阶段 5：安装技能
stage_install_skills() {
  if [[ -z "$BUNDLED_SKILLS_DIR" ]]; then
    echo "SPIRITAGENT_BUNDLED_SKILLS_DIR is required" >&2
    return 1
  fi
  if [[ ! -d "$BUNDLED_SKILLS_DIR" ]]; then
    echo "bundled skills dir not found: $BUNDLED_SKILLS_DIR" >&2
    return 1
  fi

  # 尊重 .no-bundled-skills 标记（skipped 语义由 Tauri 侧按退出码判断）
  if [[ -f "$SPIRITAGENT_HOME/.no-bundled-skills" ]]; then
    return 0
  fi

  # 不加 --delete，保留用户自装 skills
  mkdir -p "$SPIRITAGENT_HOME/skills"
  if command -v rsync >/dev/null 2>&1; then
    rsync -a "$BUNDLED_SKILLS_DIR/" "$SPIRITAGENT_HOME/skills/"
  else
    cp -R "$BUNDLED_SKILLS_DIR/." "$SPIRITAGENT_HOME/skills/"
  fi

  # OfficeCLI 尽力安装
  install_officecli || true
}

# 阶段 6：收尾
stage_finalize() {
  : > "$SPIRITAGENT_HOME/.spiritagent-bootstrap-complete"
}

MODE="stage"
STAGE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -Manifest|--manifest) MODE="manifest"; shift ;;
    -Stage|--stage)       STAGE="$2"; shift 2 ;;
    *)                    echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

case "$MODE" in
  manifest)
    emit_manifest
    ;;
  stage)
    if [[ -z "$STAGE" ]]; then
      echo "error: -Stage NAME is required (or pass -Manifest)" >&2
      exit 2
    fi
    case "$STAGE" in
      welcome)         stage_welcome ;;
      install-python)  stage_install_python ;;
      unpack-runner)   stage_unpack_runner ;;
      unpack-desktop)  stage_unpack_desktop ;;
      install-skills)  stage_install_skills ;;
      finalize)        stage_finalize ;;
      *)               echo "unknown stage: $STAGE" >&2; exit 1 ;;
    esac
    ;;
esac
