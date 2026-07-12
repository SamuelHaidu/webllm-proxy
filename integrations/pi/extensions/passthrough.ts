/**
 * `/chatgpt` and `/genie` pass-through commands: switch the CURRENT session to
 * a `webllm` model on the given upstream and disable ALL local tool calling
 * (`pi.setActiveTools([])`) -- no injected read/write/bash, no injected
 * research/memory/subagent, nothing. What's left talks to the raw web
 * backend:
 *
 * - `/chatgpt [model-id] [message]` -- plain chatgpt.com web chat, exactly
 *   like using chatgpt.com in a browser.
 * - `/genie [model-id] [message]` -- plain Claude-over-databricks chat (the
 *   `llmproxy` channel, native `tool_use` if a future extension ever adds
 *   tools back -- see caveat below). NOT a re-creation of Databricks' own
 *   ~37 KB Genie system prompt + 30 built-in server-executed tools (that
 *   bundle was deliberately not reverse-engineered/replicated here -- see
 *   docs/discovery/2026-07-10-databricks-llmproxy.md Update 1). "No injected
 *   custom tools" is honored literally: pi sends nothing of its own.
 *
 * Both commands reuse the model discovery from webllm-provider.ts; the
 * `webllm` provider must already be registered (gateway reachable at load
 * time) for either to find any candidates.
 */

import type { ExtensionAPI, ExtensionCommandContext } from "@earendil-works/pi-coding-agent";
import { CHATGPT_PREFIX, DATABRICKS_PREFIX, pickModel, splitArgs } from "../src/passthrough";

async function runPassthrough(
  pi: ExtensionAPI,
  ctx: ExtensionCommandContext,
  label: string,
  prefix: string,
  args: string,
): Promise<void> {
  // Left as the registry's real Model<Api>[] (not narrowed to MinimalModel) so
  // `pi.setModel(picked)` below gets a real Model straight from getAll(), no cast.
  const all = ctx.modelRegistry.getAll();
  const knownIds = all
    .filter((m) => m.provider === "webllm" && m.id.startsWith(prefix))
    .map((m) => m.id);
  const { modelId, message } = splitArgs(args, knownIds);
  const picked = pickModel(all, prefix, modelId);

  if (!picked) {
    ctx.ui.notify(
      `No ${label} models found via the webllm provider. Is 'webllm-proxy gateway' running ` +
        `with the '${prefix.replace("__", "")}' provider served (webllm-proxy serve --provider ` +
        `${prefix.replace("__", "")}), and did this session start with the gateway reachable?`,
      "error",
    );
    return;
  }

  const success = await pi.setModel(picked);
  if (!success) {
    ctx.ui.notify(`No API key/auth configured for webllm model ${picked.id}.`, "error");
    return;
  }
  pi.setActiveTools([]);
  ctx.ui.notify(`${label} pass-through: ${picked.id}, no local tools active.`, "info");

  if (message) {
    pi.sendUserMessage(message);
  }
}

export default function passthrough(pi: ExtensionAPI): void {
  pi.registerCommand("chatgpt", {
    description: "Plain chatgpt.com web chat pass-through (no local tools)",
    handler: (args, ctx) => runPassthrough(pi, ctx, "chatgpt", CHATGPT_PREFIX, args),
  });

  pi.registerCommand("genie", {
    description: "Plain databricks Genie/Claude pass-through (no local tools)",
    handler: (args, ctx) => runPassthrough(pi, ctx, "genie", DATABRICKS_PREFIX, args),
  });
}
