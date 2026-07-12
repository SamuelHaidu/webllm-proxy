/**
 * Thin wrapper around the `ai-memory` CLI (the dockerised wrapper script at
 * e.g. ~/.local/bin/ai-memory — see its own --help). No MCP server involved
 * at runtime: we spawn the CLI directly, matching exactly how a human would
 * invoke it, and let its own wrapper handle Docker/networking/data-volume
 * concerns (AI_MEMORY_SERVER_URL / AI_MEMORY_DATA_DIR overrides, if any, are
 * inherited from the environment unchanged).
 *
 * Project/workspace scoping is whatever the CLI derives from `cwd` (its own
 * `--workspace`/`--project` default resolution) -- we don't second-guess it.
 *
 * `execFile` (not `exec`/a shell) so page bodies/paths are never shell-parsed.
 */

import { execFile } from "node:child_process";

export const DEFAULT_BIN = "ai-memory";

export interface SearchHit {
  path: string;
  title?: string;
  snippet?: string;
  rank?: number;
}

export interface PageBody {
  path: string;
  workspace: string;
  project: string;
  title: string | null;
  body: string;
  frontmatter?: Record<string, unknown>;
}

export interface MemoryStatus {
  version: string;
  counts?: { pages_latest?: number; pages_all?: number; sessions?: number; observations?: number };
  [key: string]: unknown;
}

export interface WritePageOptions {
  path: string;
  body: string;
  title?: string;
  kind?: "fact" | "rule" | "decision" | "gotcha";
  tags?: string[];
  tier?: "working" | "episodic" | "semantic" | "procedural";
  pinned?: boolean;
}

/** Options threaded through every call: which binary to run and from where
 *  (cwd drives the CLI's own project auto-derivation). */
export interface ClientOptions {
  bin?: string;
  cwd?: string;
}

async function run(args: string[], opts: ClientOptions = {}): Promise<string> {
  const { bin = DEFAULT_BIN, cwd } = opts;
  return new Promise((resolve, reject) => {
    execFile(
      bin,
      args,
      {
        cwd,
        env: {
          ...process.env,
          AI_MEMORY_NO_TTY: "1",
          AI_MEMORY_NO_VERSION_CHECK: "1",
        },
        maxBuffer: 10 * 1024 * 1024,
      },
      (error, stdout) => {
        if (error) {
          reject(error);
          return;
        }
        resolve(stdout.toString());
      },
    );
  });
}

export async function searchMemory(
  query: string,
  opts: ClientOptions & { limit?: number } = {},
): Promise<SearchHit[]> {
  const args = ["search", query, "--json"];
  if (opts.limit) args.push("--limit", String(opts.limit));
  const out = await run(args, opts);
  return JSON.parse(out) as SearchHit[];
}

export async function readMemoryPage(
  target: { path?: string; query?: string },
  opts: ClientOptions = {},
): Promise<PageBody> {
  const args = ["read-page", "--json"];
  if (target.path) {
    args.push("--path", target.path);
  } else if (target.query) {
    args.push(target.query);
  } else {
    throw new Error("readMemoryPage requires either path or query");
  }
  const out = await run(args, opts);
  return JSON.parse(out) as PageBody;
}

export async function writeMemoryPage(
  options: WritePageOptions,
  opts: ClientOptions = {},
): Promise<string> {
  const args = ["write-page", "--path", options.path, "--body", options.body];
  if (options.title) args.push("--title", options.title);
  if (options.kind) args.push("--kind", options.kind);
  if (options.tier) args.push("--tier", options.tier);
  if (options.pinned) args.push("--pinned");
  for (const tag of options.tags ?? []) args.push("--tag", tag);
  // write-page has no --json flag; its stdout is a one-line human confirmation.
  return (await run(args, opts)).trim();
}

export async function memoryStatus(opts: ClientOptions = {}): Promise<MemoryStatus> {
  const out = await run(["status", "--json"], opts);
  return JSON.parse(out) as MemoryStatus;
}
