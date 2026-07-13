/**
 * Pure parsing for the `chatgpt_agent.md` tag protocol behind "chatgpt emulated
 * agent mode" (the `webllm-agent` provider): turns one raw model reply into an
 * `Action` (or `null` meaning "final answer, no action"), builds the project
 * tree / first message, and wraps a tool result back as a `<result>` block. No
 * pi/SDK VALUE import here, so it unit-tests standalone. `agentProtocol.ts`
 * maps these `Action`s onto native pi tool calls.
 *
 * Ported from `scripts/agent_prompt_probe.py`, which validated the protocol
 * live against `gpt-5-5` (the model that refuses the older `tool_contract.md`
 * contract): it followed the tags with no refusal or hallucinated results.
 */

import { readdirSync } from "node:fs";
import { basename, join } from "node:path";

export type Action =
  | { kind: "read_file"; path: string; lines?: string }
  | { kind: "create_file"; path: string; body: string }
  | { kind: "edit_file"; path: string; old: string; new: string }
  | { kind: "bash"; cmd: string }
  | { kind: "find"; glob: string }
  | { kind: "search"; regex: string; path?: string };

const MAX_RESULT_CHARS = 4000;

// Matches the six tags from prompts/chatgpt_agent.md. `s` (dotall) so a tag
// body can span multiple lines (e.g. a multi-line `create_file`).
const PATTERNS = {
  read_file: /<read_file\b([^>]*?)\/?>/s,
  create_file: /<create_file\b([^>]*)>(.*?)<\/create_file>/s,
  edit_file: /<edit_file\b([^>]*)>(.*?)<\/edit_file>/s,
  bash: /<bash>(.*?)<\/bash>/s,
  find: /<find\b([^>]*)>(.*?)<\/find>/s,
  search: /<search\b([^>]*)>(.*?)<\/search>/s,
} as const satisfies Record<Action["kind"], RegExp>;

function attr(attrs: string | undefined, name: string): string | undefined {
  return new RegExp(`${name}\\s*=\\s*"([^"]*)"`).exec(attrs ?? "")?.[1];
}

/** ChatGPT's web UI renders code-block indentation as alternating U+00A0
 * (non-breaking space) + regular-space pairs, and this leaks into the raw
 * text content the model's API response actually contains, not just the
 * rendered HTML -- observed live turning a multi-level-indented Python
 * `create_file` body into `SyntaxError: invalid non-printable character
 * U+00A0`. Normalize every NBSP to a plain space before parsing so generated
 * files/edits/commands aren't silently corrupted by this artifact. */
export function stripNbsp(text: string): string {
  return text.replace(/\u00a0/g, " ");
}

export interface ActionMatch {
  action: Action;
  /** Char offsets of the whole tag within the (NBSP-normalized) input, so a
   * caller can split off any prose that surrounds it. */
  start: number;
  end: number;
}

function toAction(kind: Action["kind"], m: RegExpExecArray): Action {
  switch (kind) {
    case "read_file":
      return { kind: "read_file", path: attr(m[1], "path") ?? "", lines: attr(m[1], "lines") };
    case "create_file":
      return { kind: "create_file", path: attr(m[1], "path") ?? "", body: m[2] };
    case "edit_file": {
      const inner = m[2];
      const old = /<old>(.*?)<\/old>/s.exec(inner)?.[1] ?? "";
      const newText = /<new>(.*?)<\/new>/s.exec(inner)?.[1] ?? "";
      return { kind: "edit_file", path: attr(m[1], "path") ?? "", old, new: newText };
    }
    case "bash":
      return { kind: "bash", cmd: m[1].trim() };
    case "find":
      return { kind: "find", glob: m[2].trim() };
    case "search":
      return { kind: "search", regex: m[2].trim(), path: attr(m[1], "path") };
  }
}

/** Locate the earliest of the six tags in `text` (caller NBSP-normalizes),
 * returning the parsed action plus its char span. `null` if none match. */
export function matchFirstAction(text: string): ActionMatch | null {
  let bestKind: Action["kind"] | undefined;
  let bestMatch: RegExpExecArray | undefined;
  for (const kind of Object.keys(PATTERNS) as Action["kind"][]) {
    const m = PATTERNS[kind].exec(text);
    if (m && (!bestMatch || m.index < bestMatch.index)) {
      bestKind = kind;
      bestMatch = m;
    }
  }
  if (!bestKind || !bestMatch) return null;
  return {
    action: toAction(bestKind, bestMatch),
    start: bestMatch.index,
    end: bestMatch.index + bestMatch[0].length,
  };
}

/** The earliest tag in `reply` as an `Action`, or `null` for a plain (final-
 * answer) reply. NBSP-normalizes first (see `stripNbsp`). */
export function parseAction(reply: string): Action | null {
  return matchFirstAction(stripNbsp(reply))?.action ?? null;
}

/** Wrap an executor's output text as the `<result>` block fed back as the
 * next turn's message, truncated so one huge output can't blow the context. */
export function renderResult(text: string): string {
  const clipped =
    text.length > MAX_RESULT_CHARS ? `${text.slice(0, MAX_RESULT_CHARS)}\n...(truncated)` : text;
  return `<result>\n${clipped}\n</result>`;
}

const TREE_SKIP = new Set([
  ".git",
  "__pycache__",
  ".venv",
  "node_modules",
  ".ruff_cache",
  ".pytest_cache",
  ".pi",
  "dist",
]);
const TREE_MAX_ENTRIES = 500;

/** A directories-before-files, alphabetical file tree of `cwd` (skipping the
 * usual vendor/cache/VCS dirs), for the prompt's `<<PROJECT_TREE>>` slot.
 * Capped at `TREE_MAX_ENTRIES` lines for a real (possibly large) project. */
export function buildProjectTree(cwd: string, maxEntries = TREE_MAX_ENTRIES): string {
  const lines: string[] = [`${basename(cwd)}/`];
  let truncated = false;

  function walk(dir: string, prefix: string): void {
    if (truncated) return;
    let entries: import("node:fs").Dirent[];
    try {
      entries = readdirSync(dir, { withFileTypes: true }).filter((e) => !TREE_SKIP.has(e.name));
    } catch {
      return;
    }
    entries.sort((a, b) => Number(a.isFile()) - Number(b.isFile()) || a.name.localeCompare(b.name));
    for (const e of entries) {
      if (lines.length >= maxEntries) {
        truncated = true;
        return;
      }
      lines.push(`${prefix}${e.name}${e.isDirectory() ? "/" : ""}`);
      if (e.isDirectory()) walk(join(dir, e.name), `${prefix}  `);
    }
  }

  walk(cwd, "  ");
  if (truncated) lines.push("  ...(truncated)");
  return lines.join("\n");
}

/** The first message sent to the nested agent session: the prompt with
 * `<<PROJECT_TREE>>` filled in, plus the user's `<request>`. */
export function buildFirstMessage(promptText: string, tree: string, request: string): string {
  return `${promptText.replace("<<PROJECT_TREE>>", tree)}\n\n<request>${request}</request>`;
}
