/**
 * The `streamSimple` implementation for the `webllm-agent` custom provider:
 * one call = one agent turn. It rebuilds the tag conversation from pi's
 * `context`, POSTs a single plain-chat turn to the aggregator gateway (no tools
 * -- so the proxy doesn't inject its own `<tool>` contract), then emits the
 * model's reply as native pi events: prose as assistant text, the one tag as a
 * native tool call (`agentProtocol.emitReplyEvents`). pi executes the tool with
 * its own tools and calls us again with the result -- a fully native loop.
 *
 * The event-stream plumbing lives here; the transformation logic is the pure,
 * unit-tested `./agentProtocol`. Validated end-to-end by a live run rather than
 * a unit test (a real gateway turn, like the built-in providers' own suites).
 */

import { randomUUID } from "node:crypto";
import {
  type Api,
  type AssistantMessage,
  type AssistantMessageEventStream,
  type Context,
  type Model,
  type SimpleStreamOptions,
  createAssistantMessageEventStream,
} from "@earendil-works/pi-ai";
import { buildUpstreamMessages, emitReplyEvents } from "./agentProtocol";
import { buildProjectTree } from "./agentTags";

export interface AgentStreamConfig {
  /** `chatgpt_agent.md` contents (with the `<<PROJECT_TREE>>` placeholder). */
  promptText: string;
  /** Project root, for the prompt's file tree and where pi runs its tools. */
  cwd: string;
  /** Aggregator gateway base URL, e.g. `http://127.0.0.1:5100`. */
  gatewayBaseUrl: string;
}

function zeroUsage(): AssistantMessage["usage"] {
  return {
    input: 0,
    output: 0,
    cacheRead: 0,
    cacheWrite: 0,
    totalTokens: 0,
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
  };
}

async function callGateway(
  base: string,
  model: string,
  messages: Array<{ role: string; content: string }>,
  signal?: AbortSignal,
): Promise<string> {
  const res = await fetch(`${base}/v1/chat/completions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model, messages, stream: false }),
    signal,
  });
  if (!res.ok) {
    throw new Error(`gateway ${res.status}: ${(await res.text()).slice(0, 300)}`);
  }
  const data = (await res.json()) as {
    choices?: Array<{ message?: { content?: string } }>;
  };
  return data.choices?.[0]?.message?.content ?? "";
}

/** Build the `webllm-agent` provider's `streamSimple` bound to `cfg`. */
export function createChatgptAgentStream(cfg: AgentStreamConfig) {
  let treeCache: string | undefined;
  const tree = () => {
    treeCache ??= buildProjectTree(cfg.cwd);
    return treeCache;
  };

  return function stream(
    model: Model<Api>,
    context: Context,
    options?: SimpleStreamOptions,
  ): AssistantMessageEventStream {
    const out = createAssistantMessageEventStream();

    (async () => {
      const output: AssistantMessage = {
        role: "assistant",
        content: [],
        api: model.api,
        provider: model.provider,
        model: model.id,
        usage: zeroUsage(),
        stopReason: "stop",
        timestamp: Date.now(),
      };
      try {
        out.push({ type: "start", partial: output });
        const messages = buildUpstreamMessages(context.messages, cfg.promptText, tree());
        const reply = await callGateway(cfg.gatewayBaseUrl, model.id, messages, options?.signal);
        const reason = emitReplyEvents(
          reply,
          output,
          (event) => out.push(event),
          () => `chatgpt-agent-${randomUUID()}`,
        );
        output.stopReason = reason;
        out.push({ type: "done", reason, message: output });
        out.end();
      } catch (error) {
        output.stopReason = options?.signal?.aborted ? "aborted" : "error";
        output.errorMessage = error instanceof Error ? error.message : String(error);
        out.push({ type: "error", reason: output.stopReason, error: output });
        out.end();
      }
    })();

    return out;
  };
}
