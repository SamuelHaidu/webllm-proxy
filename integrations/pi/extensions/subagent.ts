/**
 * `subagent` tool: spawns a nested, read-only pi agent (in-process, via the
 * SDK) to explore the repository and write a compact code index the MAIN
 * agent can grep/read instead of re-exploring from scratch every session.
 *
 * v1 supports one action, `code_index`: writes `.pi/index/CODEINDEX.md`
 * (path -> one-line pointer per file/area). The nested agent reuses the
 * parent's active model (these are zero-metered web-login models) and only
 * gets read/grep/find/ls -- it can explore but never edit or run commands.
 *
 * Deliberately NOT unit-tested here (spinning up a real nested AgentSession
 * is an integration concern, not a pure-function one -- see
 * src/codeIndex.ts for the tested pure logic: prompt text + message
 * extraction). Verified instead by a `pi -e` load-smoke and a live run.
 */

import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import {
  type ExtensionAPI,
  SessionManager,
  createAgentSession,
} from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import {
  INDEX_RELATIVE_PATH,
  buildIndexPrompt,
  extractAssistantText,
  renderIndexFile,
} from "../src/codeIndex";

const READ_ONLY_TOOLS = ["read", "grep", "find", "ls"];

export default function subagent(pi: ExtensionAPI): void {
  pi.registerTool({
    name: "subagent",
    label: "Subagent",
    description:
      "Spawn a read-only nested agent to explore the repository and write a code index " +
      "(.pi/index/CODEINDEX.md) mapping files to a one-line pointer, for fast lookup later.",
    promptSnippet: "Spawn a read-only subagent to build/refresh the code index",
    promptGuidelines: [
      "Use subagent (action=code_index) once early in a session on an unfamiliar repo, or " +
        "when the codebase has changed a lot, to build .pi/index/CODEINDEX.md; then read " +
        "that file instead of re-exploring the whole tree from scratch.",
    ],
    parameters: Type.Object({
      action: Type.Optional(Type.Literal("code_index")),
      focus: Type.Optional(
        Type.String({ description: "optional area of the codebase to focus the index on" }),
      ),
    }),
    async execute(_toolCallId, params, _signal, onUpdate, ctx) {
      const cwd = ctx?.cwd ?? process.cwd();
      const model = ctx?.model;
      if (!model) {
        return {
          content: [
            { type: "text", text: "subagent error: no active model on the parent session" },
          ],
          details: {},
          isError: true,
        };
      }

      onUpdate?.({ content: [{ type: "text", text: "exploring the repository..." }], details: {} });

      const { session } = await createAgentSession({
        cwd,
        model,
        tools: READ_ONLY_TOOLS,
        sessionManager: SessionManager.inMemory(cwd),
      });
      try {
        await session.prompt(buildIndexPrompt(params.focus));
        const body = extractAssistantText(session.messages);
        if (!body.trim()) {
          return {
            content: [{ type: "text", text: "subagent produced no output" }],
            details: {},
            isError: true,
          };
        }
        const outPath = join(cwd, INDEX_RELATIVE_PATH);
        mkdirSync(dirname(outPath), { recursive: true });
        writeFileSync(outPath, renderIndexFile(body, { focus: params.focus }), "utf-8");

        const lineCount = body.trim().split("\n").length;
        return {
          content: [
            {
              type: "text",
              text: `Wrote ${INDEX_RELATIVE_PATH} (${lineCount} lines). Read that file for the index.`,
            },
          ],
          details: { path: INDEX_RELATIVE_PATH, lineCount },
        };
      } finally {
        session.dispose();
      }
    },
  });
}
