import { afterEach, describe, expect, it, vi } from "vitest";

// Mock node:child_process.execFile with a callback-style stub so memoryClient's
// `run()` (which wraps execFile in a manual Promise, not util.promisify) drives it
// exactly like the real API. No real process/docker is ever spawned.
const execFileMock = vi.fn();
vi.mock("node:child_process", () => ({
  execFile: (...args: unknown[]) => execFileMock(...args),
}));

// Imported AFTER the mock is registered (vi.mock is hoisted).
const { searchMemory, readMemoryPage, writeMemoryPage, memoryStatus } = await import(
  "../src/memoryClient"
);

function mockSuccess(stdout: string) {
  execFileMock.mockImplementation((_bin, _args, _opts, cb) => cb(null, stdout, ""));
}

function mockFailure(message: string) {
  execFileMock.mockImplementation((_bin, _args, _opts, cb) => cb(new Error(message)));
}

afterEach(() => execFileMock.mockReset());

describe("searchMemory", () => {
  it("passes query/limit and parses JSON hits", async () => {
    mockSuccess('[{"path":"a.md","title":"A","snippet":"...","rank":-1}]');
    const hits = await searchMemory("query text", { limit: 5 });
    expect(hits).toEqual([{ path: "a.md", title: "A", snippet: "...", rank: -1 }]);
    const [bin, args] = execFileMock.mock.calls[0];
    expect(bin).toBe("ai-memory");
    expect(args).toEqual(["search", "query text", "--json", "--limit", "5"]);
  });

  it("omits --limit when not given", async () => {
    mockSuccess("[]");
    await searchMemory("q");
    const [, args] = execFileMock.mock.calls[0];
    expect(args).toEqual(["search", "q", "--json"]);
  });
});

describe("readMemoryPage", () => {
  it("uses --path when given", async () => {
    mockSuccess('{"path":"a.md","workspace":"default","project":"p","title":null,"body":"hi"}');
    const page = await readMemoryPage({ path: "a.md" });
    expect(page.body).toBe("hi");
    const [, args] = execFileMock.mock.calls[0];
    expect(args).toEqual(["read-page", "--json", "--path", "a.md"]);
  });

  it("uses a positional query when path is absent", async () => {
    mockSuccess('{"path":"a.md","workspace":"d","project":"p","title":null,"body":"x"}');
    await readMemoryPage({ query: "find me" });
    const [, args] = execFileMock.mock.calls[0];
    expect(args).toEqual(["read-page", "--json", "find me"]);
  });

  it("throws when neither path nor query is given", async () => {
    await expect(readMemoryPage({})).rejects.toThrow(/requires either path or query/);
    expect(execFileMock).not.toHaveBeenCalled();
  });
});

describe("writeMemoryPage", () => {
  it("builds flags including repeatable tags and trims stdout", async () => {
    mockSuccess("✓ wrote a.md (page_id=1) under default/p\n");
    const result = await writeMemoryPage({
      path: "a.md",
      body: "body text",
      title: "Title",
      kind: "fact",
      tags: ["x", "y"],
      pinned: true,
    });
    expect(result).toBe("✓ wrote a.md (page_id=1) under default/p");
    const [, args] = execFileMock.mock.calls[0];
    expect(args).toEqual([
      "write-page",
      "--path",
      "a.md",
      "--body",
      "body text",
      "--title",
      "Title",
      "--kind",
      "fact",
      "--pinned",
      "--tag",
      "x",
      "--tag",
      "y",
    ]);
  });

  it("propagates a CLI failure", async () => {
    mockFailure("boom");
    await expect(writeMemoryPage({ path: "a.md", body: "b" })).rejects.toThrow("boom");
  });
});

describe("memoryStatus", () => {
  it("parses the status JSON", async () => {
    mockSuccess('{"version":"1.11.2","counts":{"pages_all":3}}');
    const status = await memoryStatus();
    expect(status.version).toBe("1.11.2");
    expect(status.counts?.pages_all).toBe(3);
    const [, args] = execFileMock.mock.calls[0];
    expect(args).toEqual(["status", "--json"]);
  });
});
