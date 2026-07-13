/**
 * "chatgpt emulated agent mode": makes chatgpt.com behave like any other native
 * pi coding model. Its prose renders as an assistant message and its actions
 * render/execute as normal tool calls (read/write/bash/edit/find/grep) via pi's
 * own loop, approval, and rendering -- replacing the old `/chatgpt --agent`
 * command (a side loop that reported through `notify()` toasts).
 *
 * chatgpt.com has no tool-calling API, so the emulation lives in the
 * `webllm-agent` provider's `streamSimple` (src/agentStream.ts +
 * src/agentProtocol.ts), driven by the bundled prompts/chatgpt_agent.md (kept
 * in sync with webllm_proxy/prompts/chatgpt_agent.md by hand).
 *
 * It is OFF by default and additive (the plain `webllm` provider is untouched).
 * Toggle it with the `/webllm-agent` command (persists to
 * `~/.pi/agent/settings.json` and applies immediately), or set the setting by
 * hand -- `{ "webllm": { "chatgptAgentMode": true } }` -- or `WEBLLM_CHATGPT_AGENT=1`
 * for one-off runs. When on, chatgpt models also appear as
 * `webllm-agent/chatgpt__<slug>` ("<title> (agent)").
 */

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import type { ExtensionAPI, ExtensionCommandContext } from "@earendil-works/pi-coding-agent";
import {
  ENV_VAR,
  agentModeEnvOverride,
  isChatgptAgentModeEnabled,
  readGlobalAgentMode,
  setGlobalAgentMode,
} from "../src/agentSettings";
import { createChatgptAgentStream } from "../src/agentStream";
import { fetchGatewayModels, gatewayBaseUrl } from "../src/gateway";
import { CHATGPT_PREFIX } from "../src/passthrough";

const PROVIDER = "webllm-agent";
const AGENT_API = "webllm-chatgpt-agent";
const COMMAND = "webllm-agent";
const DEFAULT_CONTEXT = 128_000;
const DEFAULT_MAX_TOKENS = 8_192;

const PROMPT_PATH = join(
  dirname(fileURLToPath(import.meta.url)),
  "..",
  "prompts",
  "chatgpt_agent.md",
);

const ON = new Set(["on", "true", "enable", "enabled", "1", "yes"]);
const OFF = new Set(["off", "false", "disable", "disabled", "0", "no"]);

/** notify in the TUI, but fall back to stdout in headless (`-p`/json) mode
 * where `ctx.ui.notify` is a no-op. */
function report(ctx: ExtensionCommandContext, message: string): void {
  if (ctx.hasUI) ctx.ui.notify(message, "info");
  else console.log(message);
}

/**
 * (Re)register the emulated-agent provider from the current gateway model list.
 * Returns the number of chatgpt models registered (0 = gateway down / none).
 */
async function registerAgentProvider(pi: ExtensionAPI): Promise<number> {
  const base = gatewayBaseUrl();
  const chatgptModels = (await fetchGatewayModels(base)).filter((m) =>
    m.id.startsWith(CHATGPT_PREFIX),
  );
  if (chatgptModels.length === 0) return 0;

  const promptText = readFileSync(PROMPT_PATH, "utf-8");
  pi.registerProvider(PROVIDER, {
    name: "WebLLM ChatGPT (emulated agent)",
    baseUrl: `${base}/v1`,
    // The gateway ignores auth; a literal keeps pi's model auth resolution happy.
    apiKey: "webllm-local",
    api: AGENT_API,
    streamSimple: createChatgptAgentStream({
      promptText,
      cwd: process.cwd(),
      gatewayBaseUrl: base,
    }),
    models: chatgptModels.map((m) => ({
      id: m.id,
      name: `${m._title ?? m.id} (agent)`,
      // Emulation surfaces no separate thinking block; keep it a plain model.
      reasoning: false,
      input: ["text"],
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
      contextWindow: DEFAULT_CONTEXT,
      maxTokens:
        typeof m._max_tokens === "number" && m._max_tokens > 0 ? m._max_tokens : DEFAULT_MAX_TOKENS,
    })),
  });
  return chatgptModels.length;
}

async function handleToggle(
  pi: ExtensionAPI,
  ctx: ExtensionCommandContext,
  args: string,
): Promise<void> {
  const arg = args.trim().toLowerCase();
  const override = agentModeEnvOverride();
  const overrideNote =
    override !== undefined ? ` (currently forced ${override ? "on" : "off"} by ${ENV_VAR})` : "";

  if (arg === "" || arg === "status") {
    const effective = isChatgptAgentModeEnabled(ctx.cwd) ? "on" : "off";
    report(
      ctx,
      `chatgpt emulated agent mode: ${effective}${overrideNote}. Toggle with '/${COMMAND} on' or '/${COMMAND} off'.`,
    );
    return;
  }

  const enabled = ON.has(arg) ? true : OFF.has(arg) ? false : !readGlobalAgentMode();
  setGlobalAgentMode(enabled);

  // Apply immediately so no relaunch is needed.
  let live: string;
  if (enabled) {
    const count = await registerAgentProvider(pi);
    live =
      count > 0
        ? ` ${count} models available now as ${PROVIDER}/* -- pick one with /model.`
        : " No chatgpt models found -- is 'webllm-proxy gateway' + 'serve --provider chatgpt' running?";
  } else {
    try {
      pi.unregisterProvider(PROVIDER);
    } catch {
      // not registered this session -- nothing to remove
    }
    live = ` ${PROVIDER}/* models removed.`;
  }

  report(
    ctx,
    `chatgpt emulated agent mode -> ${enabled ? "on" : "off"} (saved).${live}${overrideNote}`,
  );
}

export default async function chatgptAgent(pi: ExtensionAPI): Promise<void> {
  // Registered unconditionally so it can turn the mode ON when it's currently
  // off (the provider itself is only registered when enabled, below).
  pi.registerCommand(COMMAND, {
    description: "Toggle chatgpt emulated agent mode on|off (persists; applies immediately)",
    handler: (args, ctx) => handleToggle(pi, ctx, args),
  });

  if (isChatgptAgentModeEnabled(process.cwd())) {
    const count = await registerAgentProvider(pi);
    if (count === 0) {
      const hint =
        "Is 'webllm-proxy gateway' running with 'webllm-proxy serve --provider chatgpt'?";
      console.error(
        `[webllm-agent] chatgpt emulated agent mode is on but no chatgpt models were found. ${hint}`,
      );
    }
  }
}
