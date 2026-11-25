#!/usr/bin/env node
/**
 * G2 契约探测器 —— 在发布前证明「DSH 真的能注册这些工具」。
 *
 * 用法：
 *   node probe-define-tools.mjs ./dsh/index.mjs
 *   node probe-define-tools.mjs ./dsh/index.mjs --json
 *   DSH_TOOLS_MODULE="<dsh>/node_modules/@deepseek-ai/dsh-tools/lib/index.js" \
 *     node probe-define-tools.mjs ./dsh/index.mjs --real
 *
 * 检查项（任何一项失败都会让 DSH 在 apply/定义期抛 authorError，工具一个都注册不上）：
 *   1. export const inject 必须是数组（对象形式会被 Cordis 当作「依赖名为 optional 的服务」）
 *   2. buildToolSpecs() 返回非空、名称唯一
 *   3. 每个参数层级里的 object 类型都必须显式声明 additionalProperties: true|false
 *   4. 每个工具的 output 必须有 schema + render（真实 defineTool 的硬要求）
 *   5. --real 时用 DSH 自带 defineTool 实际编译一遍（最强证据）
 */

import { pathToFileURL } from 'node:url';
import process from 'node:process';

const ALLOWED_TYPES = new Set(['string', 'number', 'integer', 'boolean', 'null', 'array', 'object', 'json', 'oneOf']);

function parseArgs(argv) {
  const args = { entry: null, real: false, json: false, config: {} };
  for (let i = 0; i < argv.length; i += 1) {
    const a = argv[i];
    if (a === '--real') args.real = true;
    else if (a === '--json') args.json = true;
    else if (a === '--config') args.config = JSON.parse(argv[++i] || '{}');
    else if (!a.startsWith('-') && !args.entry) args.entry = a;
  }
  return args;
}

/** 返回该 schema 规格的问题列表（路径 + 原因）。 */
function validateSchemaSpec(spec, path, problems) {
  if (spec == null || typeof spec !== 'object' || Array.isArray(spec)) {
    problems.push(`${path}: 必须是对象规格`);
    return;
  }
  const { type } = spec;
  if (!type) {
    // 允许「只有 properties 的隐式对象根」：parameters 的属性映射本身就是这样
    if (spec.properties) {
      validateProperties(spec.properties, path, problems);
      return;
    }
    problems.push(`${path}: 缺少 type`);
    return;
  }
  if (!ALLOWED_TYPES.has(type)) {
    problems.push(`${path}.type: 非法类型 "${type}"（允许 ${[...ALLOWED_TYPES].join(' | ')}）`);
    return;
  }
  if (type === 'object') {
    if (typeof spec.additionalProperties !== 'boolean') {
      problems.push(`${path}.additionalProperties: 必须是显式 true 或 false（当前 ${JSON.stringify(spec.additionalProperties)}）`);
    }
    if (spec.properties) validateProperties(spec.properties, path, problems);
  }
  if (type === 'array' && spec.items) validateSchemaSpec(spec.items, `${path}.items`, problems);
  if (type === 'oneOf') {
    if (!Array.isArray(spec.oneOf) || spec.oneOf.length === 0) problems.push(`${path}.oneOf: 必须是非空数组`);
    else (spec.oneOf || []).forEach((s, i) => validateSchemaSpec(s, `${path}.oneOf[${i}]`, problems));
  }
  if (spec.required !== undefined && typeof spec.required !== 'boolean' && !Array.isArray(spec.required)) {
    problems.push(`${path}.required: 应为布尔值（属性级）或字符串数组（对象级）`);
  }
}

function validateProperties(props, path, problems) {
  if (props == null || typeof props !== 'object' || Array.isArray(props)) {
    problems.push(`${path}.properties: 必须是「参数名 -> 规格」的映射`);
    return;
  }
  for (const [key, value] of Object.entries(props)) {
    validateSchemaSpec(value, `${path}.${key}`, problems);
  }
}

async function run() {
  const args = parseArgs(process.argv.slice(2));
  const report = { entry: args.entry, pass: false, checks: [], problems: [], specCount: 0, realDefineTool: null };
  const fail = (msg) => {
    report.problems.push(msg);
    report.checks.push({ name: msg, ok: false });
  };
  const ok = (msg, detail) => report.checks.push({ name: msg, ok: true, detail });

  if (!args.entry) {
    fail('用法: node probe-define-tools.mjs <entry.mjs> [--real] [--json]');
    return report;
  }

  let mod;
  try {
    mod = await import(pathToFileURL(args.entry).href);
  } catch (err) {
    fail(`无法 import 入口模块: ${err.message}`);
    return report;
  }

  const injected = mod.inject ?? mod.default?.inject;
  if (Array.isArray(injected)) ok(`inject 形态正确: [${injected.join(', ')}]`);
  else fail(`inject 必须导出为数组，当前为 ${JSON.stringify(injected)}（对象形式会被当作服务名依赖，apply 永不执行）`);

  const applyFn = mod.apply ?? mod.default?.apply;
  if (typeof applyFn === 'function') ok('apply 已导出为函数');
  else fail('apply 未导出或不是函数');

  const builder = mod.buildToolSpecs ?? mod.default?.buildToolSpecs;
  if (typeof builder !== 'function') {
    fail('未导出 buildToolSpecs()，无法做离线契约校验（请在入口暴露纯函数构建器）');
    return report;
  }

  let specs;
  try {
    specs = await builder(args.config ?? {});
  } catch (err) {
    fail(`buildToolSpecs() 抛错: ${err.message}`);
    return report;
  }
  if (!Array.isArray(specs) || specs.length === 0) {
    fail('buildToolSpecs() 未返回非空数组');
    return report;
  }
  report.specCount = specs.length;
  ok(`buildToolSpecs() 返回 ${specs.length} 个工具规格`);

  const seen = new Map();
  const specProblems = [];
  const realFn = args.real ? await loadRealDefineTool(report) : null;

  for (const spec of specs) {
    const name = spec?.name;
    if (typeof name !== 'string' || !name) {
      specProblems.push(`存在缺少 name 的工具规格`);
      continue;
    }
    if (seen.has(name)) specProblems.push(`工具名重复: ${name}`);
    seen.set(name, true);

    if (typeof spec.description !== 'string' || spec.description.length < 10) {
      specProblems.push(`${name}: description 缺失或过短（模型选工具依赖它）`);
    }
    if (spec.parameters == null || typeof spec.parameters !== 'object') {
      specProblems.push(`${name}: 缺少 parameters 属性映射`);
    } else {
      validateProperties(spec.parameters, `${name}.parameters`, specProblems);
    }

    if (spec.output == null) {
      specProblems.push(`${name}: 缺少 output（真实 defineTool 必填 schema + render）`);
    } else {
      if (spec.output.schema == null) specProblems.push(`${name}.output: 缺少 schema`);
      else validateSchemaSpec(spec.output.schema, `${name}.output.schema`, specProblems);
      if (typeof spec.output.render !== 'function') specProblems.push(`${name}.output: 缺少 render(args, value) 函数`);
    }

    if (realFn) {
      try {
        realFn({
          name,
          description: spec.description,
          parameters: spec.parameters,
          output: spec.output ?? { schema: { type: 'object', additionalProperties: true }, render: (_a, v) => JSON.stringify(v) },
          execute: typeof spec.execute === 'function' ? spec.execute : async () => ({}),
        });
      } catch (err) {
        specProblems.push(`${name}: 真实 defineTool 编译失败 -> ${err.message}`);
      }
    }
  }

  if (specProblems.length === 0) {
    ok(`全部 ${specs.length} 个工具通过契约校验${realFn ? '（含真实 defineTool 编译）' : ''}`);
  } else {
    report.problems.push(...specProblems);
    report.checks.push({ name: '工具规格契约', ok: false, detail: specProblems });
  }

  report.pass = report.problems.length === 0;
  return report;
}

async function loadRealDefineTool(report) {
  const modulePath = process.env.DSH_TOOLS_MODULE;
  if (!modulePath) {
    report.realDefineTool = 'skipped: 未设置 DSH_TOOLS_MODULE';
    return null;
  }
  try {
    const mod = await import(pathToFileURL(modulePath).href);
    const defineTool = mod.defineTool ?? mod.default?.defineTool;
    if (typeof defineTool !== 'function') throw new Error('模块里没有导出 defineTool');
    report.realDefineTool = `loaded: ${modulePath}`;
    return defineTool;
  } catch (err) {
    report.problems.push(
      `--real 加载 dsh-tools 失败: ${err.message}\n` +
        '    提示: DSH 的依赖在 app.asar 里，普通 node 读不了。改用 DSH 自带 Electron 以 node 模式执行：\n' +
        '    ELECTRON_RUN_AS_NODE=1 DSH_TOOLS_MODULE="<app>/Resources/app.asar/dsh/node_modules/@deepseek-ai/dsh-tools/lib/index.js" \\\n' +
        '      "<app>/Contents/MacOS/<DSH binary>" skills/dsh-plugin-release/scripts/probe-define-tools.mjs ./dsh/index.mjs --real',
    );
    return null;
  }
}

const report = await run();
if (process.argv.includes('--json')) {
  console.log(JSON.stringify(report, null, 2));
} else {
  for (const c of report.checks) console.log(`${c.ok ? '✅' : '❌'} ${c.name}`);
  if (report.problems.length) {
    console.log(`\n共 ${report.problems.length} 个问题：`);
    for (const p of report.problems) console.log(`  - ${p}`);
  }
  console.log(`\n结论: ${report.pass ? 'PASS — DSH 可注册全部工具' : 'FAIL — 发版会静默丢失工具'}`);
}
process.exit(report.pass ? 0 : 1);
