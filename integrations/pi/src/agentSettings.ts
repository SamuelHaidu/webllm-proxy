/**
 * The "chatgpt emulated agent mode" toggle. It lives in pi's own settings files
 * as `{ "webllm": { "chatgptAgentMode": true } }` -- `~/.pi/agent/settings.json`
 * (global) or `.pi/settings.json` (project; project wins). The usual way to flip
 * it is the `/webllm-agent` command (extensions/chatgpt-agent.ts), which uses
 * `setGlobalAgentMode` here; you can also edit the file by hand, or set
 * `WEBLLM_CHATGPT_AGENT=1` to override for one run.
 *
 * We read/write the raw JSON rather than pi's typed `SettingsManager` because
 * this is a custom (non-schema) key. `resolveAgentMode` / `withAgentMode` are
 * the pure, unit-tested cores.
 */

import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { CONFIG_DIR_NAME, getAgentDir } from "@earendil-works/pi-coding-agent";

export const ENV_VAR = "WEBLLM_CHATGPT_AGENT";
const FALSEY = new Set(["0", "false", "no", "off", ""]);

export interface WebllmSettings {
  webllm?: { chatgptAgentMode?: unknown };
}

function pick(settings: WebllmSettings | null): boolean | undefined {
  const value = settings?.webllm?.chatgptAgentMode;
  return typeof value === "boolean" ? value : undefined;
}

/** Pure resolution: env override wins, else project setting, else global,
 * else off. */
export function resolveAgentMode(
  env: string | undefined,
  project: WebllmSettings | null,
  global: WebllmSettings | null,
): boolean {
  if (env !== undefined) return !FALSEY.has(env.trim().toLowerCase());
  return pick(project) ?? pick(global) ?? false;
}

function readJson(path: string): WebllmSettings | null {
  try {
    return JSON.parse(readFileSync(path, "utf-8")) as WebllmSettings;
  } catch {
    return null;
  }
}

/** Whether the emulated-agent provider should be registered for `cwd`. */
export function isChatgptAgentModeEnabled(cwd: string): boolean {
  const project = readJson(join(cwd, CONFIG_DIR_NAME, "settings.json"));
  const global = readJson(join(getAgentDir(), "settings.json"));
  return resolveAgentMode(process.env[ENV_VAR], project, global);
}

/** The `WEBLLM_CHATGPT_AGENT` env override, if set (it wins over settings). */
export function agentModeEnvOverride(): boolean | undefined {
  const env = process.env[ENV_VAR];
  return env === undefined ? undefined : !FALSEY.has(env.trim().toLowerCase());
}

function globalSettingsPath(): string {
  return join(getAgentDir(), "settings.json");
}

/** The persisted value in the global settings file (what the toggle command
 * writes/reads), ignoring env and project overrides. */
export function readGlobalAgentMode(): boolean {
  return pick(readJson(globalSettingsPath())) ?? false;
}

/** Pure: set `webllm.chatgptAgentMode` on a settings object, preserving every
 * other key (top-level and other `webllm.*`). */
export function withAgentMode(
  settings: Record<string, unknown>,
  enabled: boolean,
): Record<string, unknown> {
  const webllm =
    typeof settings.webllm === "object" && settings.webllm !== null
      ? (settings.webllm as Record<string, unknown>)
      : {};
  return { ...settings, webllm: { ...webllm, chatgptAgentMode: enabled } };
}

/** Persist the toggle to the GLOBAL settings file (read-modify-write, keeping
 * all other settings). Returns the file path written. */
export function setGlobalAgentMode(enabled: boolean): string {
  const path = globalSettingsPath();
  let current: Record<string, unknown> = {};
  try {
    current = JSON.parse(readFileSync(path, "utf-8")) as Record<string, unknown>;
  } catch {
    current = {};
  }
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, `${JSON.stringify(withAgentMode(current, enabled), null, 2)}\n`, "utf-8");
  return path;
}
