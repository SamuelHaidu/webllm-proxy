/**
 * Pure logic for the `/chatgpt` and `/genie` pass-through commands: picking
 * the right `webllm` model out of the registry, with no pi/SDK import so it
 * unit-tests standalone. The actual `pi.setModel`/`pi.setActiveTools` calls
 * live in extensions/passthrough.ts.
 */

export const PROVIDER = "webllm";
export const CHATGPT_PREFIX = "chatgpt__";
export const DATABRICKS_PREFIX = "databricks__";

/** Structural subset of a pi `Model` -- deliberately loose so this stays
 *  unit-testable without the SDK's real (generic) `Model<Api>` type. */
export interface MinimalModel {
  id: string;
  provider: string;
}

/** All `webllm` models namespaced under the given upstream prefix
 *  (`chatgpt__`/`databricks__`), sorted by id for a deterministic pick. */
export function candidateModels<T extends MinimalModel>(models: T[], prefix: string): T[] {
  return models
    .filter((m) => m.provider === PROVIDER && m.id.startsWith(prefix))
    .sort((a, b) => a.id.localeCompare(b.id));
}

/**
 * Pick a model for a pass-through command: an explicit `wantedId` (matched
 * exactly against the namespaced id) wins if present among the candidates;
 * otherwise the first candidate (alphabetical) is used. undefined if there
 * are no candidates at all.
 */
export function pickModel<T extends MinimalModel>(
  models: T[],
  prefix: string,
  wantedId?: string,
): T | undefined {
  const candidates = candidateModels(models, prefix);
  if (candidates.length === 0) return undefined;
  if (wantedId) {
    const exact = candidates.find((m) => m.id === wantedId || m.id === `${prefix}${wantedId}`);
    if (exact) return exact;
  }
  return candidates[0];
}

/**
 * Splits `/chatgpt [model-id] [message...]`-style args into an optional
 * leading model-id token and the rest as the message. A token is only taken
 * as a model id if it exactly matches one of `knownIds` -- otherwise the
 * whole argument string is treated as the message (so "/chatgpt what is 2+2"
 * doesn't mistake "what" for a model id).
 */
export function splitArgs(
  args: string,
  knownIds: string[],
): { modelId?: string; message?: string } {
  const trimmed = args.trim();
  if (!trimmed) return {};
  const [first, ...rest] = trimmed.split(/\s+/);
  if (knownIds.includes(first)) {
    const message = rest.join(" ").trim();
    return { modelId: first, message: message || undefined };
  }
  return { message: trimmed };
}
