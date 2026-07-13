import { describe, expect, it } from "vitest";
import { type WebllmSettings, resolveAgentMode, withAgentMode } from "../src/agentSettings";

const ON: WebllmSettings = { webllm: { chatgptAgentMode: true } };
const OFF: WebllmSettings = { webllm: { chatgptAgentMode: false } };

describe("resolveAgentMode", () => {
  it("env override wins and is truthy unless a falsey token", () => {
    for (const v of ["1", "true", "yes", "on"]) {
      expect(resolveAgentMode(v, null, null)).toBe(true);
    }
    for (const v of ["0", "false", "no", "off", ""]) {
      expect(resolveAgentMode(v, ON, ON)).toBe(false);
    }
  });

  it("uses the project setting when no env is set", () => {
    expect(resolveAgentMode(undefined, ON, null)).toBe(true);
    expect(resolveAgentMode(undefined, OFF, ON)).toBe(false);
  });

  it("falls back to the global setting when the project has none", () => {
    expect(resolveAgentMode(undefined, null, ON)).toBe(true);
    expect(resolveAgentMode(undefined, {}, ON)).toBe(true);
  });

  it("defaults to off when nothing is configured", () => {
    expect(resolveAgentMode(undefined, null, null)).toBe(false);
    expect(resolveAgentMode(undefined, {}, {})).toBe(false);
  });

  it("ignores non-boolean values", () => {
    expect(resolveAgentMode(undefined, { webllm: { chatgptAgentMode: "yes" } }, null)).toBe(false);
  });
});

describe("withAgentMode", () => {
  it("sets the nested flag on an empty object", () => {
    expect(withAgentMode({}, true)).toEqual({ webllm: { chatgptAgentMode: true } });
  });

  it("preserves other top-level settings and other webllm keys", () => {
    const before = { theme: "dark", webllm: { gatewayUrl: "x", chatgptAgentMode: false } };
    expect(withAgentMode(before, true)).toEqual({
      theme: "dark",
      webllm: { gatewayUrl: "x", chatgptAgentMode: true },
    });
  });

  it("does not mutate the input", () => {
    const before = { webllm: { chatgptAgentMode: false } };
    withAgentMode(before, true);
    expect(before).toEqual({ webllm: { chatgptAgentMode: false } });
  });
});
