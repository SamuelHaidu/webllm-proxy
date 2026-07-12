/** Client for the proxy's async deep-research job API (the gateway forwards
 *  `/v1/research` to the chatgpt provider). Submit returns a job; poll it to a
 *  terminal status. Pure fetch calls -- the polling loop lives in the tool. */

export interface ResearchJob {
  id: string;
  status: "queued" | "running" | "succeeded" | "failed" | string;
  report?: string;
  error?: string;
  progress?: string[];
}

export async function submitResearch(
  base: string,
  query: string,
  depth?: string,
): Promise<ResearchJob> {
  const res = await fetch(`${base}/v1/research`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(depth ? { query, depth } : { query }),
  });
  if (!res.ok) throw new Error(`research submit failed (${res.status})`);
  return (await res.json()) as ResearchJob;
}

export async function getResearch(base: string, id: string): Promise<ResearchJob> {
  const res = await fetch(`${base}/v1/research/${encodeURIComponent(id)}`);
  if (!res.ok) throw new Error(`research poll failed (${res.status})`);
  return (await res.json()) as ResearchJob;
}

export function isTerminal(status: string): boolean {
  return status === "succeeded" || status === "failed";
}
