/**
 * Pure mapping from the aggregator gateway's OpenAI `/v1/models` payload to pi
 * provider model configs. No pi import here, so it unit-tests standalone.
 *
 * The gateway namespaces every id as `<provider>__<slug>` and passes through the
 * proxy's optional `_title` / `_max_tokens` hints (see webllm_proxy/gateway).
 */

export interface GatewayModel {
  /** namespaced id, e.g. "chatgpt__gpt-5" */
  id: string;
  _title?: string;
  _max_tokens?: number;
  _provider?: string;
  object?: string;
}

export interface PiModel {
  id: string;
  name: string;
  reasoning: boolean;
  input: ("text" | "image")[];
  cost: { input: number; output: number; cacheRead: number; cacheWrite: number };
  contextWindow: number;
  maxTokens: number;
}

const ZERO_COST = { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 };
const DEFAULT_CONTEXT = 128_000;
const DEFAULT_MAX_TOKENS = 8_192;

// Heuristic: web "think / reasoning / research / deep / oN" variants are reasoning
// models. Cost is 0 (these are login-web models with no metered API pricing).
const REASONING = /think|reason|research|deep|\bo[134]\b/i;

export function isReasoning(model: GatewayModel): boolean {
  return REASONING.test(model.id) || REASONING.test(model._title ?? "");
}

export function mapModel(model: GatewayModel): PiModel {
  const maxTokens =
    typeof model._max_tokens === "number" && model._max_tokens > 0
      ? model._max_tokens
      : DEFAULT_MAX_TOKENS;
  return {
    id: model.id,
    name: model._title ?? model.id,
    reasoning: isReasoning(model),
    input: ["text"],
    cost: { ...ZERO_COST },
    contextWindow: DEFAULT_CONTEXT,
    maxTokens,
  };
}

export function mapModels(models: GatewayModel[]): PiModel[] {
  return models.filter((m) => typeof m.id === "string" && m.id.length > 0).map(mapModel);
}
