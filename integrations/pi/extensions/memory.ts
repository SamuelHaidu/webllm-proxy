/**
 * `memory` tool: long-term retention for this project's discoveries, bugs,
 * plans, and progress -- backed natively by the `ai-memory` CLI (no MCP server
 * at runtime; see src/memoryClient.ts). One tool with an `action` switch keeps
 * the schema small for the LLM.
 *
 * Suggested path convention (not enforced): `discoveries/<topic>.md`,
 * `bugs/<slug>.md`, `plans/<slug>.md`, `progress/<slug>.md`.
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import {
  type ClientOptions,
  memoryStatus,
  readMemoryPage,
  searchMemory,
  writeMemoryPage,
} from "../src/memoryClient";

function formatHits(hits: Awaited<ReturnType<typeof searchMemory>>): string {
  if (hits.length === 0) return "(no matches)";
  return hits
    .map(
      (h) => `- ${h.path}${h.title ? ` — ${h.title}` : ""}${h.snippet ? `\n  ${h.snippet}` : ""}`,
    )
    .join("\n");
}

export default function memory(pi: ExtensionAPI): void {
  pi.registerTool({
    name: "memory",
    label: "Memory",
    description:
      "Long-term project memory: search, read, and write durable notes (discoveries, bugs, " +
      "plans, progress) that persist across sessions. Backed by the ai-memory store.",
    promptSnippet: "Search/read/write durable long-term project notes",
    promptGuidelines: [
      "Use memory (action=write) to record discoveries, bug reports, plans, and progress " +
        "notes that should survive across sessions -- prefer paths like " +
        "discoveries/<topic>.md, bugs/<slug>.md, plans/<slug>.md, progress/<slug>.md.",
      "Use memory (action=search or action=read) before re-deriving something that may " +
        "already be recorded from a previous session.",
    ],
    parameters: Type.Object({
      action: Type.Union([
        Type.Literal("search"),
        Type.Literal("read"),
        Type.Literal("write"),
        Type.Literal("status"),
      ]),
      query: Type.Optional(Type.String({ description: "search text, or read-page lookup query" })),
      path: Type.Optional(Type.String({ description: "exact wiki path, e.g. discoveries/foo.md" })),
      body: Type.Optional(Type.String({ description: "markdown body (action=write)" })),
      title: Type.Optional(Type.String()),
      kind: Type.Optional(
        Type.Union([
          Type.Literal("fact"),
          Type.Literal("rule"),
          Type.Literal("decision"),
          Type.Literal("gotcha"),
        ]),
      ),
      tags: Type.Optional(Type.Array(Type.String())),
      limit: Type.Optional(Type.Integer({ description: "max search hits (default 10)" })),
    }),
    async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
      const opts: ClientOptions = { cwd: ctx?.cwd };
      try {
        switch (params.action) {
          case "search": {
            if (!params.query) throw new Error("action=search requires query");
            const hits = await searchMemory(params.query, { ...opts, limit: params.limit });
            return { content: [{ type: "text", text: formatHits(hits) }], details: { hits } };
          }
          case "read": {
            const page = await readMemoryPage({ path: params.path, query: params.query }, opts);
            return { content: [{ type: "text", text: page.body }], details: { page } };
          }
          case "write": {
            if (!params.path || !params.body) {
              throw new Error("action=write requires path and body");
            }
            const confirmation = await writeMemoryPage(
              {
                path: params.path,
                body: params.body,
                title: params.title,
                kind: params.kind,
                tags: params.tags,
              },
              opts,
            );
            return { content: [{ type: "text", text: confirmation }], details: {} };
          }
          case "status": {
            const status = await memoryStatus(opts);
            return {
              content: [{ type: "text", text: JSON.stringify(status, null, 2) }],
              details: { status },
            };
          }
        }
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        return {
          content: [{ type: "text", text: `memory error: ${message}` }],
          details: {},
          isError: true,
        };
      }
    },
  });
}
