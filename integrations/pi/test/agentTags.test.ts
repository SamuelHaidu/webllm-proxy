import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { buildFirstMessage, buildProjectTree, parseAction, renderResult } from "../src/agentTags";

describe("parseAction", () => {
  it("returns null (final answer) for a plain-text reply", () => {
    expect(parseAction("```text\nAll done, tests pass.\n```")).toBeNull();
  });

  it("parses a self-closing read_file tag", () => {
    expect(parseAction('<read_file path="main.py"/>')).toEqual({
      kind: "read_file",
      path: "main.py",
      lines: undefined,
    });
  });

  it("parses read_file with a line range", () => {
    expect(parseAction('<read_file path="a.py" lines="1-40"/>')).toEqual({
      kind: "read_file",
      path: "a.py",
      lines: "1-40",
    });
  });

  it("parses a non-self-closing read_file tag leniently", () => {
    expect(parseAction('<read_file path="a.py">')).toEqual({
      kind: "read_file",
      path: "a.py",
      lines: undefined,
    });
  });

  it("parses create_file with multi-line body", () => {
    const reply =
      '<create_file path="tests/test_main.py">import unittest\n\nclass X: pass</create_file>';
    expect(parseAction(reply)).toEqual({
      kind: "create_file",
      path: "tests/test_main.py",
      body: "import unittest\n\nclass X: pass",
    });
  });

  it("normalizes ChatGPT web's NBSP-indentation artifact to plain spaces", () => {
    // Observed live: chatgpt.com's code-block rendering leaks alternating
    // U+00A0 (non-breaking space) + regular-space pairs into the raw API
    // text for indentation, which crashes Python with "invalid non-printable
    // character U+00A0" if written to disk verbatim.
    const nbsp = "\u00a0";
    const reply = `<create_file path="t.py">class X:\n${nbsp} ${nbsp} def f(self):\n${nbsp} ${nbsp} ${nbsp} ${nbsp} return 1\n</create_file>`;
    const action = parseAction(reply);
    expect(action).toEqual({
      kind: "create_file",
      path: "t.py",
      body: "class X:\n    def f(self):\n        return 1\n",
    });
    expect(action?.kind === "create_file" && action.body).not.toContain(nbsp);
  });

  it("parses edit_file with old/new sub-tags", () => {
    const reply =
      '<edit_file path="main.py"><old>def a():\n    pass</old><new>def a():\n    return 1</new></edit_file>';
    expect(parseAction(reply)).toEqual({
      kind: "edit_file",
      path: "main.py",
      old: "def a():\n    pass",
      new: "def a():\n    return 1",
    });
  });

  it("defaults missing old/new to empty strings rather than throwing", () => {
    const reply = '<edit_file path="main.py"><new>x</new></edit_file>';
    expect(parseAction(reply)).toEqual({ kind: "edit_file", path: "main.py", old: "", new: "x" });
  });

  it("parses bash", () => {
    expect(parseAction("<bash>python -m unittest discover -s tests -v</bash>")).toEqual({
      kind: "bash",
      cmd: "python -m unittest discover -s tests -v",
    });
  });

  it("parses find with no attrs", () => {
    expect(parseAction("<find>webllm_proxy/**/*.py</find>")).toEqual({
      kind: "find",
      glob: "webllm_proxy/**/*.py",
    });
  });

  it("parses search with an optional path attr", () => {
    expect(parseAction('<search path="src/">build_preamble</search>')).toEqual({
      kind: "search",
      regex: "build_preamble",
      path: "src/",
    });
    expect(parseAction("<search>build_preamble</search>")).toEqual({
      kind: "search",
      regex: "build_preamble",
      path: undefined,
    });
  });

  it("picks the earliest tag when a reply contains stray extra text before it", () => {
    const reply = 'Sure, let me check.\n<read_file path="main.py"/>\nand then more.';
    const action = parseAction(reply);
    expect(action?.kind).toBe("read_file");
  });

  it("prefers whichever real tag appears first when multiple types are present", () => {
    // Pathological reply (shouldn't happen given the one-action-per-turn prompt,
    // but the earliest-match rule should still be deterministic).
    const reply = '<bash>ls</bash><read_file path="a.py"/>';
    expect(parseAction(reply)?.kind).toBe("bash");
  });
});

describe("renderResult", () => {
  it("wraps text in a <result> block", () => {
    expect(renderResult("hello")).toBe("<result>\nhello\n</result>");
  });

  it("truncates very long output", () => {
    const big = "x".repeat(5000);
    const out = renderResult(big);
    expect(out).toContain("...(truncated)");
    expect(out.length).toBeLessThan(big.length);
  });
});

describe("buildProjectTree", () => {
  let dir: string;

  afterEach(() => {
    if (dir) rmSync(dir, { recursive: true, force: true });
  });

  it("lists files and directories, directories first, skipping vendor dirs", () => {
    dir = mkdtempSync(join(tmpdir(), "agent-tags-test-"));
    writeFileSync(join(dir, "main.py"), "");
    writeFileSync(join(dir, "README.md"), "");
    mkdirSync(join(dir, "tests"));
    writeFileSync(join(dir, "tests", "test_main.py"), "");
    mkdirSync(join(dir, "node_modules"));
    writeFileSync(join(dir, "node_modules", "should_be_skipped.js"), "");

    const tree = buildProjectTree(dir);
    expect(tree).toMatch(/main\.py/);
    expect(tree).toMatch(/tests\//);
    expect(tree).toMatch(/test_main\.py/);
    expect(tree).not.toMatch(/node_modules/);
    expect(tree).not.toMatch(/should_be_skipped/);
    // directories sort before files at the same level
    const lines = tree.split("\n").map((l) => l.trim());
    expect(lines.indexOf("tests/")).toBeLessThan(lines.indexOf("main.py"));
  });
});

describe("buildFirstMessage", () => {
  it("fills the project-tree placeholder and appends the request tag", () => {
    const msg = buildFirstMessage(
      "prompt with <<PROJECT_TREE>> here",
      "a/\n  b.py",
      "do the thing",
    );
    expect(msg).toContain("prompt with a/\n  b.py here");
    expect(msg).toContain("<request>do the thing</request>");
    expect(msg).not.toContain("<<PROJECT_TREE>>");
  });
});
