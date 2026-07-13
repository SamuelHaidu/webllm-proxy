/**
 * Bridges the `chatgpt_agent.md` tag protocol to pi's NATIVE agent loop, for
 * the `webllm-agent` custom provider (extensions/chatgpt-agent.ts). Unlike the
 * old `/chatgpt --agent` command (which drove its own side loop and printed
 * `notify()` toasts), here pi runs the loop: the model's prose becomes a real
 * assistant message and its one tag becomes a real tool call that pi executes
 * with its own `read`/`write`/`edit`/`bash`/`find`/`grep` tools -- rendered,
 * approved, and streamed exactly like any other model.
 *
 * This module is the pure glue (no pi/SDK VALUE imports, only `import type`, so
 * it unit-tests standalone):
 *   - `buildUpstreamMessages` rebuilds the tag conversation to send to the
 *     gateway from pi's message history (assistant tool calls -> their tag,
 *     tool results -> `<result>`).
 *   - `splitReply` separates the model's prose from its one tag.
 *   - `actionToToolCall` maps a parsed tag to a native pi tool call.
 *   - `emitReplyEvents` turns one reply into the provider event sequence.
 */

import type {
  AssistantMessage,
  AssistantMessageEvent,
  ImageContent,
  Message,
  TextContent,
  ToolCall,
} from "@earendil-works/pi-ai";
import {
  type Action,
  buildFirstMessage,
  matchFirstAction,
  renderResult,
  stripNbsp,
} from "./agentTags";

/** Native pi built-in tool names each tag maps onto. */
type PiToolName = "read" | "write" | "edit" | "bash" | "find" | "grep";

interface NativeToolCall {
  name: PiToolName;
  arguments: Record<string, unknown>;
}

/** `"1-40"` (1-indexed inclusive) -> pi read tool's `{offset, limit}`. */
function parseLineRange(lines?: string): { offset?: number; limit?: number } {
  if (!lines) return {};
  const [a, b] = lines.split("-", 2).map((s) => Number.parseInt(s.trim(), 10));
  if (!Number.isFinite(a)) return {};
  const end = Number.isFinite(b) ? b : a;
  return { offset: a, limit: Math.max(1, end - a + 1) };
}

/** Reconstruct a `lines="a-b"` attribute from a read tool's offset/limit. */
function lineRangeAttr(offset?: unknown, limit?: unknown): string {
  if (typeof offset !== "number") return "";
  const end = typeof limit === "number" ? offset + limit - 1 : offset;
  return ` lines="${offset}-${end}"`;
}

/** Map a parsed tag to the native pi tool call pi will actually execute. */
export function actionToToolCall(action: Action): NativeToolCall {
  switch (action.kind) {
    case "read_file":
      return { name: "read", arguments: { path: action.path, ...parseLineRange(action.lines) } };
    case "create_file":
      return { name: "write", arguments: { path: action.path, content: action.body } };
    case "edit_file":
      return {
        name: "edit",
        arguments: { path: action.path, edits: [{ oldText: action.old, newText: action.new }] },
      };
    case "bash":
      return { name: "bash", arguments: { command: action.cmd } };
    case "find":
      return { name: "find", arguments: { pattern: action.glob } };
    case "search":
      return {
        name: "grep",
        arguments: action.path
          ? { pattern: action.regex, path: action.path }
          : { pattern: action.regex },
      };
  }
}

function str(v: unknown): string {
  return typeof v === "string" ? v : v == null ? "" : String(v);
}

/** Reverse of `actionToToolCall`: render a stored native tool call back into
 * the tag the model "said", to replay its own history to the gateway. */
export function toolCallToTag(name: string, args: Record<string, unknown>): string {
  switch (name) {
    case "read":
      return `<read_file path="${str(args.path)}"${lineRangeAttr(args.offset, args.limit)}/>`;
    case "write":
      return `<create_file path="${str(args.path)}">${str(args.content)}</create_file>`;
    case "edit": {
      const first = Array.isArray(args.edits)
        ? (args.edits[0] as Record<string, unknown>)
        : undefined;
      const old = str(first?.oldText ?? args.oldText);
      const next = str(first?.newText ?? args.newText);
      return `<edit_file path="${str(args.path)}"><old>${old}</old><new>${next}</new></edit_file>`;
    }
    case "bash":
      return `<bash>${str(args.command)}</bash>`;
    case "find":
      return `<find>${str(args.pattern)}</find>`;
    case "grep":
      return args.path
        ? `<search path="${str(args.path)}">${str(args.pattern)}</search>`
        : `<search>${str(args.pattern)}</search>`;
    default:
      // A tool the model shouldn't have (it only knows the six tags); replay it
      // faithfully enough that history stays coherent.
      return `<${name}>${JSON.stringify(args)}</${name}>`;
  }
}

function extractText(content: string | (TextContent | ImageContent)[]): string {
  if (typeof content === "string") return content;
  return content
    .filter((c): c is TextContent => c.type === "text")
    .map((c) => c.text)
    .join("\n");
}

export interface UpstreamMessage {
  role: "user" | "assistant";
  content: string;
}

/**
 * Rebuild the tag conversation to POST to the gateway from pi's message list:
 * the first user turn carries the full prompt (+ project tree) and a
 * `<request>`; later user turns are `<request>`; assistant turns are replayed
 * as their prose + tag; tool results become `<result>` user turns.
 */
export function buildUpstreamMessages(
  messages: Message[],
  promptText: string,
  tree: string,
): UpstreamMessage[] {
  const out: UpstreamMessage[] = [];
  let firstUser = true;
  for (const m of messages) {
    if (m.role === "user") {
      const text = extractText(m.content);
      out.push({
        role: "user",
        content: firstUser
          ? buildFirstMessage(promptText, tree, text)
          : `<request>${text}</request>`,
      });
      firstUser = false;
    } else if (m.role === "assistant") {
      const parts: string[] = [];
      for (const block of m.content) {
        if (block.type === "text" && block.text.trim()) parts.push(block.text.trim());
        else if (block.type === "toolCall") parts.push(toolCallToTag(block.name, block.arguments));
      }
      if (parts.length > 0) out.push({ role: "assistant", content: parts.join("\n\n") });
    } else if (m.role === "toolResult") {
      out.push({ role: "user", content: renderResult(extractText(m.content)) });
    }
  }
  return out;
}

/** Strip the ```text fences the prompt asks prose to live in, so it renders as
 * clean assistant text. */
function unwrapProse(text: string): string {
  return text
    .trim()
    .replace(/^```[a-zA-Z]*\n?/, "")
    .replace(/\n?```$/, "")
    .trim();
}

/** Split one raw model reply into its prose (assistant text) and its single
 * action tag (`null` when the reply is a plain final answer). */
export function splitReply(reply: string): { prose: string; action: Action | null } {
  const text = stripNbsp(reply);
  const match = matchFirstAction(text);
  if (!match) return { prose: unwrapProse(text), action: null };
  const prose = unwrapProse(text.slice(0, match.start) + text.slice(match.end));
  return { prose, action: match.action };
}

/**
 * Turn one model reply into the provider's event sequence via `push`, updating
 * `output.content` in place (pi renders `partial` as it streams). Returns the
 * stop reason: `"toolUse"` when the model emitted a tag (pi will execute it and
 * call the provider again), else `"stop"` (final answer).
 */
export function emitReplyEvents(
  reply: string,
  output: AssistantMessage,
  push: (event: AssistantMessageEvent) => void,
  newToolCallId: () => string,
): "toolUse" | "stop" {
  const { prose, action } = splitReply(reply);

  if (prose) {
    const block: TextContent = { type: "text", text: "" };
    output.content.push(block);
    const index = output.content.length - 1;
    push({ type: "text_start", contentIndex: index, partial: output });
    block.text = prose;
    push({ type: "text_delta", contentIndex: index, delta: prose, partial: output });
    push({ type: "text_end", contentIndex: index, content: prose, partial: output });
  }

  if (action) {
    const { name, arguments: args } = actionToToolCall(action);
    const toolCall: ToolCall = { type: "toolCall", id: newToolCallId(), name, arguments: args };
    output.content.push(toolCall);
    const index = output.content.length - 1;
    push({ type: "toolcall_start", contentIndex: index, partial: output });
    push({
      type: "toolcall_delta",
      contentIndex: index,
      delta: JSON.stringify(args),
      partial: output,
    });
    push({ type: "toolcall_end", contentIndex: index, toolCall, partial: output });
    return "toolUse";
  }

  return "stop";
}
