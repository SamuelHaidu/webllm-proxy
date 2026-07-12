import { afterEach, describe, expect, it, vi } from "vitest";
import { getResearch, isTerminal, submitResearch } from "../src/researchClient";

afterEach(() => vi.restoreAllMocks());

function mockFetch(
  handler: (url: string, init?: RequestInit) => { ok?: boolean; status?: number; body: unknown },
) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const r = handler(url, init);
      return {
        ok: r.ok ?? true,
        status: r.status ?? 200,
        json: async () => r.body,
      } as unknown as Response;
    }),
  );
}

describe("submitResearch", () => {
  it("posts query+depth and returns the job", async () => {
    let captured: { url: string; body: unknown } | undefined;
    mockFetch((url, init) => {
      captured = { url, body: JSON.parse(String(init?.body)) };
      return { body: { id: "j1", status: "queued" } };
    });
    const job = await submitResearch("http://gw", "why is the sky blue", "deep");
    expect(job.id).toBe("j1");
    expect(captured?.url).toBe("http://gw/v1/research");
    expect(captured?.body).toEqual({ query: "why is the sky blue", depth: "deep" });
  });

  it("omits depth when not given", async () => {
    let body: unknown;
    mockFetch((_url, init) => {
      body = JSON.parse(String(init?.body));
      return { body: { id: "j", status: "queued" } };
    });
    await submitResearch("http://gw", "q");
    expect(body).toEqual({ query: "q" });
  });

  it("throws on non-ok submit", async () => {
    mockFetch(() => ({ ok: false, status: 502, body: {} }));
    await expect(submitResearch("http://gw", "q")).rejects.toThrow(/502/);
  });
});

describe("getResearch", () => {
  it("polls a job by id", async () => {
    mockFetch((url) => {
      expect(url).toBe("http://gw/v1/research/j1");
      return { body: { id: "j1", status: "succeeded", report: "# Report" } };
    });
    const j = await getResearch("http://gw", "j1");
    expect(j.report).toBe("# Report");
  });
});

describe("isTerminal", () => {
  it("is true only for succeeded/failed", () => {
    expect(isTerminal("succeeded")).toBe(true);
    expect(isTerminal("failed")).toBe(true);
    expect(isTerminal("running")).toBe(false);
    expect(isTerminal("queued")).toBe(false);
  });
});
