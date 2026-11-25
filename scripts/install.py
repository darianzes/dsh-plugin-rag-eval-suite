#!/usr/bin/env python3
"""Cross-platform installer & verifier for RAG Eval Suite.

Supports Antigravity, Claude Code, OpenAI Codex, and DeepSeek Harness (dsh).
Never mutates sensitive host config state silently — supports `--dry-run` preview
and writes clean, reversible symlinks or configuration snippets.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Dict, List


PLUGIN_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_NAME = "rag-eval-suite"


def _symlink_or_copy(src: Path, dst: Path, dry_run: bool, copy_mode: bool = False) -> str:
    if dry_run:
        return f"[DRY-RUN] {'copy' if copy_mode else 'symlink'} {dst} -> {src}"
    dst.parent.mkdir(parents=True, exist_ok=True)
    existed = dst.is_symlink() or dst.exists()
    if existed:
        if dst.is_symlink() or dst.is_file():
            dst.unlink()
        else:
            shutil.rmtree(dst)
    if copy_mode:
        shutil.copytree(src, dst, symlinks=True)
        return f"[{'RE-COPIED' if existed else 'COPIED'}] {dst} <- {src}"
    os.symlink(src, dst)
    return f"[{'RELINKED' if existed else 'LINKED'}] {dst} -> {src}"


def _verify_manifests() -> List[str]:
    checks = [
        ("Antigravity manifest", PLUGIN_ROOT / "plugin.json"),
        ("Antigravity MCP config", PLUGIN_ROOT / "mcp_config.json"),
        ("Claude Code manifest", PLUGIN_ROOT / ".claude-plugin" / "plugin.json"),
        ("Claude Code MCP config", PLUGIN_ROOT / ".mcp.json"),
        ("Codex manifest", PLUGIN_ROOT / ".codex-plugin" / "plugin.json"),
        ("Open Agent MCP config", PLUGIN_ROOT / "mcp.json"),
        ("DSH package.json", PLUGIN_ROOT / "package.json"),
        ("DSH Cordis patch", PLUGIN_ROOT / "cordis.patch.yml"),
        ("DSH Cordis adapter", PLUGIN_ROOT / "dsh" / "index.mjs"),
        ("DSH Locale (en)", PLUGIN_ROOT / "locale" / "en.json"),
        ("DSH Locale (zh)", PLUGIN_ROOT / "locale" / "zh.json"),
        ("Plugin Icon SVG", PLUGIN_ROOT / "assets" / "logo.svg"),
        ("Universal SKILL.md", PLUGIN_ROOT / "skills" / PLUGIN_NAME / "SKILL.md"),
        ("Plugin Rules", PLUGIN_ROOT / "rules" / "AGENTS.md"),
        ("MCP Runner Script", PLUGIN_ROOT / "scripts" / "run_mcp.sh"),
    ]
    results: List[str] = []
    missing = 0
    for label, path in checks:
        ok = path.exists()
        if not ok:
            missing += 1
        results.append(f"{'✅' if ok else '❌'} {label}: {path}")
    if missing:
        raise FileNotFoundError(f"Missing {missing} required plugin files.")
    return results


def _write_antigravity_mcp_config(plugin_root: Path, target: Path) -> str:
    """Localize mcp_config.json with an absolute launcher path (best effort).

    The shipped manifest is relocatable (relative ./scripts/run_mcp.sh); Antigravity
    launches MCP servers from an unpredictable cwd, so a copied install gets the
    absolute path pinned here. Symlinked installs must not mutate their source tree,
    so they only get a printed hint instead.
    """
    try:
        same_tree = target.resolve() == plugin_root.resolve()
    except OSError:
        same_tree = True
    if same_tree:
        return (
            "提示: 当前为 symlink 安装（不修改源目录）。若客户端需要绝对路径，可手动把 "
            f"{plugin_root / 'mcp_config.json'} 中的 args 改为 \"{plugin_root / 'scripts' / 'run_mcp.sh'}\"。"
        )
    cfg = {
        "mcpServers": {
            PLUGIN_NAME: {
                "command": "bash",
                "args": [str(target / "scripts" / "run_mcp.sh")],
                "env": {"PYTHONPATH": str(target), "PYTHONUNBUFFERED": "1"},
            }
        }
    }
    try:
        (target / "mcp_config.json").write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return f"[LOCALIZED] {target / 'mcp_config.json'} (absolute launcher path)"
    except OSError as exc:  # read-only install dir (global npm prefix)
        return f"[SKIP] 无法写入 {target / 'mcp_config.json'}: {exc}"


def install_antigravity(dry_run: bool, copy_mode: bool) -> List[str]:
    home = Path.home()
    target = home / ".gemini" / "config" / "plugins" / PLUGIN_NAME
    logs = [_symlink_or_copy(PLUGIN_ROOT, target, dry_run=dry_run, copy_mode=copy_mode)]
    if not dry_run:
        logs.append(_write_antigravity_mcp_config(PLUGIN_ROOT, target))
    logs.append(
        "提示 (Antigravity): 插件会自动被 Antigravity 发现，可在 Customizations 页面查看/启停（不会修改 config.json）。"
    )
    return logs


def install_claude(dry_run: bool, copy_mode: bool) -> List[str]:
    home = Path.home()
    skill_target = home / ".claude" / "skills" / PLUGIN_NAME
    logs = [_symlink_or_copy(PLUGIN_ROOT / "skills" / PLUGIN_NAME, skill_target, dry_run=dry_run, copy_mode=copy_mode)]
    logs.append(
        f"提示 (Claude Code): 也可直接以完整插件模式加载：`claude --plugin-dir {PLUGIN_ROOT}` 或 `/plugin install {PLUGIN_ROOT}`。"
    )
    return logs


def install_codex(dry_run: bool, copy_mode: bool) -> List[str]:
    home = Path.home()
    codex_home = Path(os.environ.get("CODEX_HOME", str(home / ".codex")))
    shared_skill = home / ".agents" / "skills" / PLUGIN_NAME
    codex_skill = codex_home / "skills" / PLUGIN_NAME
    logs = [
        _symlink_or_copy(PLUGIN_ROOT / "skills" / PLUGIN_NAME, shared_skill, dry_run=dry_run, copy_mode=copy_mode),
        _symlink_or_copy(PLUGIN_ROOT / "skills" / PLUGIN_NAME, codex_skill, dry_run=dry_run, copy_mode=copy_mode),
        f"提示 (Codex MCP): 在 ~/.codex/config.toml 中添加 MCP server：\n"
        f"  [mcp_servers.rag-eval-suite]\n"
        f'  command = "{PLUGIN_ROOT / "scripts" / "run_mcp.sh"}"',
    ]
    return logs


def install_dsh(dry_run: bool, copy_mode: bool) -> List[str]:
    home = Path.home()
    shared_skill = home / ".agents" / "skills" / PLUGIN_NAME
    logs = [
        _symlink_or_copy(PLUGIN_ROOT / "skills" / PLUGIN_NAME, shared_skill, dry_run=dry_run, copy_mode=copy_mode),
        f"提示 (DeepSeek Harness / dsh): 已满足标准 DSH Bundle 规范，推荐按 Bundle 安装：\n"
        f"  1) 标准 Bundle 安装（推荐）：在 DSH Plugin Manager 调用 `plugin_manager` (`install_bundle`) 指定目录 `{PLUGIN_ROOT}`\n"
        f"  2) 本地开发调试挂载：`npx @deepseek-ai/dsh --patch {PLUGIN_ROOT / 'cordis.patch.yml'}`\n"
        f"  3) 备选 Stdio MCP 接入：`command={PLUGIN_ROOT / 'scripts' / 'run_mcp.sh'}`",
    ]
    return logs


def main() -> int:
    parser = argparse.ArgumentParser(description="Install RAG Eval Suite across Antigravity, Claude Code, Codex, and DSH.")
    parser.add_argument(
        "--target",
        default="all",
        choices=["antigravity", "claude", "codex", "dsh", "all"],
        help="Target harness platform",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without modifying filesystem")
    parser.add_argument("--copy", action="store_true", help="Copy files instead of creating symlinks")
    parser.add_argument("--verify", action="store_true", help="Verify all multi-platform plugin manifests and exit")
    args = parser.parse_args()

    print("=== RAG Eval Suite Manifest Verification ===")
    for line in _verify_manifests():
        print(" ", line)
    if args.verify:
        return 0

    targets = ["antigravity", "claude", "codex", "dsh"] if args.target == "all" else [args.target]
    handlers = {
        "antigravity": install_antigravity,
        "claude": install_claude,
        "codex": install_codex,
        "dsh": install_dsh,
    }

    print(f"\n=== Installing for: {', '.join(targets)} (dry_run={args.dry_run}) ===")
    for t in targets:
        print(f"\n[{t.upper()}]")
        for line in handlers[t](dry_run=args.dry_run, copy_mode=args.copy):
            print(" ", line)

    return 0


if __name__ == "__main__":
    sys.exit(main())
