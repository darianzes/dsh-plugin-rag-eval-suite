#!/usr/bin/env bash
# =============================================================================
# release.sh —— DSH 插件一键发版（闸门 → 版本同步 → 打包验收 → npm 发布 → tag/release）
#
#   ./scripts/release.sh 0.3.0                # 发布指定版本
#   ./scripts/release.sh patch                # 语义化自增（patch|minor|major）
#   ./scripts/release.sh 0.3.0 --dry-run      # 只演练：跑闸门 + 打印计划，不改文件、不发布
#   ./scripts/release.sh 0.3.0 --skip-publish # 打包 + 版本同步 + 提交，手动发布
#   ./scripts/release.sh 0.3.0 --otp 123456   # 直接带 OTP 发布
#
# 退出码：0 成功；1 前置/闸门失败；2 用法错误。
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; BLUE=$'\033[34m'; BOLD=$'\033[1m'; NC=$'\033[0m'

DRY_RUN=0
SKIP_PUBLISH=0
SKIP_GIT=0
SKIP_REAL=0
ASSUME_YES=0
OTP=""
NOTES=""
VERSION_ARG=""

step()  { printf '\n%s▸ %s%s\n' "$BLUE$BOLD" "$1" "$NC"; }
ok()    { printf '  %s✅ %s%s\n' "$GREEN" "$1" "$NC"; }
warn()  { printf '  %s⚠️  %s%s\n' "$YELLOW" "$1" "$NC"; }
die()   { printf '\n%s✖ %s%s\n' "$RED" "$1" "$NC" >&2; exit 1; }
run()   { if [ "$DRY_RUN" = "1" ]; then printf '  %s[dry-run]%s %s\n' "$YELLOW" "$NC" "$*"; else "$@"; fi; }

usage() {
  sed -n '3,15p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit 2
}

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --skip-publish) SKIP_PUBLISH=1 ;;
    --skip-git) SKIP_GIT=1 ;;
    --skip-real) SKIP_REAL=1 ;;
    -y|--yes) ASSUME_YES=1 ;;
    --otp) OTP="${2:-}"; shift ;;
    --notes) NOTES="${2:-}"; shift ;;
    -h|--help) usage ;;
    -*) die "未知参数: $1" ;;
    *) VERSION_ARG="$1" ;;
  esac
  shift
done
[ -n "$VERSION_ARG" ] || usage

PKG_NAME="$(python3 -c "import json;print(json.load(open('package.json'))['name'])")"
CURRENT_VERSION="$(python3 -c "import json;print(json.load(open('package.json'))['version'])")"
PROBE="skills/dsh-plugin-release/scripts/probe-define-tools.mjs"

# ---------------------------------------------------------------- 版本计算
compute_version() {
  python3 - "$CURRENT_VERSION" "$1" <<'PY'
import sys, re
cur, arg = sys.argv[1], sys.argv[2]
parts = [int(x) for x in re.findall(r'\d+', cur)[:3]]
while len(parts) < 3: parts.append(0)
major, minor, patch = parts
if arg == 'major':  major, minor, patch = major + 1, 0, 0
elif arg == 'minor': minor, patch = minor + 1, 0
elif arg == 'patch': patch += 1
elif re.fullmatch(r'\d+\.\d+\.\d+', arg): major, minor, patch = [int(x) for x in arg.split('.')]
else: sys.exit(f"无效版本号: {arg}（用 x.y.z 或 major/minor/patch）")
print(f"{major}.{minor}.{patch}")
PY
}
NEW_VERSION="$(compute_version "$VERSION_ARG")" || exit 2
TAG="v${NEW_VERSION}"

printf '%s\n' "${BOLD}Release ${PKG_NAME}${NC}: ${CURRENT_VERSION} → ${GREEN}${NEW_VERSION}${NC}  ${YELLOW}(dry-run=${DRY_RUN})${NC}"

# ---------------------------------------------------------------- 0. 前置检查
step "0/7 前置检查"
[ -f package.json ] || die "不在插件仓库根目录（缺少 package.json）"
[ -d .git ] || die "当前目录不是 git 仓库"

if [ "$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '')" != "main" ]; then
  warn "当前不在 main 分支（$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '未知')）"
fi
if [ -n "$(git status --porcelain)" ]; then
  if [ "$DRY_RUN" = "1" ]; then
    warn "工作区有未提交改动（dry-run 允许；正式发版前必须提交）："
    git status --short | sed 's/^/    /'
  else
    git status --short | sed 's/^/    /'
    die "工作区不干净。先提交或 stash（发版脚本会自己提交版本变更）"
  fi
else
  ok "git 工作区干净"
fi

if git rev-parse -q --verify "refs/tags/${TAG}" >/dev/null; then
  die "tag ${TAG} 已存在。换一个版本号，或 git tag -d ${TAG} 后再发"
fi
ok "tag ${TAG} 未被占用"

if ! grep -Eq "^## \[${NEW_VERSION}\]" CHANGELOG.md; then
  die "CHANGELOG.md 缺少 '## [${NEW_VERSION}]' 段落。先写变更记录再发版"
fi
ok "CHANGELOG 已有 ${NEW_VERSION} 段落"

NPM_USER="$(npm whoami 2>/dev/null || true)"
if [ "$SKIP_PUBLISH" = "0" ]; then
  [ -n "$NPM_USER" ] || die "npm 未登录（npm login）。或改用 --skip-publish"
  ok "npm 已登录: ${NPM_USER}"
else
  warn "跳过 npm 发布（--skip-publish）"
fi

# ---------------------------------------------------------------- 1. G1 逻辑闸
step "1/7 G1 逻辑闸：单元测试"
run python3 -m unittest discover -s tests -v 2>&1 | tail -5
ok "单元测试通过"

# ---------------------------------------------------------------- 2. G2 契约闸
step "2/7 G2 契约闸：DSH 工具定义"
node --check dsh/index.mjs || die "dsh/index.mjs 语法错误"
ok "node --check 通过"

if [ -f "$PROBE" ]; then
  node "$PROBE" ./dsh/index.mjs | tail -3
  ok "离线契约校验通过（additionalProperties / output / inject）"
  DSH_APP="/Applications/DeepSeek Harness.app"
  if [ "$SKIP_REAL" = "0" ] && [ -d "$DSH_APP" ]; then
    ELECTRON_RUN_AS_NODE=1 \
    DSH_TOOLS_MODULE="$DSH_APP/Contents/Resources/app.asar/dsh/node_modules/@deepseek-ai/dsh-tools/lib/index.js" \
    "$DSH_APP/Contents/MacOS/DeepSeek Harness" "$PROBE" ./dsh/index.mjs --real 2>&1 | tail -2
    ok "真实 defineTool 编译通过"
  else
    warn "跳过真实 defineTool 探测（未装 DSH 或 --skip-real）"
  fi
else
  warn "未找到契约探测器 ${PROBE}，跳过"
fi

# ---------------------------------------------------------------- 3. 版本同步
step "3/7 版本同步（6 处清单）"
if [ "$DRY_RUN" = "1" ]; then
  warn "[dry-run] 将把 ${CURRENT_VERSION} → ${NEW_VERSION} 写入 package.json / plugin.json / .claude-plugin / .codex-plugin / __init__.py"
else
  python3 - "$NEW_VERSION" <<'PY'
import json, pathlib, re, sys
new = sys.argv[1]
changed = []

def bump_json(rel):
    p = pathlib.Path(rel)
    if not p.exists(): return
    data = json.loads(p.read_text(encoding="utf-8"))
    if data.get("version") == new: return
    text = p.read_text(encoding="utf-8")
    text2 = re.sub(r'("version"\s*:\s*)"[^"]*"', r'\1"%s"' % new, text, count=1)
    p.write_text(text2, encoding="utf-8")
    changed.append(rel)

for rel in ["package.json", "plugin.json", ".claude-plugin/plugin.json", ".codex-plugin/plugin.json"]:
    bump_json(rel)

py = pathlib.Path("rag_eval/__init__.py")
if py.exists():
    t = py.read_text(encoding="utf-8")
    t2 = re.sub(r'__version__\s*=\s*"[^"]*"', f'__version__ = "{new}"', t, count=1)
    if t2 != t:
        py.write_text(t2, encoding="utf-8"); changed.append("rag_eval/__init__.py")

mc = pathlib.Path("mcp_config.json")
if mc.exists():
    t = mc.read_text(encoding="utf-8")
    t2 = re.sub(r'"RAG_EVAL_VERSION"\s*:\s*"[^"]*"', f'"RAG_EVAL_VERSION": "{new}"', t)
    if t2 != t:
        mc.write_text(t2, encoding="utf-8"); changed.append("mcp_config.json")

print("  updated: " + (", ".join(changed) if changed else "(已是目标版本)"))
PY
  ok "版本号统一为 ${NEW_VERSION}"
fi

# ---------------------------------------------------------------- 4. G3 产物闸
step "4/7 G3 产物闸：打包 + 解包验收"
PKG_DIR="$(mktemp -d "${TMPDIR:-/tmp}/rag-eval-release.XXXXXX")"
trap 'rm -rf "$PKG_DIR"' EXIT

if [ "$DRY_RUN" = "1" ]; then
  npm pack --dry-run 2>&1 | grep -E "package size|total files|version:" | sed 's/^/  /' || true
  warn "[dry-run] 跳过解包冒烟"
else
  npm pack --pack-destination "$PKG_DIR" >/dev/null
  TARBALL="$(ls "$PKG_DIR"/*.tgz | head -1)"
  BASENAME="$(basename "$TARBALL")"
  mkdir -p "$ROOT/dist"
  cp "$TARBALL" "$ROOT/dist/$BASENAME"
  TARBALL="$ROOT/dist/$BASENAME"
  ok "打包: dist/${BASENAME} ($(du -h "$TARBALL" | cut -f1), shasum $(shasum -a 1 "$TARBALL" | cut -c1-12)…)"

  tar xzf "$TARBALL" -C "$PKG_DIR"
  PKG_ROOT="$PKG_DIR/package"

  if tar tzf "$TARBALL" | grep -Eq '\.pyc$|/Users/|node_modules/'; then
    tar tzf "$TARBALL" | grep -E '\.pyc$|/Users/|node_modules/' | head -5 | sed 's/^/    /'
    die "包内混入 pyc / 绝对路径 / node_modules"
  fi
  ok "包内无 pyc、无本机绝对路径"

  ( cd "$PKG_ROOT" && python3 scripts/install.py --verify 2>&1 | grep -c "✅" | sed 's/^/  清单校验通过项: /' )
  ( cd "$PKG_ROOT" && python3 -m rag_eval demo --workdir "$PKG_DIR/.demo" --max-cases 8 2>&1 | grep -E "综合评级|峰值内存" | sed 's/^/  /' )
  ok "解包后冒烟通过（install --verify + demo）"
fi

# ---------------------------------------------------------------- 5. 发布
step "5/7 npm 发布"
if [ "$SKIP_PUBLISH" = "1" ] || [ "$DRY_RUN" = "1" ]; then
  if [ "$DRY_RUN" = "1" ]; then warn "跳过发布（dry-run）"; else warn "跳过发布（--skip-publish）"; fi
else
  PUBLISH_ARGS=(publish --access public)
  [ -n "$OTP" ] && PUBLISH_ARGS+=(--otp "$OTP")
  if [ -z "$OTP" ]; then
    warn "未提供 --otp，npm 会提示 One-time password（或走浏览器 2FA 授权）"
  fi
  "${PUBLISH_ARGS[@]}" || die "npm publish 失败（看上面的错误码：403=需 2FA，404=token 失效）"

  step "5b/7 等待 registry 生效（异步，最长 180s）"
  for i in $(seq 1 36); do
    LIVE="$(curl -fsS "https://registry.npmjs.org/${PKG_NAME}" 2>/dev/null \
      | python3 -c "import sys,json;print(json.load(sys.stdin).get('dist-tags',{}).get('latest',''))" 2>/dev/null || true)"
    if [ "$LIVE" = "$NEW_VERSION" ]; then
      ok "registry latest = ${NEW_VERSION}（${i}0 秒内）"
      break
    fi
    [ "$i" = "36" ] && warn "等待 180s 后 latest 仍是 '${LIVE}'；稍后手动核对：npm view ${PKG_NAME} dist-tags"
    sleep 5
  done
fi

# ---------------------------------------------------------------- 6. Git
step "6/7 提交 / tag / 推送"
if [ "$DRY_RUN" = "1" ] || [ "$SKIP_GIT" = "1" ]; then
  warn "跳过 git 操作"
else
  git add -A
  if git diff --cached --quiet; then
    warn "没有版本变更可提交（可能已是目标版本）"
  else
    git commit -q -m "chore(release): ${NEW_VERSION}"
    ok "commit: chore(release): ${NEW_VERSION}"
  fi
  git tag -a "$TAG" -m "$TAG"
  git push -q origin HEAD --tags
  ok "已推送 ${TAG}"
fi

# ---------------------------------------------------------------- 7. GitHub Release
step "7/7 GitHub Release"
if [ "$DRY_RUN" = "1" ] || [ "$SKIP_GIT" = "1" ]; then
  warn "跳过 gh release"
elif ! command -v gh >/dev/null 2>&1; then
  warn "未安装 gh CLI，跳过 release 创建"
else
  NOTES_TEXT="$NOTES"
  if [ -z "$NOTES_TEXT" ]; then
    NOTES_TEXT="$(python3 - "$NEW_VERSION" <<'PY'
import re, sys
ver = sys.argv[1]
text = open("CHANGELOG.md", encoding="utf-8").read()
m = re.search(rf"^## \[{re.escape(ver)}\][^\n]*\n(.*?)(?=^## |\Z)", text, re.S | re.M)
print((m.group(1).strip() if m else f"Release {ver}")[:6000])
PY
)"
  fi
  if gh release view "$TAG" >/dev/null 2>&1; then
    warn "release ${TAG} 已存在，跳过"
  else
    printf '%s' "$NOTES_TEXT" > "$PKG_DIR/notes.md"
    gh release create "$TAG" --title "$TAG" --notes-file "$PKG_DIR/notes.md" >/dev/null
    ok "release: $(gh release view "$TAG" --json url --jq .url 2>/dev/null || echo "$TAG")"
  fi
fi

# ---------------------------------------------------------------- 汇总
printf '\n%s══════════ 发版完成 ══════════%s\n' "$GREEN$BOLD" "$NC"
printf '  包名      : %s\n' "$PKG_NAME"
printf '  版本      : %s → %s\n' "$CURRENT_VERSION" "$NEW_VERSION"
printf '  本地 tag  : %s\n' "$TAG"
printf '  产物      : %s\n' "${TARBALL:-（dry-run 未打包）}"
printf '  安装      : npm i %s@%s\n' "$PKG_NAME" "$NEW_VERSION"
printf '  验收      : mkdir -p /tmp/accept && cd /tmp/accept && npm i %s@%s --prefer-online\n' "$PKG_NAME" "$NEW_VERSION"
printf '\n'
