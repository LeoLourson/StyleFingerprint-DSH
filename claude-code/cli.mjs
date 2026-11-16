#!/usr/bin/env node
/**
 * Claude Code adapter of the bundle.
 *
 * DSH hands the plugin a Cordis context; Claude Code has none. This file plays
 * that host: it calls the plugin's own `apply(ctx)` with a minimal context and
 * keeps what the plugin registers — the `style` tool, the prompt section and the
 * skill — then serves each to Claude Code in its own form:
 *
 *   node claude-code/cli.mjs serve           MCP server over stdio with the `style` tool
 *   node claude-code/cli.mjs session-start   SessionStart hook: the prompt section as context
 *   node claude-code/cli.mjs skill <dir>     write <dir>/SKILL.md from the plugin skill
 *
 * The MCP transport is newline-delimited JSON-RPC 2.0 written by hand, so the
 * adapter needs no npm packages.
 */
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { createInterface } from 'node:readline';
import { apply, closeEngine, name as pluginName } from '../index.js';

/** Claude Code names an MCP tool `mcp__<server>__<tool>`; the server name is set at `claude mcp add`. */
const SERVER = 'style-fingerprint';
const TOOL_ID = `mcp__${SERVER}__style`;

/** Run `apply` against a fake Cordis context and keep what it registers. */
function host() {
  const got = { tools: new Map(), sections: [], skills: [] };
  const services = {
    tools: { register: (tool) => { got.tools.set(tool.name, tool); return () => {}; } },
    systemPrompt: { section: (section) => { got.sections.push(section); return () => {}; } },
    skills: { register: (skill) => { got.skills.push(skill); return () => {}; } },
  };
  const ctx = {
    ...services,
    effect: (fn) => { fn(); return () => {}; },
    on: () => () => {},
    get: (service) => services[service],
    // Claude Code has no webServer, so the settings page route stays unmounted.
    inject: (_names, callback) => { callback(ctx); },
  };
  apply(ctx);
  return got;
}

function sectionText(got) {
  const section = got.sections[0];
  const text = typeof section?.text === 'function' ? section.text() : String(section?.text ?? '');
  return `${text}\n\nВ Claude Code инструмент \`style\` называется \`${TOOL_ID}\`, скилл — \`style\`.`;
}

function sessionStart(got) {
  const output = { hookSpecificOutput: { hookEventName: 'SessionStart', additionalContext: sectionText(got) } };
  process.stdout.write(`${JSON.stringify(output)}\n`);
}

function writeSkill(got, dir) {
  if (!dir) throw new Error('usage: cli.mjs skill <dir>');
  const skill = got.skills[0];
  if (!skill) throw new Error('the plugin registered no skill');
  const content = skill.content.replaceAll('`style`', `\`style\` (\`${TOOL_ID}\`)`);
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, 'SKILL.md'), `---\nname: ${skill.name}\ndescription: ${JSON.stringify(skill.description)}\n---\n\n${content}\n`, 'utf8');
}

async function answer(got, message) {
  const { method, params = {} } = message;
  switch (method) {
    case 'initialize':
      return {
        protocolVersion: params.protocolVersion ?? '2025-06-18',
        capabilities: { tools: {} },
        serverInfo: { name: pluginName, version: '0.1.0' },
      };
    case 'ping':
      return {};
    case 'tools/list':
      return {
        tools: [...got.tools.values()].map((tool) => ({ name: tool.name, description: tool.description, inputSchema: tool.parameters })),
      };
    case 'tools/call': {
      const tool = got.tools.get(params.name);
      if (!tool) return { content: [{ type: 'text', text: `unknown tool ${params.name}` }], isError: true };
      // `execute` already turns a thrown error into a JSON answer with `error`.
      const text = await tool.execute(params.arguments ?? {}, { context: {} });
      return { content: [{ type: 'text', text }] };
    }
    default:
      throw Object.assign(new Error(`method not found: ${method}`), { code: -32601 });
  }
}

function serve(got) {
  const send = (reply) => process.stdout.write(`${JSON.stringify({ jsonrpc: '2.0', ...reply })}\n`);
  const pending = new Set();
  const handle = async (line) => {
    if (line.trim() === '') return;
    let message;
    try {
      message = JSON.parse(line);
    } catch {
      send({ id: null, error: { code: -32700, message: 'parse error' } });
      return;
    }
    if (message.id === undefined) return; // notifications need no answer
    try {
      send({ id: message.id, result: await answer(got, message) });
    } catch (error) {
      send({ id: message.id, error: { code: error.code ?? -32603, message: error.message } });
    }
  };
  const lines = createInterface({ input: process.stdin });
  lines.on('line', (line) => {
    const job = handle(line).finally(() => pending.delete(job));
    pending.add(job);
  });
  // Answer what is already in flight before the engine stops and the process exits.
  lines.on('close', () => {
    void Promise.allSettled([...pending]).then(closeEngine).finally(() => process.exit(0));
  });
}

const [command, arg] = process.argv.slice(2);
const got = host();
if (command === 'serve') serve(got);
else if (command === 'session-start') sessionStart(got);
else if (command === 'skill') writeSkill(got, arg);
else {
  process.stderr.write('usage: cli.mjs serve | session-start | skill <dir>\n');
  process.exit(2);
}
