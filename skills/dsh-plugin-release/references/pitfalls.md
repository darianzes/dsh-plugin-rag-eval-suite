# 发布踩坑与诊断手册（DSH 插件 / npm）

> 全部来自真实发版过程（RAG Eval Suite 0.2.1 → 0.2.2）。症状 → 根因 → 可直接复制的处置命令。

## A. 装载层（插件装了但没生效）

| # | 症状 | 根因 | 处置 |
|---|---|---|---|
| A1 | `install_bundle` 返回 applied，但 DSH 里看不到任何工具 | `defineTool` **定义期**抛错，注册循环整体失败 | 跑 G2 探测；DSH 日志搜 `authorError` |
| A2 | 报 `schema.additionalProperties must be explicitly true or false` | JSON Schema 规格里 `object` 没写 `additionalProperties` 布尔值（`output.schema` 根和嵌套参数都要写） | `{ type: 'object', additionalProperties: true, properties: {...} }` |
| A3 | 报 `parameters.<x>.additionalProperties ...` | 某个 object 类型参数同理 | 同上，逐层补齐 |
| A4 | 报 `output is required` / `output.schema is required` | `defineTool` 必填 `output: { schema, render }` | 补 `render(args, value)`，别只给 schema |
| A5 | 插件完全不加载，无报错，仅 Loader 警告 | `cordis.patch.yml` 不是 `insert` 方言，或 `name` 不是 npm 包名 | 改成 `- insert: [{ id, name: <package-name> }]` |
| A6 | `apply` 里的注册代码整段被跳过 | `export const inject = { optional: [...] }` 被 Cordis 当成「依赖名为 optional 的服务」 | `export const inject = ['tools'];`（数组） |
| A7 | 想找 `registry.defineTool` 但不存在 | 该 API 不存在；正确写法是 `ctx.tools.register(defineTool({...}))` | 从 `@deepseek-ai/dsh-tools` import `defineTool` |
| A8 | 自己写的 ESM 检查脚本永远"通过" | 用 `defineFn = x => x` 之类的假实现兜底，把错误吞了 | 探测必须用真实 `defineTool`；假实现只能做形状检查 |

## B. 打包层（包能装但跑不起来）

| # | 症状 | 根因 | 处置 |
|---|---|---|---|
| B1 | 用户 `npm i` 后 demo 直接失败（提示阻断性未知项） | `examples/` 没进 `files`，`cmd_demo` 找不到示例语料 | 把 `examples/sample_kb` 加进 `files` |
| B2 | 包里混进 `__pycache__/*.pyc` | `files` 白名单**不受** `.gitignore` 约束 | `"prepack": "python3 -c \"...rglob('__pycache__')...\""` + 发版前 `tar tzf \| grep '\.pyc$'` |
| B3 | 别人机器上 MCP 起不来 | `dsh.mcp` 指向的 `mcp.json` 本身没打包 | `files` 里加 `mcp.json`、`.mcp.json` |
| B4 | 客户端找不到 MCP 启动脚本 | 配置里写了本机绝对路径 | 改相对路径 `bash ./scripts/run_mcp.sh`；安装脚本按 target 本地化 |
| B5 | 跨端清单缺失（Claude/Codex 装不上） | `.claude-plugin/`、`.codex-plugin/`、`commands/`、`rules/` 未列入 `files` | 逐项补 + `install.py --verify` 兜底 |
| B6 | 包装完发现少文件 | 从未解包核对 | `npm pack` → `tar xzf -C /tmp/pkgcheck` → 在那个目录跑 `install.py --verify` |

## C. 安装路径（开发态 vs 验收态）

| # | 症状 | 根因 | 处置 |
|---|---|---|---|
| C1 | 改了代码立刻生效，以为打包没问题 | `link:` 安装指向源目录，天然掩盖打包缺陷 | 发版必须做 **tarball 真装**（`file:<abs>.tgz`） |
| C2 | `install_bundle file:<tgz>` 后还是旧代码 | pnpm lockfile 把 `file:` 解析成 `link:../../../<src>` | `remove_bundle` → 删 `node_modules/<pkg>` 残留软链 → 重新 `install_bundle file:<abs path>` |
| C3 | 装完 `node_modules/<pkg>` 是软链但以为是实体 | 同上，未清 stale symlink | `ls -l node_modules/<pkg>`；验收要求**实体目录** |
| C4 | 验完忘了切回开发安装 | 手工流程遗漏 | 收尾固定执行 `install_bundle link:<repo root>` |

## D. npm 发布层

| # | 症状 | 根因 | 处置 |
|---|---|---|---|
| D1 | `E404 Not Found - PUT https://registry.npmjs.org/<pkg>` | `~/.npmrc` 里 token 失效（npm 把 401 掩盖成 404） | `npm whoami` 复验 → 备份并注释失效行 → `npm login` |
| D2 | `E403 ... 2FA ... required to publish` | npm 2026 政策：发布必须 2FA；bypass granular token 只能 stage 不能直发 | 账号开 2FA，publish 输 OTP；或 `npm stage approve`（npm ≥11.5） |
| D3 | publish 返回 **202** 后版本迟迟不出现 | 服务端异步处理（正常路径） | 轮询 `curl -s https://registry.npmjs.org/<pkg>` 的 `dist-tags`；实测约 2 分钟 |
| D4 | `npm view version` 显示 `0.0.0-stage` | 新包首发占位版本 + 客户端元数据缓存 | 无害；用 `--prefer-online --cache /tmp/fresh` 复核 |
| D5 | `npm i` 报 `notarget` | 同 D4 的缓存视图 | 同上；或直接锁定版本号安装 |
| D6 | 发完才发现 `repository` 未填、页面没有源码链接 | 元数据没进 `package.json`（需重新发版才能改） | 补 `repository`/`homepage`/`bugs` 后 bump 补丁版本重发 |
| D7 | 想改 CHANGELOG 后再修 tag | tag 已推送 | `git tag -d vX` → 重新打 → `git push -f origin refs/tags/vX`（仅限未发布/刚发布时） |

## E. 环境与工具

| # | 症状 | 根因 | 处置 |
|---|---|---|---|
| E1 | 读 DSH 安装目录里的模块报 `Cannot find module ... app.asar ...` | asar 是虚拟归档，普通 node 无法读取 | `ELECTRON_RUN_AS_NODE=1 <DSH 可执行文件> <script>` |
| E2 | `wp`/`ls` 打不开 app.asar 下文件 | 同上 | 用 read 工具读取文件内容，不要用 shell 列目录 |
| E3 | CI 里 `node:20` action 出现 deprecation 注解 | GitHub 强制 action 运行时升级 | 无害；需要消除就把 workflow 的 `node-version` 提上去 |
| E4 | 本地跑得通、CI 里拒答判定不同 | 测试依赖本机时区/文件排序 | 固定排序（`sorted()`）、固定时区 |

## 一句话原则

> **工具定义（G2）与打包清单（G3）必须由脚本证明，不能靠"我本地是好的"。**
> 本地 `link:` 安装是最强的"假阳性制造机"。
