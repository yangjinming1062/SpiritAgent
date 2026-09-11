#!/usr/bin/env bash
# SpiritAgent 安装脚本（POSIX / macOS）。由 Tauri SpiritAgent-Setup.app 调用；
# 6 阶段负载释放：安装 Python（如需）、拷贝 runner wheel / 桌面应用 / skills 至 $SPIRITAGENT_HOME 及平台规范位置。
# 协议：
#   install.sh -Manifest                 → 输出 manifest JSON
#   install.sh -Stage NAME -Json         → 执行单个阶段，输出结果帧
# payload 位置通过 SPIRITAGENT_BUNDLE_* 环境变量或对应 --bundled-*-dir 参数传递；二者并存时环境变量优先。

set -euo pipefail
shopt -s nullglob

PROTOCOL_VERSION=2
SCRIPT_NAME="install.sh"
PYTHON_VERSION="3.13"

if [[ "$(uname -s)" == "Darwin" ]]; then
  DEFAULT_SPIRITAGENT_HOME_UNIX="$HOME/Library/Application Support/SpiritAgent"
else
  DEFAULT_SPIRITAGENT_HOME_UNIX="$HOME/.spiritagent"
fi

RUNNER_WHEEL_GLOB="spirit_agent-*.whl"

# 桌面端格式默认 dmg，可由 $SPIRITAGENT_INSTALLER_FORMAT 覆盖。
DEFAULT_DESKTOP_FORMAT="dmg"

SPIRITAGENT_HOME_ARG=""
BUNDLED_RUNNER_DIR_ARG=""
BUNDLED_DESKTOP_DIR_ARG=""
BUNDLED_SKILLS_DIR_ARG=""
BUNDLED_ONBOARDING_AUDIO_DIR_ARG=""

MODE="stage"
STAGE=""
JSON_OUTPUT=0
NON_INTERACTIVE=0

usage() {
  cat <<EOF
$SCRIPT_NAME — 唤生 桌面版安装器 (6-stage payload release)

Usage:
  $SCRIPT_NAME -Manifest
  $SCRIPT_NAME -Stage NAME [-Json] [-NonInteractive] \\
      [--spiritagent-home PATH] \\
      [--bundled-runner-dir PATH] \\
      [--bundled-desktop-dir PATH] \\
      [--bundled-skills-dir PATH] \\
      [--bundled-onboarding-audio-dir PATH]

Stages: welcome, install-python, unpack-runner, unpack-desktop, install-skills, finalize.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -Manifest|--manifest)         MODE="manifest"; shift ;;
    -Stage|--stage)               MODE="stage"; STAGE="$2"; shift 2 ;;
    -Json|--json)                 JSON_OUTPUT=1; shift ;;
    -NonInteractive|--non-interactive) NON_INTERACTIVE=1; shift ;;
    --spiritagent-home)                  SPIRITAGENT_HOME_ARG="$2"; shift 2 ;;
    --bundled-runner-dir)         BUNDLED_RUNNER_DIR_ARG="$2"; shift 2 ;;
    --bundled-desktop-dir)        BUNDLED_DESKTOP_DIR_ARG="$2"; shift 2 ;;
    --bundled-skills-dir)         BUNDLED_SKILLS_DIR_ARG="$2"; shift 2 ;;
    --bundled-onboarding-audio-dir) BUNDLED_ONBOARDING_AUDIO_DIR_ARG="$2"; shift 2 ;;
    -h|--help)                    usage; exit 0 ;;
    *)                            echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

# 路径优先级：环境变量 > 参数 > 默认值。
if [[ -n "${SPIRITAGENT_HOME:-}" ]]; then
  SPIRITAGENT_HOME_RESOLVED="$SPIRITAGENT_HOME"
elif [[ -n "$SPIRITAGENT_HOME_ARG" ]]; then
  SPIRITAGENT_HOME_RESOLVED="$SPIRITAGENT_HOME_ARG"
else
  SPIRITAGENT_HOME_RESOLVED="$DEFAULT_SPIRITAGENT_HOME_UNIX"
fi

BUNDLED_RUNNER_DIR="${SPIRITAGENT_BUNDLED_RUNNER_DIR:-$BUNDLED_RUNNER_DIR_ARG}"
BUNDLED_DESKTOP_DIR="${SPIRITAGENT_BUNDLED_DESKTOP_DIR:-$BUNDLED_DESKTOP_DIR_ARG}"
BUNDLED_SKILLS_DIR="${SPIRITAGENT_BUNDLED_SKILLS_DIR:-$BUNDLED_SKILLS_DIR_ARG}"
BUNDLED_ONBOARDING_AUDIO_DIR="${SPIRITAGENT_BUNDLED_ONBOARDING_AUDIO_DIR:-$BUNDLED_ONBOARDING_AUDIO_DIR_ARG}"
DESKTOP_FORMAT="${SPIRITAGENT_INSTALLER_FORMAT:-$DEFAULT_DESKTOP_FORMAT}"

emit_manifest() {
  printf '__SPIRITAGENT_MANIFEST__:{"protocol_version": %s, "stages": [{"name": "welcome", "title": "准备安装", "category": "setup", "needs_user_input": false}, {"name": "install-python", "title": "安装 Python 运行时", "category": "prereqs", "needs_user_input": false}, {"name": "unpack-runner", "title": "安装 唤生 运行器", "category": "payload", "needs_user_input": false}, {"name": "unpack-desktop", "title": "安装 唤生 桌面应用", "category": "payload", "needs_user_input": false}, {"name": "install-skills", "title": "安装内置技能", "category": "payload", "needs_user_input": false}, {"name": "finalize", "title": "完成安装", "category": "finalize", "needs_user_input": false}]}\n' "$PROTOCOL_VERSION"
}

# emit_stage_ok <stage> [skipped=0|1] [reason]
emit_stage_ok() {
  local stage="$1" skipped="${2:-0}" reason="${3:-}"
  if [[ "$skipped" == "1" && -n "$reason" ]]; then
    # reason 中的双引号转义后嵌入 JSON，保证结果帧合法。
    local esc="${reason//\\/\\\\}"
    esc="${esc//\"/\\\"}"
    printf '__SPIRITAGENT_STAGE_RESULT__:{"ok": true, "stage": "%s", "skipped": true, "reason": "%s"}\n' "$stage" "$esc"
  else
    printf '__SPIRITAGENT_STAGE_RESULT__:{"ok": true, "stage": "%s"}\n' "$stage"
  fi
}

# emit_stage_err <stage> <reason>
emit_stage_err() {
  local stage="$1" reason="$2"
  local esc="${reason//\\/\\\\}"
  esc="${esc//\"/\\\"}"
  printf '__SPIRITAGENT_STAGE_RESULT__:{"ok": false, "stage": "%s", "reason": "%s"}\n' "$stage" "$esc"
}

install_uv() {
  local managed_uv="$SPIRITAGENT_HOME_RESOLVED/bin/uv"
  if [[ -f "$managed_uv" ]]; then
    UV_CMD="$managed_uv"
    return 0
  fi

  mkdir -p "$SPIRITAGENT_HOME_RESOLVED/bin"

  if command -v curl >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | UV_INSTALL_DIR="$SPIRITAGENT_HOME_RESOLVED/bin" sh 2>/dev/null
  elif command -v wget >/dev/null 2>&1; then
    wget -qO- https://astral.sh/uv/install.sh | UV_INSTALL_DIR="$SPIRITAGENT_HOME_RESOLVED/bin" sh 2>/dev/null
  else
    return 1
  fi

  if [[ -f "$managed_uv" ]]; then
    UV_CMD="$managed_uv"
    return 0
  fi
  return 1
}

install_officecli() {
  if command -v officecli >/dev/null 2>&1; then
    return 0
  fi
  local managed_officecli="$SPIRITAGENT_HOME_RESOLVED/bin/officecli"
  if [[ -f "$managed_officecli" ]]; then
    return 0
  fi

  mkdir -p "$SPIRITAGENT_HOME_RESOLVED/bin"

  if command -v curl >/dev/null 2>&1; then
    curl -fsSL https://d.officecli.ai/install.sh | bash 2>/dev/null || true
  elif command -v wget >/dev/null 2>&1; then
    wget -qO- https://d.officecli.ai/install.sh | bash 2>/dev/null || true
  fi

  if [[ -f "$HOME/.local/bin/officecli" && ! -f "$managed_officecli" ]]; then
    cp "$HOME/.local/bin/officecli" "$managed_officecli" 2>/dev/null || true
  fi

  return 0
}

test_python() {
  if [[ -z "${UV_CMD:-}" ]]; then
    if ! install_uv; then
      return 1
    fi
  fi

  local candidates=("$PYTHON_VERSION" "3.12" "3.14" "3.11")
  local missing=()

  for ver in "${candidates[@]}"; do
    local found
    found=$("$UV_CMD" python find "$ver" 2>/dev/null || true)
    if [[ -n "$found" ]]; then
      PYTHON_VERSION="$ver"
      return 0
    fi
    missing+=("$ver")
  done

  # 冷缓存：安装首选版本后再次只查该版本。
  "$UV_CMD" python install "$PYTHON_VERSION" 2>/dev/null || true
  local found
  found=$("$UV_CMD" python find "$PYTHON_VERSION" 2>/dev/null || true)
  if [[ -n "$found" ]]; then
    return 0
  fi

  return 1
}

# 阶段 1：welcome
stage_welcome() {
  mkdir -p "$SPIRITAGENT_HOME_RESOLVED/bin" \
           "$SPIRITAGENT_HOME_RESOLVED/skills" \
           "$SPIRITAGENT_HOME_RESOLVED/logs"

  if [[ ! -d "$SPIRITAGENT_HOME_RESOLVED" ]]; then
    emit_stage_err welcome "could not create SPIRITAGENT_HOME: $SPIRITAGENT_HOME_RESOLVED"
    return 1
  fi
  if [[ ! -w "$SPIRITAGENT_HOME_RESOLVED" ]]; then
    emit_stage_err welcome "SPIRITAGENT_HOME not writable: $SPIRITAGENT_HOME_RESOLVED"
    return 1
  fi

  local marker="$SPIRITAGENT_HOME_RESOLVED/.spiritagent-bootstrap-complete"
  local is_reinstall="false"
  [[ -f "$marker" ]] && is_reinstall="true"

  printf '__SPIRITAGENT_STAGE_RESULT__:{"ok": true, "stage": "welcome", "data": {"spiritagent_home": "%s", "is_reinstall": %s}}\n' \
    "$SPIRITAGENT_HOME_RESOLVED" "$is_reinstall"
}

# 阶段 2：安装 Python
stage_install_python() {
  if test_python; then
    printf '__SPIRITAGENT_STAGE_RESULT__:{"ok": true, "stage": "install-python", "data": {"version": "%s"}}\n' "$PYTHON_VERSION"
    return 0
  fi

  emit_stage_err install-python "Python $PYTHON_VERSION is required but could not be installed. Install Python manually from https://www.python.org/downloads/ and re-run."
  return 1
}

# 阶段 3：解包运行器
stage_unpack_runner() {
  if [[ -z "$BUNDLED_RUNNER_DIR" ]]; then
    emit_stage_err unpack-runner "--bundled-runner-dir (or SPIRITAGENT_BUNDLED_RUNNER_DIR) is required"
    return 1
  fi
  if [[ ! -d "$BUNDLED_RUNNER_DIR" ]]; then
    emit_stage_err unpack-runner "bundled runner dir not found: $BUNDLED_RUNNER_DIR"
    return 1
  fi

  local wheel
  wheel=("$BUNDLED_RUNNER_DIR"/$RUNNER_WHEEL_GLOB)
  if [[ ${#wheel[@]} -eq 0 || ! -f "${wheel[0]}" ]]; then
    emit_stage_err unpack-runner "wheel not found in $BUNDLED_RUNNER_DIR"
    return 1
  fi
  wheel="${wheel[0]}"

  local runner_dir="$SPIRITAGENT_HOME_RESOLVED/runner"
  mkdir -p "$runner_dir"

  # 拷贝 server.py 至与 wheel 同级
  if [[ -f "$BUNDLED_RUNNER_DIR/server.py" ]]; then
    cp -f "$BUNDLED_RUNNER_DIR/server.py" "$runner_dir/server.py"
  fi

  # `--clear` 在重装时至关重要：缺省情况下 `uv venv` 遇到目标目录已存在会报错，留下陈旧/损坏 venv，正是本阶段要修复的故障态。install.ps1 同步使用该 flag。
  "$UV_CMD" venv "$runner_dir/.venv" --python "$PYTHON_VERSION" --clear 2>/dev/null || {
    emit_stage_err unpack-runner "uv venv failed"
    return 1
  }

  # 安装 wheel 至 venv。从国内访问 PyPI 不稳，首次失败回退至阿里云镜像（与 install.ps1 保持一致）；再次失败把同一错误透出给上层。
  if ! "$UV_CMD" pip install --python "$runner_dir/.venv/bin/python" "$wheel" 2>/dev/null; then
    if ! "$UV_CMD" pip install --python "$runner_dir/.venv/bin/python" \
        --index-url https://mirrors.aliyun.com/pypi/simple/ \
        "$wheel" 2>/dev/null; then
      emit_stage_err unpack-runner "uv pip install failed (PyPI + Aliyun mirror)"
      return 1
    fi
  fi

  # 不再做安装后烟测：build_client.{ps1,sh} 已经用 pytest 把 wheel 卡在打包前，损坏 venv 不会到达用户。

  # 清理旧的 PyInstaller 二进制
  rm -f "$SPIRITAGENT_HOME_RESOLVED/bin/spiritagent-runner"

  # 拷贝 onboarding 引导音频：语言子目录（zh/、en/、…）1:1 映射至 $SPIRITAGENT_HOME/audio/onboarding/<lang>/。
  local audio_count=0
  if [[ -n "$BUNDLED_ONBOARDING_AUDIO_DIR" && -d "$BUNDLED_ONBOARDING_AUDIO_DIR" ]]; then
    for lang_dir in "$BUNDLED_ONBOARDING_AUDIO_DIR"/*/; do
      [[ -d "$lang_dir" ]] || continue
      local lang
      lang=$(basename "$lang_dir")
      local audio_target="$SPIRITAGENT_HOME_RESOLVED/audio/onboarding/$lang"
      mkdir -p "$audio_target"
      cp -R "$lang_dir"/. "$audio_target"/
    done
    audio_count=$(find "$SPIRITAGENT_HOME_RESOLVED/audio/onboarding" -name '*.mp3' -type f | wc -l | tr -d ' ')
  fi

  local size
  size=$(stat -c%s "$wheel" 2>/dev/null || stat -f%z "$wheel" 2>/dev/null || echo 0)
  printf '__SPIRITAGENT_STAGE_RESULT__:{"ok": true, "stage": "unpack-runner", "data": {"venv": "%s/runner/.venv", "wheel": "%s", "size_bytes": %s, "onboarding_audio_copied": %s}}\n' \
    "$SPIRITAGENT_HOME_RESOLVED" "$(basename "$wheel")" "$size" "$audio_count"
}

# 阶段 4：解包桌面端
stage_unpack_desktop() {
  if [[ -z "$BUNDLED_DESKTOP_DIR" ]]; then
    emit_stage_err unpack-desktop "--bundled-desktop-dir (or SPIRITAGENT_BUNDLED_DESKTOP_DIR) is required"
    return 1
  fi
  if [[ ! -d "$BUNDLED_DESKTOP_DIR" ]]; then
    emit_stage_err unpack-desktop "bundled desktop dir not found: $BUNDLED_DESKTOP_DIR"
    return 1
  fi

  # 按格式定位产物
  local artifact=""
  case "$DESKTOP_FORMAT" in
    dmg)
      artifact=$(ls -1 "$BUNDLED_DESKTOP_DIR"/*.dmg 2>/dev/null | head -1 || true)
      ;;
    nsis)
      artifact=$(ls -1 "$BUNDLED_DESKTOP_DIR"/*.exe 2>/dev/null | head -1 || true)
      ;;
    zip)
      artifact=$(ls -1 "$BUNDLED_DESKTOP_DIR"/*.zip 2>/dev/null | head -1 || true)
      ;;
    *)
      emit_stage_err unpack-desktop "unknown desktop format: $DESKTOP_FORMAT"
      return 1
      ;;
  esac

  if [[ -z "$artifact" || ! -f "$artifact" ]]; then
    emit_stage_err unpack-desktop "no desktop artifact found in $BUNDLED_DESKTOP_DIR (format=$DESKTOP_FORMAT)"
    return 1
  fi

  case "$DESKTOP_FORMAT" in
    dmg)
      # macOS：挂载 DMG，把 SpiritAgent.app 拷到 /Applications，卸载并清空 xattr。
      if [[ "$(uname -s)" != "Darwin" ]]; then
        emit_stage_err unpack-desktop "dmg format requires macOS host"
        return 1
      fi
      local mount_point
      mount_point=$(hdiutil attach -nobrowse -readonly "$artifact" 2>/dev/null | awk '/\/Volumes/{print $3; exit}')
      if [[ -z "$mount_point" ]]; then
        emit_stage_err unpack-desktop "failed to mount $artifact"
        return 1
      fi
      if [[ ! -d "$mount_point/SpiritAgent.app" ]]; then
        hdiutil detach "$mount_point" 2>/dev/null || true
        emit_stage_err unpack-desktop "SpiritAgent.app not found in DMG $artifact"
        return 1
      fi
      rm -rf /Applications/SpiritAgent.app
      cp -R "$mount_point/SpiritAgent.app" /Applications/SpiritAgent.app
      hdiutil detach "$mount_point" 2>/dev/null || true
      xattr -cr /Applications/SpiritAgent.app 2>/dev/null || true
      printf '__SPIRITAGENT_STAGE_RESULT__:{"ok": true, "stage": "unpack-desktop", "data": {"installed_path": "/Applications/SpiritAgent.app", "format": "dmg"}}\n'
      ;;
    nsis|zip)
      # POSIX 上不支持这些格式；Windows 用户改走 install.ps1。
      emit_stage_err unpack-desktop "desktop format '$DESKTOP_FORMAT' is not supported on this platform (use install.ps1 on Windows)"
      return 1
      ;;
  esac
}

# 阶段 5：安装技能
stage_install_skills() {
  if [[ -z "$BUNDLED_SKILLS_DIR" ]]; then
    emit_stage_err install-skills "--bundled-skills-dir (or SPIRITAGENT_BUNDLED_SKILLS_DIR) is required"
    return 1
  fi
  if [[ ! -d "$BUNDLED_SKILLS_DIR" ]]; then
    emit_stage_err install-skills "bundled skills dir not found: $BUNDLED_SKILLS_DIR"
    return 1
  fi

  # 尊重 .no-bundled-skills 标记（由 --no-skills / spiritagent profile 设置）。
  if [[ -f "$SPIRITAGENT_HOME_RESOLVED/.no-bundled-skills" ]]; then
    emit_stage_ok install-skills 1 "user opted out via .no-bundled-skills"
    return 0
  fi

  # 用 rsync 不加 --delete 以保留用户本地添加的 skills。
  mkdir -p "$SPIRITAGENT_HOME_RESOLVED/skills"
  if command -v rsync >/dev/null 2>&1; then
    rsync -a "$BUNDLED_SKILLS_DIR/" "$SPIRITAGENT_HOME_RESOLVED/skills/"
  else
    cp -R "$BUNDLED_SKILLS_DIR/." "$SPIRITAGENT_HOME_RESOLVED/skills/"
  fi

  # 动态安装 OfficeCLI（若网络可用）
  install_officecli || true

  local bundled_count
  bundled_count=$(find "$SPIRITAGENT_HOME_RESOLVED/skills" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l | tr -d ' ')

  printf '__SPIRITAGENT_STAGE_RESULT__:{"ok": true, "stage": "install-skills", "data": {"bundled_count": %s}}\n' \
    "$bundled_count"
}

# 阶段 6：收尾
stage_finalize() {
  : > "$SPIRITAGENT_HOME_RESOLVED/.spiritagent-bootstrap-complete"

  printf '__SPIRITAGENT_STAGE_RESULT__:{"ok": true, "stage": "finalize", "data": {"marker": "%s/.spiritagent-bootstrap-complete"}}\n' \
    "$SPIRITAGENT_HOME_RESOLVED"
}

case "$MODE" in
  manifest)
    emit_manifest
    ;;
  stage)
    if [[ -z "$STAGE" ]]; then
      echo "error: --stage NAME is required (or pass -Manifest)" >&2
      exit 2
    fi
    case "$STAGE" in
      welcome)         stage_welcome ;;
      install-python)  stage_install_python ;;
      unpack-runner)   stage_unpack_runner ;;
      unpack-desktop)  stage_unpack_desktop ;;
      install-skills)  stage_install_skills ;;
      finalize)        stage_finalize ;;
      *)
        emit_stage_err "$STAGE" "unknown stage"
        exit 1
        ;;
    esac
    ;;
esac
