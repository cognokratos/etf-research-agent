/**
 * Replay the frozen NAT 1.9 SSE corpus through the real chat route.
 *
 * `contracts/nat-1.9/sse/` was recorded from the real NAT agent. Each stream is
 * handed to `app/api/gateway/chat/route.ts` as the gateway's response body, and
 * the UI message chunks the route writes are asserted. Runs with plain Node and
 * no install: `npm run verify:nat-corpus`, or `make verify-nat-corpus`.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { register } from "node:module";

register("./nat-corpus-hooks.mjs", import.meta.url);

const corpus = new URL("../../contracts/nat-1.9/sse/", import.meta.url);
const { POST } = await import("../app/api/gateway/chat/route.ts");

async function replay(name) {
  const body = readFileSync(new URL(`${name}.sse`, corpus));
  globalThis.fetch = async () => new Response(body, { status: 200, headers: { "content-type": "text/event-stream" } });
  const request = new Request("http://ui.test/api/gateway/chat", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ messages: [{ id: "1", role: "user", parts: [{ type: "text", text: "replayed" }] }] }),
  });
  return POST(request);
}

const answer = (chunks) => chunks.filter((c) => c.type === "text-delta").map((c) => c.delta).join("");
const tools = (chunks, type) => chunks.filter((c) => c.type === type);
const finish = (chunks) => chunks.find((c) => c.type === "finish")?.finishReason;
let passed = 0;
async function check(name, fn) {
  await fn();
  passed += 1;
  console.log(`PASS: ${name}`);
}

await check("scalar answer chunks survive the route verbatim", async () => {
  const chunks = await replay("read-tools-scalar-chunks");
  assert.equal(
    answer(chunks),
    "CSPX-LSE: the deterministic engine decided shortlist with an investment score of 86 out of 100. true 1e3 0 — a research decision only.",
  );
  assert.equal(finish(chunks), "stop");
});

await check("MCP tools arrive as FUNCTION_* events and render as tool cards by suffix", async () => {
  const chunks = await replay("read-tools-scalar-chunks");
  assert.deepEqual(tools(chunks, "tool-input-available").map((c) => c.toolName), ["get_etf", "evaluate_etf"]);
  assert.deepEqual(tools(chunks, "tool-input-available")[0].input, { etf: "CSPX-LSE" });
  const evaluation = tools(chunks, "tool-output-available")[1].output;
  assert.equal(evaluation.evaluation.decision, "shortlist");
});

await check("guardrail decision events never become tool cards", async () => {
  const chunks = await replay("input-rail-block");
  assert.equal(tools(chunks, "tool-input-available").length, 0);
  assert.equal(answer(chunks), "I'm sorry, I can't respond to that.");
});

await check("an interaction_required event becomes a human_approval card, closed when the stream ends", async () => {
  const chunks = await replay("commit-confirm-radio");
  const approval = tools(chunks, "tool-input-available").find((c) => c.toolName === "human_approval");
  assert.ok(approval, "approval card");
  assert.equal(approval.input.prompt.input_type, "radio");
  assert.deepEqual(approval.input.prompt.options.map((o) => [o.id, o.value]), [
    ["reject", "reject"], ["research", "research"], ["shortlist", "shortlist"], ["cancel", "__CANCEL__"],
  ]);
  const closed = tools(chunks, "tool-output-available").find((c) => c.toolCallId === approval.toolCallId);
  assert.deepEqual(closed.output, { resolved: true });
  assert.ok(tools(chunks, "tool-input-available").some((c) => c.toolName === "commit_evaluation"));
});

await check("a binary assign prompt is surfaced with its boolean options", async () => {
  const chunks = await replay("assign-confirm-binary");
  const approval = tools(chunks, "tool-input-available").find((c) => c.toolName === "human_approval");
  assert.equal(approval.input.prompt.input_type, "binary_choice");
  assert.deepEqual(approval.input.prompt.options.map((o) => o.value), [true, false]);
});

await check("each of several prompts in one run gets its own card", async () => {
  const chunks = await replay("commit-human-promotion");
  const cards = tools(chunks, "tool-input-available").filter((c) => c.toolName === "human_approval");
  assert.deepEqual(cards.map((c) => c.input.prompt.input_type), ["radio", "text", "text"]);
});

await check("a bare workflow_error block ends the message with an error", async () => {
  const chunks = await replay("workflow-error-provider");
  const error = chunks.find((c) => c.type === "error");
  assert.match(error.errorText, /Error code: 500 - scripted provider failure/);
  assert.equal(finish(chunks), "error");
});

await check("known gap: an `event: error` frame is silently dropped", async () => {
  const chunks = await replay("session-error-frame");
  assert.equal(chunks.find((c) => c.type === "error"), undefined);
  assert.equal(answer(chunks), "");
  assert.equal(finish(chunks), "stop");
});

await check("an output-rail block shows only the refusal text", async () => {
  const chunks = await replay("output-rail-block");
  assert.equal(answer(chunks), "Blocked by regex check output rails.");
});

console.log(`${passed} NAT corpus checks passed`);
