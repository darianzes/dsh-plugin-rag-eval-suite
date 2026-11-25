---
name: dsh-plugin-release
description: >-
  Package, verify and publish DeepSeek Harness (dsh) Cordis plugins as installable bundles:
  bundle/composition contract (cordis.patch.yml insert dialect, locale, icon), tool-definition
  conformance (defineTool property-map schema, explicit additionalProperties, array inject),
  npm packaging hygiene (files whitelist, prepack cache purge, no absolute paths), version
  matrix sync, tarball-install acceptance and the OTP/2FA publish flow. Use when turning a
  plugin that "runs locally" into one that installs from npm on every harness without regressions.
---

# DSH 插件工程化发布（从「本地能跑」到「别人能装」）

本 Skill 沉淀把 **DeepSeek Harness Cordis 插件**做成可发布产物的完整工程方法：契约、打包、版本、发布、验收、诊断。
适用于自研插件复用（RAG Eval Suite 就是按这套流程做到 `npm i` → 四端可装 → CI 绿）。

---

## 0. 三道闸门（缺一不可，任何一道红就别发）

| 闸门 | 命令 | 失败含义 |
|---|---|---|
| **G1 逻辑** | `python3 -m unittest discover -s tests -v`（或项目等价测试） | 代码本身坏了 |
| **G2 契约** | `node --check dsh/index.mjs` + 真实 `defineTool` 探测 + 真实 Cordis 注册探测 | DSH 装得上但工具注册不上（**最隐蔽**） |
| **G3 产物** | `npm pack` → 解包 → `install.py --verify` → 冒烟 demo/MCP 握手 | 包能装但跑不起来（缺文件、绝对路径、pyc） |

> 只跑 G1 就发版，是本类插件最常见的翻车方式：本地 `link:` 安装永远用源码，天然掩盖所有打包问题。

---

## 1. Bundle 契约（package.json）

```jsonc
{
  "type": "module",
  "main": "dsh/index.mjs",
  "icon": "./assets/logo.svg",
  "exports": { ".": { "import": "./dsh/index.mjs" }, "./icon": "./assets/logo.svg", "./locale/*": "./locale/*.json" },
  "files": ["dsh", "rag_eval", "skills", "scripts", "assets", "locale", "cordis.patch.yml", "plugin.json", "README.md", "LICENSE"],
  "dsh": {
    "bundle": { "patch": "./cordis.patch.yml" },
    "skills": ["skills/<skill-a>", "skills/<skill-b>"],
    "mcp": "mcp.json"
  },
  "peerDependencies": { "@deepseek-ai/cordis": ">=0.5.0", "@deepseek-ai/dsh": ">=0.2.0", "@deepseek-ai/dsh-tools": ">=0.1.0" },
  "peerDependenciesMeta": { "@deepseek-ai/dsh-tools": { "optional": true } }
}
```

要点：

- `files` 是**白名单**，`.gitignore` 对它**无效**——`__pycache__`、`.env`、调试脚本必须靠 `prepack` 或显式排除清掉。
- `cordis.patch.yml` 只有 **insert 方言**，按 `id + name` 声明：
  ```yaml
  - insert:
      - id: my-plugin
        name: my-plugin-package-name      # 必须是 npm 包名
  ```
  非 insert 形态、或 `target` 匹配不到行，Loader 只警告后**跳过**（插件静默不加载）。
- `dsh.mcp` 指向的 `mcp.json` 必须**真的在包里**（`files` 里要有），否则 stdio 备用通道断裂。
- 可选但强烈建议：`locale/en.json`、`locale/zh.json`（`meta.title` / `meta.description`）、`assets/logo.svg`。

---

## 2. 工具面契约（最容易踩的坑）

```js
import { defineTool } from '@deepseek-ai/dsh-tools';

export const inject = ['tools'];        // 必须是数组；对象形式会被当成"依赖名为 optional 的服务"

export function apply(ctx, config) {
  ctx.inject(['tools'], (tools) => {
    for (const spec of buildToolSpecs(config)) {
      tools.register(defineTool({
        name: spec.name,
        description: spec.description,
        parameters: spec.parameters,     // 属性映射 DSL，不是 JSON Schema 对象根
        output: {
          schema: { type: 'object', additionalProperties: true },   // 必须显式布尔值
          render: (_args, value) => JSON.stringify(value, null, 2),
        },
        execute: spec.execute,
      }));
    }
  });
}
```

硬性规则（违反即 `authorError`，而且是**定义期**抛错，注册循环整体失败→工具一个都没有）：

1. 每个 `object` 类型（含 `output.schema` 根、含嵌套参数）都必须写出 `additionalProperties: true|false`，缺省不合法。
2. `parameters` 是「参数名 → `{ type, required: true, description }`」的属性映射；类型只允许 `string | number | integer | boolean | null | array | object | json | oneOf`。
3. `defineTool` 必填 `name`、`description`、`parameters`、`output`；`output` 必须同时有 `schema` 与 `render(args, value)`。
4. `inject` 用数组；需要 MCP 时再 `ctx.inject(['mcp'], …)`，未安装 MCP 服务会静默跳过。
5. 每个工具 `execute` 自行兜底错误：把 SDK 抛出转成结构化 `{ error }`，避免把异常打到宿主 UI。

**G2 探测（不要靠肉眼）**：

```bash
# 离线契约校验（无需 DSH 环境，CI 可用）
node skills/dsh-plugin-release/scripts/probe-define-tools.mjs ./dsh/index.mjs

# 最终判定：用 DSH 自带的 defineTool 真编译一遍
# 注意：依赖在 app.asar 里，普通 node 读不到，必须用 Electron 的 node 模式
ELECTRON_RUN_AS_NODE=1 \
DSH_TOOLS_MODULE="/Applications/DeepSeek Harness.app/Contents/Resources/app.asar/dsh/node_modules/@deepseek-ai/dsh-tools/lib/index.js" \
"/Applications/DeepSeek Harness.app/Contents/MacOS/DeepSeek Harness" \
  skills/dsh-plugin-release/scripts/probe-define-tools.mjs ./dsh/index.mjs --real
```

探测必须同时覆盖：`buildToolSpecs()` 数量（与 MCP `tools/list` 对齐）、每个 spec 的 `parameters` 逐层 `additionalProperties`、`output.schema`、`inject` 形态。

---

## 3. 打包卫生

```jsonc
"scripts": {
  "prepack": "python3 -c \"import pathlib,shutil; [shutil.rmtree(p) for p in pathlib.Path('.').rglob('__pycache__'))]\""
}
```

发版前自查：

```bash
npm pack --dry-run 2>&1 | tail -20                     # 文件数/体积是否符合预期
tar tzf *.tgz | grep -E "\.pyc$|\.env|/Users/|node_modules"   # 期望：无输出
grep -rn "/Users/\|/home/" --include="*.json" --include="*.yml" . | grep -v node_modules   # 绝对路径
```

必须入包的东西（漏了会在**别人机器上**才炸）：

- `examples/`（README 与 demo 命令依赖的示例语料）
- `.mcp.json` / `.claude-plugin/` / `.codex-plugin/` / `commands/` / `rules/`（跨端清单）
- `LICENSE`、`CHANGELOG.md`、`README.md`
- `scripts/run_mcp.sh` 等运行期脚本（用 `SCRIPT_DIR` 自推导路径，保证可搬迁）

**绝对路径禁令**：`mcp_config.json` 一类被客户端读取的配置里不要写死本机路径；改成相对引用（`bash ./scripts/run_mcp.sh`），安装时由安装脚本按 `target` 本地化。

---

## 4. 版本同步矩阵

一次发版必须同步（漏一个，npm 页面/README/插件市场就会互相打脸）：

| 位置 | 字段 |
|---|---|
| `package.json` | `version` |
| `plugin.json` | `version` |
| `.claude-plugin/plugin.json` | `version` |
| `.codex-plugin/plugin.json` | `version` |
| `rag_eval/__init__.py`（或包 `__version__`） | `__version__` |
| `mcp_config.json` / MCP 服务握手 | 版本常量（若存在） |
| `CHANGELOG.md` | 新版本段落（Keep a Changelog） |

`scripts/release.sh` 会一次性完成同步 + 校验（见 §5）。

---

## 5. 发布流水线（一键）

```bash
./scripts/release.sh 0.3.0 --dry-run     # 演练：只打印将执行的步骤
./scripts/release.sh 0.3.0               # 全流程：闸门 → bump → pack → 冒烟 → publish → tag → release
./scripts/release.sh patch               # 语义化自增
./scripts/release.sh 0.3.0 --skip-publish # 只准备产物与提交，稍后手动发
```

脚本步骤固定为：**前置检查（分支/干净/登录态）→ G1 → G2 → 同步版本 → G3（pack + 解包 + 冒烟）→ `npm publish`（OTP + 轮询生效）→ commit/tag/push → `gh release create`**。
任何一步非零退出即中止，不回滚已 push 的 tag（需人工 `git tag -d` + 远端删除）。

手工等价命令：

```bash
python3 -m unittest discover -s tests -v
node --check dsh/index.mjs
npm pack && tar xzf *.tgz -C /tmp/pkgcheck && (cd /tmp/pkgcheck/package && python3 scripts/install.py --verify)
npm publish --access public --otp=<6位码>
npm view <pkg> dist-tags --json          # 直到出现新版本号（异步，见 §6）
git add -A && git commit -m "chore(release): <ver>" && git tag -a v<ver> -m v<ver> && git push origin main --tags
gh release create v<ver> --title "v<ver>" --notes-file CHANGELOG.md
```

---

## 6. 诊断速查（症状 → 处置）

| 症状 | 根因 | 处置 |
|---|---|---|
| `npm view` 只有旧版本 / 出现 `0.0.0-stage` | 新包首发的占位版本 + 客户端元数据缓存 | `--prefer-online --cache /tmp/fresh`；等 1–5 分钟 |
| `npm publish` 返回 **202** 后版本不出现 | npm 异步处理（正常） | 轮询 `registry/<pkg>` 的 `dist-tags`，约 2 分钟收敛 |
| `E403 ... 2FA ... required to publish` | npm 2026 政策：发布必须 2FA；bypass granular token 只能 stage 不能直发 | 账号开 2FA，publish 时输 OTP；或走 `npm stage approve`（npm ≥11.5） |
| `E404 Not Found - PUT ...` | `~/.npmrc` 里 token 失效（真实错误被掩盖成 404） | `npm whoami` 复验 → 注释失效行 → `npm login` |
| 插件装上了但一个工具都没有 | `defineTool` 定义期抛错（`additionalProperties` 缺失/`output` 缺失），或 `inject` 写成对象 | 跑 G2 探测；看 DSH 日志里的 `authorError` |
| 插件装上了但完全不加载 | `cordis.patch.yml` 方言不对 / `name` 不是 npm 包名 | 改成 `- insert: [{ id, name }]` |
| `plugin_manager file:<tgz>` 装了还是旧代码 | pnpm 把 `file:` 解析成 `link:` 到源目录 | 卸载后清 `node_modules` 里残留软链，再 `install_bundle file:<abs path>`；用 `link:`（开发态）与 `file:`（验收态）明确区分 |
| 别人 `npm i` 后 demo 直接失败（提示阻断性未知项） | `examples/` 没进包，demo 找不到语料 | 把示例语料加进 `files` |
| 包里混入 `__pycache__` / 本机路径 | `files` 白名单不受 `.gitignore` 约束 | `prepack` 清理 + 发版前 `tar tzf | grep` 自查 |

---

## 7. 发布后验收（别人视角，必须做）

```bash
mkdir -p /tmp/accept && cd /tmp/accept && npm init -y >/dev/null
npm i <pkg>@<ver> --cache /tmp/accept-cache --prefer-online
node -e "console.log(require('<pkg>/package.json').version)"
python3 node_modules/<pkg>/scripts/install.py --verify        # 清单齐全
python3 -m rag_eval demo --workdir /tmp/accept/.demo --max-cases 8   # 或项目等价冒烟
```

DSH 侧：`plugin_manager install_bundle file:/abs/<pkg>-<ver>.tgz` → 确认装成**实体目录**（非软链）→ 工具表出现全部工具 → 验完恢复 `link:` 开发安装。

---

## 参考

- `references/release-checklist.md` — 发版前/中/后逐项清单
- `references/pitfalls.md` — 20 条真实踩坑与对应命令
- `scripts/probe-define-tools.mjs` — G2 契约探测器（可接真实 `defineTool`）

> 经验值：本类插件 80% 的"发布失败"不是业务逻辑问题，而是**契约**（工具定义）与**产物**（打包清单）问题。把 G2/G3 变成脚本，不要在发版当天靠记忆。
