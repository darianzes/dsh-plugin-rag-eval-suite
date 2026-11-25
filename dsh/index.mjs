/**
 * DeepSeek Harness (dsh) Cordis Plugin Adapter for RAG Eval Suite.
 *
 * - Uses DSH Loader `insert:` composition via `cordis.patch.yml`.
 * - Resolves `@deepseek-ai/dsh-tools`'s `defineTool` when running inside DSH.
 * - Uses DSH `DefineToolOptions` property-map `parameters` DSL and explicit
 *   `output: { schema, render(args, value) }` projection on all 10 tools.
 * - Tool names are 1:1 aligned with `rag_eval/mcp_server.py` and `SKILL.md`.
 */

import { execFile } from 'node:child_process';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';

const execFileAsync = promisify(execFile);
const __dirname = dirname(fileURLToPath(import.meta.url));
const PLUGIN_ROOT = resolve(__dirname, '..');

// Dynamically resolve real `@deepseek-ai/dsh-tools` defineTool when hosted inside DSH.
let resolvedDshDefineTool = null;
try {
  const dshToolsMod = await import('@deepseek-ai/dsh-tools');
  if (typeof dshToolsMod?.defineTool === 'function') {
    resolvedDshDefineTool = dshToolsMod.defineTool;
  }
} catch {
  // Optional peer dependency: absent in standalone/non-DSH test environments.
}

export const name = 'rag-eval-suite';

// Canonical DSH tool-plugin service dependency declaration.
export const inject = ['tools'];

export const defaultConfig = {
  pythonBin: 'python3',
  defaultWorkspace: '.rag_eval',
  maxWorkers: 2,
  memoryLimitMb: 256,
  timeoutMs: 120000,
};

/**
 * Execute a CLI subcommand against the stdlib Python core with bounded memory/timeout.
 * Preserves structured JSON error payloads (e.g., exit code 2 from Understand-First Gate).
 */
export async function runCoreCli(args, options = {}) {
  const pythonBin = options.pythonBin || defaultConfig.pythonBin;
  const timeout = options.timeoutMs || defaultConfig.timeoutMs;
  const env = {
    ...process.env,
    PYTHONPATH: PLUGIN_ROOT + (process.env.PYTHONPATH ? `:${process.env.PYTHONPATH}` : ''),
    PYTHONUNBUFFERED: '1',
    RAG_EVAL_MAX_WORKERS: String(options.maxWorkers || defaultConfig.maxWorkers),
    RAG_EVAL_MEM_LIMIT_MB: String(options.memoryLimitMb || defaultConfig.memoryLimitMb),
  };

  try {
    const { stdout, stderr } = await execFileAsync(pythonBin, ['-m', 'rag_eval', ...args], {
      cwd: options.cwd || process.cwd(),
      env,
      timeout,
      maxBuffer: 8 * 1024 * 1024,
    });
    return { ok: true, exitCode: 0, stdout: stdout.trim(), stderr: stderr.trim() };
  } catch (err) {
    const stdout = typeof err.stdout === 'string' ? err.stdout.trim() : '';
    const stderr = typeof err.stderr === 'string' ? err.stderr.trim() : String(err.message || err);
    return { ok: false, exitCode: err.code ?? 1, stdout, stderr };
  }
}

function renderJsonResult(_args, value) {
  const text = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
  return [{ type: 'text', text }];
}

function createToolSpec(spec) {
  return {
    name: spec.name,
    description: spec.description,
    parameters: spec.parameters,
    output: {
      schema: { type: 'object', additionalProperties: true },
      render: renderJsonResult,
    },
    execute: spec.execute,
  };
}

/**
 * Full 10-tool specification aligned 1:1 with MCP (`rag_eval/mcp_server.py`) & `SKILL.md`.
 * Uses DSH `DefineToolOptions` property-map `parameters` DSL (NOT root JSON Schema object).
 */
export function buildToolSpecs(config = {}) {
  const merged = { ...defaultConfig, ...config };

  const jsonRun = async (cliArgs, cwd, arrayWrapKey = 'items') => {
    const res = await runCoreCli([...cliArgs, '--json'], { ...merged, cwd });
    if (res.stdout) {
      try {
        const parsed = JSON.parse(res.stdout);
        if (Array.isArray(parsed)) {
          return { [arrayWrapKey]: parsed, count: parsed.length };
        }
        if (parsed !== null && typeof parsed === 'object') {
          return parsed;
        }
        return { ok: res.ok, value: parsed };
      } catch {
        return { ok: res.ok, raw: res.stdout, stderr: res.stderr };
      }
    }
    return { ok: false, exitCode: res.exitCode, error: res.stderr || 'CLI produced no output' };
  };

  const toBoolStr = (val) => (val === true ? 'true' : val === false ? 'false' : null);

  return [
    // 1. rag_eval_inspect
    createToolSpec({
      name: 'rag_eval_inspect',
      description:
        'Phase 1 (Understand-First): Inspect target RAG codebase & knowledge base to discover architecture, observability, and blocking unknowns before any testing.',
      parameters: {
        project_path: { type: 'string', required: true, description: 'Target RAG codebase or knowledge base directory' },
        kb_paths: { type: 'array', items: { type: 'string' }, description: 'Knowledge base paths (optional)' },
        system_name: { type: 'string', description: 'Optional display name for target RAG system' },
        workdir: { type: 'string', description: 'Evaluation state directory (default .rag_eval)' },
        adapter: { type: 'string', description: 'Adapter type: http | python | cli | offline | demo' },
        endpoint: { type: 'string', description: 'HTTP endpoint URL when adapter=http' },
        callable: { type: 'string', description: 'Python callable path module:func when adapter=python' },
        responses_file: { type: 'string', description: 'Offline JSONL/JSON file when adapter=offline' },
        observability_overrides: {
          type: 'object',
          additionalProperties: true,
          description: 'Observability overrides: { retrieved_docs?: boolean, citations?: boolean }',
        },
      },
      execute: async (p = {}) => {
        const projectPath = p.project_path || p.projectPath;
        const args = ['inspect', '--project', projectPath, '--workdir', p.workdir || merged.defaultWorkspace];
        const kbList = Array.isArray(p.kb_paths) ? p.kb_paths : p.kbPath ? [p.kbPath] : [];
        if (kbList.length) args.push('--kb', kbList.join(','));
        if (p.system_name || p.systemName) args.push('--name', p.system_name || p.systemName);
        if (p.adapter) args.push('--adapter', p.adapter);
        if (p.endpoint) args.push('--endpoint', p.endpoint);
        if (p.callable) args.push('--callable', p.callable);
        if (p.responses_file) args.push('--responses', p.responses_file);
        const obs = p.observability_overrides || p.observabilityOverrides || {};
        if (toBoolStr(obs.retrieved_docs) !== null) args.push('--obs-retrieved', toBoolStr(obs.retrieved_docs));
        if (toBoolStr(obs.citations) !== null) args.push('--obs-citations', toBoolStr(obs.citations));
        return jsonRun(args, projectPath);
      },
    }),

    // 2. rag_eval_confirm
    createToolSpec({
      name: 'rag_eval_confirm',
      description:
        'Resolve blocking unknowns, set adapter & observability flags, and explicitly confirm the RAG understanding profile to unlock test generation and backtesting.',
      parameters: {
        workdir: { type: 'string', description: 'Evaluation state directory (default .rag_eval)' },
        adapter: { type: 'string', description: 'Adapter type: http | python | cli | offline | demo' },
        endpoint: { type: 'string', description: 'HTTP endpoint URL' },
        callable: { type: 'string', description: 'Python callable module:func' },
        command: { type: 'string', description: 'CLI command template' },
        responses_file: { type: 'string', description: 'Offline JSONL/JSON file path' },
        demo_mode: { type: 'string', description: 'Demo mode: realistic_flawed | strict_good' },
        observability_overrides: {
          type: 'object',
          additionalProperties: true,
          description: 'Observability flags: { retrieved_docs?: boolean, citations?: boolean }',
        },
        resolutions: {
          type: 'object',
          additionalProperties: true,
          description: 'Map of unknown ID -> resolution note',
        },
        architecture_note: { type: 'string', description: 'Optional architecture note' },
        confirmed_by: { type: 'string', description: 'Confirmer identity (default user)' },
      },
      execute: async (p = {}) => {
        const args = ['confirm', '--workdir', p.workdir || merged.defaultWorkspace];
        const adCfg = p.adapter_config || {};
        const adapter = p.adapter || adCfg.type;
        if (adapter) args.push('--adapter', adapter);
        if (p.endpoint || adCfg.endpoint) args.push('--endpoint', p.endpoint || adCfg.endpoint);
        if (p.callable || adCfg.callable) args.push('--callable', p.callable || adCfg.callable);
        if (p.command || adCfg.command) args.push('--command', p.command || adCfg.command);
        if (p.responses_file || adCfg.responses_file) args.push('--responses', p.responses_file || adCfg.responses_file);
        if (p.demo_mode || adCfg.demo_mode) args.push('--demo-mode', p.demo_mode || adCfg.demo_mode);
        const obs = p.observability_overrides || p.observabilityOverrides || {};
        if (toBoolStr(obs.retrieved_docs) !== null) args.push('--obs-retrieved', toBoolStr(obs.retrieved_docs));
        if (toBoolStr(obs.citations) !== null) args.push('--obs-citations', toBoolStr(obs.citations));
        if (p.resolutions && typeof p.resolutions === 'object') {
          for (const [k, v] of Object.entries(p.resolutions)) {
            args.push('--resolve', `${k}=${v}`);
          }
        }
        if (p.architecture_note) args.push('--note', p.architecture_note);
        if (p.confirmed_by) args.push('--by', p.confirmed_by);
        return jsonRun(args);
      },
    }),

    // 3. rag_eval_scope
    createToolSpec({
      name: 'rag_eval_scope',
      description:
        'Phase 2: Profile RAG complexity and recommend/apply Core Scope Pruning (smoke / core / full) with an explicit Tested vs Deferred disclosure.',
      parameters: {
        workdir: { type: 'string', description: 'Evaluation state directory' },
        profile: { type: 'string', description: 'Scope profile: auto | smoke | core | full' },
        max_cases: { type: 'number', description: 'Target case count cap' },
        core_paths: { type: 'array', items: { type: 'string' }, description: 'Priority core KB subpaths' },
      },
      execute: async (p = {}) => {
        const args = ['scope', '--workdir', p.workdir || merged.defaultWorkspace];
        if (p.profile) args.push('--profile', p.profile);
        if (p.max_cases || p.maxCases) args.push('--max-cases', String(p.max_cases || p.maxCases));
        if (Array.isArray(p.core_paths) && p.core_paths.length) args.push('--core-paths', p.core_paths.join(','));
        return jsonRun(args);
      },
    }),

    // 4. rag_eval_generate_dataset
    createToolSpec({
      name: 'rag_eval_generate_dataset',
      description:
        'Phase 3: Generate categorized, corpus-grounded RAG test dataset (enforces Understand-First Gate).',
      parameters: {
        workdir: { type: 'string', description: 'Evaluation state directory' },
        scope: { type: 'string', description: 'Scope override: smoke | core | full' },
        max_cases: { type: 'number', description: 'Target case count' },
        use_llm_gen: {
          type: 'boolean',
          description: 'Optional LLM query phrasing (sends query + 160-char snippet to configured LLM API; off by default)',
        },
        force_unconfirmed: { type: 'boolean', description: 'Force bypass gate (stamps UNVERIFIED warning)' },
      },
      execute: async (p = {}) => {
        const args = ['generate', '--workdir', p.workdir || merged.defaultWorkspace];
        if (p.scope) args.push('--scope', p.scope);
        if (p.max_cases || p.maxCases) args.push('--max-cases', String(p.max_cases || p.maxCases));
        if (p.use_llm_gen) args.push('--llm-gen');
        if (p.force_unconfirmed) args.push('--force-unconfirmed');
        return jsonRun(args);
      },
    }),

    // 5. rag_eval_view_dataset
    createToolSpec({
      name: 'rag_eval_view_dataset',
      description:
        'View generated test data separately with category distribution, coverage balance warnings, and filters.',
      parameters: {
        workdir: { type: 'string', description: 'Evaluation state directory' },
        category: { type: 'string', description: 'Filter by category ID' },
        difficulty: { type: 'string', description: 'Filter by difficulty: easy | medium | hard' },
        core_only: { type: 'boolean', description: 'Only return core_priority=1 cases' },
        search: { type: 'string', description: 'Keyword filter' },
        limit: { type: 'number', description: 'Max cases to return' },
        summary_only: { type: 'boolean', description: 'Return category coverage summary only' },
        export_markdown: { type: 'boolean', description: 'Also include full Markdown table export' },
      },
      execute: async (p = {}) => {
        const wd = p.workdir || merged.defaultWorkspace;
        const sub = p.summary_only ? 'summary' : 'list';
        const args = ['dataset', sub, '--workdir', wd];
        if (sub === 'list') {
          if (p.category) args.push('--category', p.category);
          if (p.difficulty) args.push('--difficulty', p.difficulty);
          if (p.core_only || p.coreOnly) args.push('--core-only');
          if (p.search) args.push('--search', p.search);
          if (p.limit) args.push('--limit', String(p.limit));
        }
        const out = await jsonRun(args);
        if (p.export_markdown) {
          const mdRes = await runCoreCli(['dataset', 'export', '--workdir', wd], merged);
          out.markdown = mdRes.stdout;
        }
        return out;
      },
    }),

    // 6. rag_eval_list_methods
    createToolSpec({
      name: 'rag_eval_list_methods',
      description: 'List all modular RAG test methods, their metrics, and required observability flags.',
      parameters: {},
      execute: async () => jsonRun(['methods'], undefined, 'methods'),
    }),

    // 7. rag_eval_run
    createToolSpec({
      name: 'rag_eval_run',
      description:
        'Phase 4-6: Execute single test method, single case, category slice, core subset, full batch, or offline manual snapshot, and produce P0-P3 diagnostics + HTML/MD reports.',
      parameters: {
        workdir: { type: 'string', description: 'Evaluation state directory' },
        methods: { type: 'array', items: { type: 'string' }, description: 'Method IDs or ["all"]' },
        case_ids: { type: 'array', items: { type: 'string' }, description: 'Specific case IDs to run' },
        categories: { type: 'array', items: { type: 'string' }, description: 'Categories to run' },
        core_only: { type: 'boolean', description: 'Run core cases only' },
        adapter: { type: 'string', description: 'Adapter override: http | python | cli | offline | demo' },
        endpoint: { type: 'string', description: 'HTTP endpoint override' },
        responses_file: { type: 'string', description: 'Offline manual outputs JSONL/JSON file' },
        workers: { type: 'number', description: 'Bounded worker concurrency (default <= 2)' },
        use_llm_judge: { type: 'boolean', description: 'Optional LLM-as-a-Judge hybrid auditing' },
        include_cases: { type: 'boolean', description: 'Include full case_evaluations array in output JSON' },
        force_unconfirmed: { type: 'boolean', description: 'Force bypass gate (stamps UNVERIFIED warning)' },
      },
      execute: async (p = {}) => {
        const args = ['run', '--workdir', p.workdir || merged.defaultWorkspace];
        const methodsVal = Array.isArray(p.methods) ? p.methods.join(',') : p.methods;
        const casesVal = Array.isArray(p.case_ids) ? p.case_ids.join(',') : (p.case_ids || p.caseId);
        const catsVal = Array.isArray(p.categories) ? p.categories.join(',') : p.categories;
        if (methodsVal) args.push('--methods', methodsVal);
        if (casesVal) args.push('--case-id', casesVal);
        if (catsVal) args.push('--categories', catsVal);
        if (p.core_only || p.coreOnly) args.push('--core-only');
        if (p.adapter) args.push('--adapter', p.adapter);
        if (p.endpoint) args.push('--endpoint', p.endpoint);
        if (p.responses_file || p.responsesFile) args.push('--responses', p.responses_file || p.responsesFile);
        if (p.workers) args.push('--workers', String(p.workers));
        if (p.use_llm_judge) args.push('--llm-judge');
        if (p.include_cases || p.detail) args.push('--detail');
        if (p.force_unconfirmed) args.push('--force-unconfirmed');
        return jsonRun(args);
      },
    }),

    // 8. rag_eval_manual_eval
    createToolSpec({
      name: 'rag_eval_manual_eval',
      description:
        'Run instant ad-hoc manual single-case evaluation on a pasted query, retrieved chunks, actual answer, and expected answer (no live RAG needed).',
      parameters: {
        query: { type: 'string', required: true, description: 'User query' },
        answer: { type: 'string', required: true, description: 'Actual RAG output answer' },
        expected_answer: { type: 'string', description: 'Expected reference answer (optional)' },
        retrieved_chunks: { type: 'array', items: { type: 'string' }, description: 'Retrieved chunk texts' },
        must_include_facts: { type: 'array', items: { type: 'string' }, description: 'Required fact phrases' },
        must_not_claim: { type: 'array', items: { type: 'string' }, description: 'Forbidden/trap phrases' },
        should_abstain: { type: 'boolean', description: 'True if query is out-of-scope' },
        methods: { type: 'array', items: { type: 'string' }, description: 'Methods to evaluate' },
      },
      execute: async (p = {}) => {
        const args = ['manual-eval', '--query', p.query, '--answer', p.answer];
        if (p.expected_answer) args.push('--expected', p.expected_answer);
        for (const ch of p.retrieved_chunks || []) {
          args.push('--chunk', typeof ch === 'string' ? ch : ch.text || JSON.stringify(ch));
        }
        if (Array.isArray(p.must_include_facts) && p.must_include_facts.length) {
          args.push('--facts', p.must_include_facts.join(','));
        }
        if (Array.isArray(p.must_not_claim) && p.must_not_claim.length) {
          args.push('--forbid', p.must_not_claim.join(','));
        }
        if (p.should_abstain) args.push('--abstain');
        if (Array.isArray(p.methods) && p.methods.length) args.push('--methods', p.methods.join(','));
        return jsonRun(args);
      },
    }),

    // 9. rag_eval_generate_report
    createToolSpec({
      name: 'rag_eval_generate_report',
      description:
        'Generate standalone browsable HTML analysis page and downloadable Markdown (.md) report for a completed run.',
      parameters: {
        workdir: { type: 'string', description: 'Evaluation state directory' },
        run_id: { type: 'string', description: 'Run ID (defaults to latest run)' },
        update_latest: { type: 'boolean', description: 'Force overwrite latest_report.* even for historical run_id' },
      },
      execute: async (p = {}) => {
        const args = ['report', '--workdir', p.workdir || merged.defaultWorkspace];
        if (p.run_id || p.runId) args.push('--run-id', p.run_id || p.runId);
        if (p.update_latest) args.push('--update-latest');
        return jsonRun(args);
      },
    }),

    // 10. rag_eval_status
    createToolSpec({
      name: 'rag_eval_status',
      description:
        'Check RAG evaluation workspace status, understanding confirmation state, dataset counts, recent runs, and low-footprint memory usage (RSS MB).',
      parameters: {
        workdir: { type: 'string', description: 'Evaluation state directory' },
      },
      execute: async (p = {}) => jsonRun(['status', '--workdir', p.workdir || merged.defaultWorkspace]),
    }),
  ];
}

/**
 * Cordis plugin entrypoint for DeepSeek Harness (dsh).
 * Declares `export const inject = ['tools']` and registers all 10 tools via
 * `ctx.tools.register(defineTool(spec))`.
 */
export function apply(ctx, config = {}) {
  const merged = { ...defaultConfig, ...config };
  const tools = buildToolSpecs(merged);
  const logWarn = (msg) => {
    if (ctx?.logger && typeof ctx.logger.warn === 'function') {
      ctx.logger.warn(msg);
    } else {
      console.warn(`[${name}] ${msg}`);
    }
  };

  let registeredAny = false;

  const registerIntoToolsService = (targetCtx) => {
    const registry = targetCtx?.tools;
    if (!registry || typeof registry.register !== 'function') return false;

    const defineFn =
      typeof config.defineTool === 'function'
        ? config.defineTool
        : resolvedDshDefineTool;

    if (!defineFn) {
      logWarn(
        'Optional peer `@deepseek-ai/dsh-tools` was not found; registering DSH-shaped tool specs directly. ' +
          'Run inside DSH runtime (where `@deepseek-ai/dsh-tools` is present) for full defineTool validation.',
      );
    }

    const disposers = [];
    for (const spec of tools) {
      const toolDef = defineFn ? defineFn(spec) : spec;
      const dispose = registry.register(toolDef);
      if (typeof dispose === 'function') disposers.push(dispose);
    }
    registeredAny = true;
    return () => {
      for (const d of disposers) {
        try {
          d();
        } catch {
          // ignore teardown errors
        }
      }
    };
  };

  if (ctx?.tools && typeof ctx.tools.register === 'function') {
    if (typeof ctx.effect === 'function') {
      ctx.effect(() => registerIntoToolsService(ctx) || undefined);
    } else {
      registerIntoToolsService(ctx);
    }
    return;
  }

  if (ctx && typeof ctx.inject === 'function') {
    ctx.inject(['tools'], (subCtx) => {
      if (typeof subCtx.effect === 'function') {
        subCtx.effect(() => registerIntoToolsService(subCtx) || undefined);
      } else {
        registerIntoToolsService(subCtx);
      }
    });
    return;
  }

  if (!registeredAny) {
    logWarn(
      'Cordis `tools` service was not found on ctx; no DSH tools were registered. ' +
        'Ensure `@deepseek-ai/dsh-tools` is mounted, or connect via stdio MCP (`scripts/run_mcp.sh`).',
    );
  }
}

export default { name, inject, apply, buildToolSpecs, runCoreCli };
