import type { AssistantMessage, AssistantMessageEvent, Message } from "@earendil-works/pi-ai";
import { describe, expect, it } from "vitest";
import {
  actionToToolCall,
  buildUpstreamMessages,
  emitReplyEvents,
  splitReply,
  toolCallToTag,
} from "../src/agentProtocol";

describe("actionToToolCall", () => {
  it("maps each tag to the matching native pi tool", () => {
    expect(actionToToolCall({ kind: "read_file", path: "a.py" })).toEqual({
      name: "read",
      arguments: { path: "a.py" },
    });
    expect(actionToToolCall({ kind: "read_file", path: "a.py", lines: "1-40" })).toEqual({
      name: "read",
      arguments: { path: "a.py", offset: 1, limit: 40 },
    });
    expect(actionToToolCall({ kind: "create_file", path: "t.py", body: "x" })).toEqual({
      name: "write",
      arguments: { path: "t.py", content: "x" },
    });
    expect(actionToToolCall({ kind: "edit_file", path: "m.py", old: "a", new: "b" })).toEqual({
      name: "edit",
      arguments: { path: "m.py", edits: [{ oldText: "a", newText: "b" }] },
    });
    expect(actionToToolCall({ kind: "bash", cmd: "ls" })).toEqual({
      name: "bash",
      arguments: { command: "ls" },
    });
    expect(actionToToolCall({ kind: "find", glob: "*.py" })).toEqual({
      name: "find",
      arguments: { pattern: "*.py" },
    });
    expect(actionToToolCall({ kind: "search", regex: "foo", path: "src/" })).toEqual({
      name: "grep",
      arguments: { pattern: "foo", path: "src/" },
    });
    expect(actionToToolCall({ kind: "search", regex: "foo" })).toEqual({
      name: "grep",
      arguments: { pattern: "foo" },
    });
  });
});

describe("toolCallToTag", () => {
  it("renders native tool calls back to their tag form", () => {
    expect(toolCallToTag("read", { path: "a.py" })).toBe('<read_file path="a.py"/>');
    expect(toolCallToTag("read", { path: "a.py", offset: 1, limit: 40 })).toBe(
      '<read_file path="a.py" lines="1-40"/>',
    );
    expect(toolCallToTag("write", { path: "t.py", content: "x" })).toBe(
      '<create_file path="t.py">x</create_file>',
    );
    expect(toolCallToTag("edit", { path: "m.py", edits: [{ oldText: "a", newText: "b" }] })).toBe(
      '<edit_file path="m.py"><old>a</old><new>b</new></edit_file>',
    );
    expect(toolCallToTag("bash", { command: "ls" })).toBe("<bash>ls</bash>");
    expect(toolCallToTag("find", { pattern: "*.py" })).toBe("<find>*.py</find>");
    expect(toolCallToTag("grep", { pattern: "foo", path: "src/" })).toBe(
      '<search path="src/">foo</search>',
    );
  });
});

describe("splitReply", () => {
  it("returns the action and no prose for a bare tag", () => {
    expect(splitReply('<read_file path="main.py"/>')).toEqual({
      prose: "",
      action: { kind: "read_file", path: "main.py", lines: undefined },
    });
  });

  it("separates a ```text prose note from the trailing tag", () => {
    const reply = '```text\nLet me look first.\n```\n<read_file path="main.py"/>';
    const { prose, action } = splitReply(reply);
    expect(prose).toBe("Let me look first.");
    expect(action?.kind).toBe("read_file");
  });

  it("treats a tag-less reply as a final answer (prose only)", () => {
    expect(splitReply("```text\nAll done.\n```")).toEqual({ prose: "All done.", action: null });
  });

  it("normalizes NBSP indentation in a create_file body", () => {
    const nbsp = " ";
    const reply = `<create_file path="t.py">def f():\n${nbsp} ${nbsp} return 1\n</create_file>`;
    const { action } = splitReply(reply);
    expect(action).toEqual({
      kind: "create_file",
      path: "t.py",
      body: "def f():\n    return 1\n",
    });
  });
});

describe("buildUpstreamMessages", () => {
  const PROMPT = "PROMPT <<PROJECT_TREE>> END";
  const TREE = "proj/\n  main.py";

  it("wraps the first user turn with the prompt+tree+request", () => {
    const messages = [{ role: "user", content: "write tests", timestamp: 0 }] as Message[];
    const out = buildUpstreamMessages(messages, PROMPT, TREE);
    expect(out).toHaveLength(1);
    expect(out[0].role).toBe("user");
    expect(out[0].content).toContain("PROMPT proj/\n  main.py END");
    expect(out[0].content).toContain("<request>write tests</request>");
  });

  it("replays assistant tool calls as tags and tool results as <result>", () => {
    const messages = [
      { role: "user", content: "go", timestamp: 0 },
      {
        role: "assistant",
        content: [
          { type: "text", text: "reading" },
          { type: "toolCall", id: "1", name: "read", arguments: { path: "main.py" } },
        ],
        api: "x",
        provider: "webllm-agent",
        model: "m",
        usage: {},
        stopReason: "toolUse",
        timestamp: 0,
      },
      {
        role: "toolResult",
        toolCallId: "1",
        toolName: "read",
        content: [{ type: "text", text: "def fib(): ..." }],
        isError: false,
        timestamp: 0,
      },
      { role: "user", content: "now what", timestamp: 0 },
    ] as unknown as Message[];

    const out = buildUpstreamMessages(messages, PROMPT, TREE);
    expect(out.map((m) => m.role)).toEqual(["user", "assistant", "user", "user"]);
    expect(out[1].content).toBe('reading\n\n<read_file path="main.py"/>');
    expect(out[2].content).toBe("<result>\ndef fib(): ...\n</result>");
    expect(out[3].content).toBe("<request>now what</request>");
  });
});

describe("emitReplyEvents", () => {
  function freshOutput(): AssistantMessage {
    return { content: [] } as unknown as AssistantMessage;
  }

  it("emits text then a tool call and returns toolUse", () => {
    const events: AssistantMessageEvent[] = [];
    const output = freshOutput();
    const reason = emitReplyEvents(
      '```text\nreading\n```\n<read_file path="a.py"/>',
      output,
      (e) => events.push(e),
      () => "tid",
    );
    expect(reason).toBe("toolUse");
    expect(events.map((e) => e.type)).toEqual([
      "text_start",
      "text_delta",
      "text_end",
      "toolcall_start",
      "toolcall_delta",
      "toolcall_end",
    ]);
    expect(output.content).toEqual([
      { type: "text", text: "reading" },
      { type: "toolCall", id: "tid", name: "read", arguments: { path: "a.py" } },
    ]);
  });

  it("emits only text and returns stop for a final answer", () => {
    const events: AssistantMessageEvent[] = [];
    const output = freshOutput();
    const reason = emitReplyEvents(
      "```text\ndone\n```",
      output,
      (e) => events.push(e),
      () => "tid",
    );
    expect(reason).toBe("stop");
    expect(events.map((e) => e.type)).toEqual(["text_start", "text_delta", "text_end"]);
    expect(output.content).toEqual([{ type: "text", text: "done" }]);
  });
});
