/**
 * `research` tool: run a ChatGPT web deep-research job through webllm-proxy and
 * return a structured markdown report. Slow (minutes); progress notes stream via
 * `onUpdate`. Requires the gateway (`webllm-proxy gateway`) fronting a chatgpt
 * proxy that exposes the research backend.
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { gatewayBaseUrl } from "../src/gateway";
import { getResearch, submitResearch } from "../src/researchClient";

const POLL_MS = 2000;

export default function research(pi: ExtensionAPI): void {
  pi.registerTool({
    name: "research",
    label: "Deep Research",
    description:
      "Run a ChatGPT web deep-research job via webllm-proxy and return a structured " +
      "markdown report. Use for open-ended, multi-source questions that need browsing.",
    promptSnippet: "Run a ChatGPT deep-research job and return a markdown report",
    promptGuidelines: [
      "Use research for open-ended questions that need web browsing across multiple " +
        "sources; it is slow (minutes), so prefer it only for genuinely broad tasks.",
    ],
    parameters: Type.Object({
      query: Type.String({ description: "The research question" }),
      depth: Type.Optional(
        Type.String({ description: "Optional depth hint passed to the backend" }),
      ),
    }),
    async execute(_toolCallId, params, signal, onUpdate) {
      const base = gatewayBaseUrl();
      const job = await submitResearch(base, params.query, params.depth);
      let seen = 0;
      while (!signal?.aborted) {
        const j = await getResearch(base, job.id);
        const notes = j.progress ?? [];
        for (const note of notes.slice(seen)) {
          onUpdate?.({ content: [{ type: "text", text: note }], details: {} });
        }
        seen = notes.length;
        if (j.status === "succeeded") {
          return { content: [{ type: "text", text: j.report ?? "" }], details: { jobId: job.id } };
        }
        if (j.status === "failed") {
          return {
            content: [{ type: "text", text: `research failed: ${j.error ?? "unknown error"}` }],
            details: { jobId: job.id },
            isError: true,
          };
        }
        await new Promise((resolve) => setTimeout(resolve, POLL_MS));
      }
      throw new Error("research aborted");
    },
  });
}
