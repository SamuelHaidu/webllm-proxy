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
  /** Which HTTP surface this model actually needs. Missing/unknown means
   *  "openai" (the gateway's /v1/chat/completions). "anthropic" means it is
   *  Anthropic-Messages-only (the gateway's /v1/messages) -- e.g. databricks'
   *  Claude models, which 404 on /v1/chat/completions. */
  _wire?: "openai" | "anthropic";
  /** Whether the upstream declares this model reasoning/extended-thinking
   *  capable. Authoritative when present (the proxy knows its models); when
   *  absent we fall back to the id/title heuristic (`isReasoning`). This is what
   *  makes pi actually emit a `thinking` block -- e.g. databricks' Claude, whose
   *  id has no "think"/"reason" keyword for the heuristic to catch. */
  _reasoning?: boolean;
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
  /** Per-model API override; only set for non-default wire (anthropic-messages).
   *  Absent means "use the provider's default (openai-completions)". */
  api?: "anthropic-messages";
  /** Per-model baseUrl override paired with `api` above: the gateway ROOT (no
   *  `/v1`) -- the Anthropic SDK appends `/v1/messages` itself, unlike the
   *  provider-level openai-completions baseUrl which already includes `/v1`. */
  baseUrl?: string;
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

/**
 * @param gatewayRoot the gateway's root URL with NO trailing `/v1` (e.g.
 *   "http://127.0.0.1:5100"), used only to build the per-model baseUrl
 *   override for anthropic-wire models. Irrelevant for openai-wire models.
 */
export function mapModel(model: GatewayModel, gatewayRoot?: string): PiModel {
  const maxTokens =
    typeof model._max_tokens === "number" && model._max_tokens > 0
      ? model._max_tokens
      : DEFAULT_MAX_TOKENS;
  const isAnthropic = model._wire === "anthropic";
  return {
    id: model.id,
    name: model._title ?? model.id,
    reasoning: model._reasoning ?? isReasoning(model),
    input: ["text"],
    cost: { ...ZERO_COST },
    contextWindow: DEFAULT_CONTEXT,
    maxTokens,
    ...(isAnthropic ? { api: "anthropic-messages" as const, baseUrl: gatewayRoot } : {}),
  };
}

export function mapModels(models: GatewayModel[], gatewayRoot?: string): PiModel[] {
  return models
    .filter((m) => typeof m.id === "string" && m.id.length > 0)
    .map((m) => mapModel(m, gatewayRoot));
}
