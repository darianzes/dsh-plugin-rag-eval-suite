# 发版清单（逐项勾选，别跳步）

> 一条命令跑完：`./scripts/release.sh <version>`
> 手工发版时按本清单逐项确认。任何一项不确定 = 不发。

## 0. 前置

- [ ] 工作区干净：`git status --short` 无输出
- [ ] 在 `main` 分支，且已 `git pull --ff-only`
- [ ] `npm whoami` 返回账号；`gh auth status` 已登录
- [ ] 已决定版本号（语义化：修 bug → patch，加功能 → minor，破坏性 → major）
- [ ] `CHANGELOG.md` 已写好本节内容（脚本会校验该段落存在）

## 1. G1 逻辑闸

- [ ] `python3 -m unittest discover -s tests -v` 全绿
- [ ] （可选）`python3 -m rag_eval demo --workdir /tmp/g1demo --max-cases 8` 端到端跑通

## 2. G2 契约闸

- [ ] `node --check dsh/index.mjs`
- [ ] `node skills/dsh-plugin-release/scripts/probe-define-tools.mjs ./dsh/index.mjs`
- [ ] 真实 defineTool（有 DSH 环境时必做）：
  ```bash
  ELECTRON_RUN_AS_NODE=1 \
  DSH_TOOLS_MODULE="/Applications/DeepSeek Harness.app/Contents/Resources/app.asar/dsh/node_modules/@deepseek-ai/dsh-tools/lib/index.js" \
  "/Applications/DeepSeek Harness.app/Contents/MacOS/DeepSeek Harness" \
    skills/dsh-plugin-release/scripts/probe-define-tools.mjs ./dsh/index.mjs --real
  ```
- [ ] DSH 工具数量 == MCP `tools/list` 数量 == `buildToolSpecs()` 数量

## 3. 版本同步

- [ ] `package.json` `version`
- [ ] `plugin.json` `version`
- [ ] `.claude-plugin/plugin.json` `version`
- [ ] `.codex-plugin/plugin.json` `version`
- [ ] 包 `__version__`（如 `rag_eval/__init__.py`）
- [ ] `mcp_config.json` 内版本常量（若存在）
- [ ] `CHANGELOG.md` 有 `## [x.y.z] - YYYY-MM-DD`

## 4. G3 产物闸

- [ ] `npm pack` 成功，文件数符合预期
- [ ] `tar tzf *.tgz | grep -E '\.pyc$|\.env$|/Users/|node_modules'` → 无输出
- [ ] 解包到临时目录后：`python3 scripts/install.py --verify` 全 ✅
- [ ] 解包目录里跑冒烟：`python3 -m rag_eval demo --max-cases 8`
- [ ] `examples/`、`mcp.json`、`LICENSE`、`CHANGELOG.md` 均在包内

## 5. 发布

- [ ] `npm publish --access public`（输入 OTP）
- [ ] 轮询生效：`curl -s https://registry.npmjs.org/<pkg> | python3 -c "...dist-tags..."` 出现新版本（约 2 分钟）
- [ ] `npm view <pkg> dist-tags --json` → `latest` 指向新版本
- [ ] `npm view <pkg> repository.url` → 指向 GitHub（npm 页面会显示源码链接）

## 6. Git 与 Release

- [ ] `git add -A && git commit -m "chore(release): <ver>"`
- [ ] `git tag -a v<ver> -m v<ver>`
- [ ] `git push origin main --tags`
- [ ] `gh release create v<ver> --title "v<ver>" --notes-file <notes>`
- [ ] README 徽章/安装命令里的版本号是否需要更新

## 7. 发布后验收（别人视角）

- [ ] 空目录 + 空缓存 `npm i <pkg>@<ver> --prefer-online`
- [ ] 装出来的目录 `install.py --verify` 全 ✅，demo 跑通
- [ ] DSH 侧：`install_bundle file:<abs>/<pkg>-<ver>.tgz` → 实体目录 + 工具齐全
- [ ] 验完恢复 `install_bundle link:<repo root>`
- [ ] 在 issue 里记一条验收结论（含 shasum 与本机路径），方便回溯

## 8. 回滚（万一）

```bash
npm unpublish <pkg>@<ver>            # 72 小时内且无依赖；超时只能发新版本修
npm dist-tag add <pkg>@<prev> latest # 仅回退 latest 指向
git tag -d v<ver> && git push origin :refs/tags/v<ver>
```

> 原则：**宁可发 0.2.3 修，也不要 unpublish**（unpublish 会造成下游 lockfile 失效）。
