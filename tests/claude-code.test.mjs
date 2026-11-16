/**
 * Node tests of the Claude Code adapter (`claude-code/cli.mjs`): the MCP server
 * answers the handshake, lists the `style` tool and runs a check over stdio;
 * the SessionStart hook prints the style section; the skill command writes a
 * SKILL.md that Claude Code can load.
 *
 * No network and no Python required: the check runs its rules without the engine.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { mkdtemp, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = dirname(HERE);
const CLI = join(ROOT, 'claude-code', 'cli.mjs');

/** Send JSON-RPC lines to `cli.mjs serve` and collect the answers by id. */
async function talk(messages) {
  const child = spawn(process.execPath, [CLI, 'serve'], { stdio: ['pipe', 'pipe', 'pipe'] });
  const answers = new Map();
  let buffer = '';
  const expected = messages.filter((message) => message.id !== undefined).length;
  const done = new Promise((resolve, reject) => {
    child.stdout.on('data', (chunk) => {
      buffer += chunk.toString('utf8');
      let cut;
      while ((cut = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, cut).trim();
        buffer = buffer.slice(cut + 1);
        if (line === '') continue;
        const answer = JSON.parse(line);
        answers.set(answer.id, answer);
        if (answers.size === expected) resolve();
      }
    });
    child.on('error', reject);
    setTimeout(() => reject(new Error(`timeout, answers: ${answers.size}/${expected}`)), 15000).unref();
  });
  for (const message of messages) child.stdin.write(`${JSON.stringify({ jsonrpc: '2.0', ...message })}\n`);
  try {
    await done;
  } finally {
    child.stdin.end();
    child.kill();
  }
  return answers;
}

test('serve: handshake, tool list and a check over stdio', async () => {
  const spoilt = await readFile(join(ROOT, 'tests', 'fixtures', 'sample-corrupted.txt'), 'utf8');
  const answers = await talk([
    { id: 1, method: 'initialize', params: { protocolVersion: '2025-06-18', capabilities: {}, clientInfo: { name: 'test', version: '0' } } },
    { method: 'notifications/initialized' },
    { id: 2, method: 'tools/list' },
    { id: 3, method: 'tools/call', params: { name: 'style', arguments: { action: 'check', profile: 'chat_answer', text: spoilt } } },
    { id: 4, method: 'no/such/method' },
  ]);

  const init = answers.get(1).result;
  assert.equal(init.protocolVersion, '2025-06-18');
  assert.ok(init.capabilities.tools);

  const tools = answers.get(2).result.tools;
  assert.deepEqual(tools.map((tool) => tool.name), ['style']);
  assert.ok(tools[0].inputSchema.properties.action.enum.includes('check'));

  const call = answers.get(3).result;
  assert.notEqual(call.isError, true);
  const report = JSON.parse(call.content[0].text);
  assert.ok(report.violations.length > 0, 'the spoilt text has violations');

  assert.equal(answers.get(4).error.code, -32601);
});

test('session-start: prints the style section as additional context', () => {
  const run = spawnSync(process.execPath, [CLI, 'session-start'], { encoding: 'utf8' });
  assert.equal(run.status, 0, run.stderr);
  const output = JSON.parse(run.stdout);
  assert.equal(output.hookSpecificOutput.hookEventName, 'SessionStart');
  assert.match(output.hookSpecificOutput.additionalContext, /Стиль: текст для человека вне контекста/u);
  assert.match(output.hookSpecificOutput.additionalContext, /mcp__style-fingerprint__style/u);
});

test('skill: writes SKILL.md with front matter and the plugin skill text', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'style-skill-'));
  const run = spawnSync(process.execPath, [CLI, 'skill', dir], { encoding: 'utf8' });
  assert.equal(run.status, 0, run.stderr);
  const text = await readFile(join(dir, 'SKILL.md'), 'utf8');
  assert.match(text, /^---\nname: style\ndescription: "/u);
  assert.match(text, /# style: русский текст для человека вне контекста/u);
});
