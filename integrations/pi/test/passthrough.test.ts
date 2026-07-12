import { describe, expect, it } from "vitest";
import {
  CHATGPT_PREFIX,
  DATABRICKS_PREFIX,
  candidateModels,
  pickModel,
  splitArgs,
} from "../src/passthrough";

const MODELS = [
  { id: "chatgpt__gpt-5", provider: "webllm" },
  { id: "chatgpt__gpt-5-thinking", provider: "webllm" },
  { id: "databricks__claude-4-5-sonnet", provider: "webllm" },
  { id: "databricks__gpt-41-2025-04-14", provider: "webllm" },
  { id: "claude-sonnet-4-5", provider: "anthropic" },
];

describe("candidateModels", () => {
  it("filters to webllm models under the given prefix, sorted", () => {
    const c = candidateModels(MODELS, CHATGPT_PREFIX);
    expect(c.map((m) => m.id)).toEqual(["chatgpt__gpt-5", "chatgpt__gpt-5-thinking"]);
  });

  it("excludes non-webllm provider models even if the id matches", () => {
    const decoy = [...MODELS, { id: "chatgpt__decoy", provider: "other" }];
    const c = candidateModels(decoy, CHATGPT_PREFIX);
    expect(c.some((m) => m.id === "chatgpt__decoy")).toBe(false);
  });

  it("returns [] when there are no candidates", () => {
    expect(candidateModels(MODELS, "copilot__")).toEqual([]);
  });
});

describe("pickModel", () => {
  it("picks the first candidate (alphabetical) with no wantedId", () => {
    expect(pickModel(MODELS, DATABRICKS_PREFIX)?.id).toBe("databricks__claude-4-5-sonnet");
  });

  it("picks an exact wantedId match among candidates", () => {
    expect(pickModel(MODELS, DATABRICKS_PREFIX, "databricks__gpt-41-2025-04-14")?.id).toBe(
      "databricks__gpt-41-2025-04-14",
    );
  });

  it("accepts a bare slug (without the provider prefix) for wantedId", () => {
    expect(pickModel(MODELS, DATABRICKS_PREFIX, "gpt-41-2025-04-14")?.id).toBe(
      "databricks__gpt-41-2025-04-14",
    );
  });

  it("falls back to the first candidate when wantedId matches nothing", () => {
    expect(pickModel(MODELS, DATABRICKS_PREFIX, "nope")?.id).toBe("databricks__claude-4-5-sonnet");
  });

  it("returns undefined when there are no candidates at all", () => {
    expect(pickModel(MODELS, "copilot__", "anything")).toBeUndefined();
  });
});

describe("splitArgs", () => {
  const knownIds = ["chatgpt__gpt-5", "chatgpt__gpt-5-thinking"];

  it("returns {} for empty args", () => {
    expect(splitArgs("", knownIds)).toEqual({});
    expect(splitArgs("   ", knownIds)).toEqual({});
  });

  it("treats a leading known model id as modelId, rest as message", () => {
    expect(splitArgs("chatgpt__gpt-5 hello there", knownIds)).toEqual({
      modelId: "chatgpt__gpt-5",
      message: "hello there",
    });
  });

  it("treats a leading known model id with no message as modelId only", () => {
    expect(splitArgs("chatgpt__gpt-5", knownIds)).toEqual({ modelId: "chatgpt__gpt-5" });
  });

  it("treats the whole string as a message when the first token isn't a known id", () => {
    expect(splitArgs("what is 2+2", knownIds)).toEqual({ message: "what is 2+2" });
  });
});
