import { describe, expect, it } from "vitest";
import { type GatewayModel, isReasoning, mapModel, mapModels } from "../src/models";

describe("mapModels", () => {
  it("maps id/title and uses the upstream max_tokens", () => {
    const [m] = mapModels([{ id: "chatgpt__gpt-5", _title: "GPT-5", _max_tokens: 32000 }]);
    expect(m.id).toBe("chatgpt__gpt-5");
    expect(m.name).toBe("GPT-5");
    expect(m.maxTokens).toBe(32000);
    expect(m.contextWindow).toBe(128000);
    expect(m.input).toEqual(["text"]);
    expect(m.cost.input).toBe(0);
  });

  it("falls back name->id and maxTokens->default", () => {
    const [m] = mapModels([{ id: "databricks__claude" }]);
    expect(m.name).toBe("databricks__claude");
    expect(m.maxTokens).toBe(8192);
  });

  it("drops entries without an id", () => {
    const bad = { id: "" } as GatewayModel;
    expect(mapModels([bad, { id: "x__y" }]).length).toBe(1);
  });
});

describe("_wire handling", () => {
  it("leaves api/baseUrl unset for openai-wire (default) models", () => {
    const m = mapModel({ id: "databricks__gpt-41-2025-04-14", _wire: "openai" }, "http://gw:5100");
    expect(m.api).toBeUndefined();
    expect(m.baseUrl).toBeUndefined();
  });

  it("leaves api/baseUrl unset when _wire is absent", () => {
    const m = mapModel({ id: "chatgpt__gpt-5" }, "http://gw:5100");
    expect(m.api).toBeUndefined();
    expect(m.baseUrl).toBeUndefined();
  });

  it("overrides api to anthropic-messages and baseUrl to the gateway root for anthropic-wire models", () => {
    const m = mapModel(
      { id: "databricks__claude-4-5-sonnet", _wire: "anthropic" },
      "http://gw:5100",
    );
    expect(m.api).toBe("anthropic-messages");
    expect(m.baseUrl).toBe("http://gw:5100");
  });
});

describe("_reasoning handling", () => {
  it("uses the explicit _reasoning=true hint even when the id has no keyword", () => {
    // databricks Claude: no "think"/"reason" in the id, but the proxy declares it.
    const m = mapModel({ id: "databricks__claude-4-5-sonnet", _reasoning: true });
    expect(m.reasoning).toBe(true);
  });

  it("uses the explicit _reasoning=false hint even when the id has a keyword", () => {
    const m = mapModel({ id: "databricks__reasoner-x", _reasoning: false });
    expect(m.reasoning).toBe(false);
  });

  it("falls back to the id/title heuristic when _reasoning is absent", () => {
    expect(mapModel({ id: "chatgpt__gpt-5-thinking" }).reasoning).toBe(true);
    expect(mapModel({ id: "chatgpt__gpt-4o" }).reasoning).toBe(false);
  });
});

describe("isReasoning", () => {
  it("detects think/reason/research/deep variants", () => {
    expect(isReasoning({ id: "copilot__think" })).toBe(true);
    expect(isReasoning({ id: "chatgpt__x", _title: "Deep Research" })).toBe(true);
    expect(mapModel({ id: "copilot__think" }).reasoning).toBe(true);
  });

  it("is false for plain chat models", () => {
    expect(isReasoning({ id: "chatgpt__gpt-4o" })).toBe(false);
    expect(mapModel({ id: "chatgpt__gpt-4o" }).reasoning).toBe(false);
  });
});
