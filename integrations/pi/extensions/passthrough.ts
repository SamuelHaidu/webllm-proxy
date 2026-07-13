/**
 * `/chatgpt` and `/genie` pass-through commands: switch the CURRENT session to a
 * `webllm` model on the given upstream, then set the local-tool policy for that
 * backend. They differ precisely because the backends differ:
 *
 * - `/chatgpt [model-id] [message]` -- plain chatgpt.com web chat, exactly like
 *   using chatgpt.com in a browser. Tool policy `"none"`: nothing local can run
 *   against a web chat, so ALL local tools are disabled. (For chatgpt as a
 *   working coding agent, enable "chatgpt emulated agent mode" -- see
 *   extensions/chatgpt-agent.ts -- and pick a `webllm-agent/*` model instead.)
 * - `/genie [model-id] [message]` -- databricks Claude (the `llmproxy` channel)
 *   as a working agent. Tool policy `"all"`: KEEP pi's local tools active so the
 *   model can actually act -- start SQL warehouses / run queries via the
 *   `databricks` CLI under bash, read/write files, etc. This is the fix for the
 *   channel reality: unlike the real Genie UI (which runs ~30 built-in
 *   server-side tools), the raw `llmproxy` passthrough executes NO tools of its
 *   own, so a model with no client tools can only describe steps, never do them.
 *   `genie_framing.md` (injected server-side) already primes it to use bash/read/
 *   write; this makes those tools actually present. (Run `/genie` inside a real
 *   Databricks project dir, not this proxy's own repo, to avoid the repo's
 *   CLAUDE.md confusing the model about where it is.)
 *
 * Both commands reuse the model discovery from webllm-provider.ts; the `webllm`
 * provider must already be registered (gateway reachable at load time) for
 * either to find any candidates.
 */

import type { ExtensionAPI, ExtensionCommandContext } from "@earendil-works/pi-coding-agent";
import {
  CHATGPT_PREFIX,
  DATABRICKS_PREFIX,
  type ToolPolicy,
  activeToolsFor,
  pickModel,
  splitArgs,
} from "../src/passthrough";

async function runPassthrough(
  pi: ExtensionAPI,
  ctx: ExtensionCommandContext,
  label: string,
  prefix: string,
  toolPolicy: ToolPolicy,
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

  const tools = activeToolsFor(
    toolPolicy,
    pi.getAllTools().map((t) => t.name),
  );
  pi.setActiveTools(tools);
  ctx.ui.notify(
    toolPolicy === "none"
      ? `${label} pass-through: ${picked.id}, no local tools active.`
      : `${label}: ${picked.id}, ${tools.length} local tools active (can run bash/CLI).`,
    "info",
  );

  if (message) {
    pi.sendUserMessage(message);
  }
}

export default function passthrough(pi: ExtensionAPI): void {
  pi.registerCommand("chatgpt", {
    description: "Plain chatgpt.com web chat pass-through (no local tools)",
    handler: (args, ctx) => runPassthrough(pi, ctx, "chatgpt", CHATGPT_PREFIX, "none", args),
  });

  pi.registerCommand("genie", {
    description: "Databricks Genie/Claude agent (keeps local tools so it can act)",
    handler: (args, ctx) => runPassthrough(pi, ctx, "genie", DATABRICKS_PREFIX, "all", args),
  });
}
