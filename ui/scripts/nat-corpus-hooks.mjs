/**
 * Module hooks for verify-nat-corpus.mjs, so the real chat route can run under
 * plain Node with no npm install:
 *
 * - `ai` resolves to a recording sink (`createUIMessageStream` runs `execute`
 *   with a writer that collects chunks). Only the AI SDK's output plumbing is
 *   replaced; every line of SSE parsing is the route's own.
 * - extensionless relative imports (`../../../../lib/nat-wire`) resolve to `.ts`,
 *   as Next's bundler would.
 */
const SINK = `data:text/javascript,${encodeURIComponent(`
export function createUIMessageStream({ execute }) { return { execute }; }
export async function createUIMessageStreamResponse({ stream }) {
  const chunks = [];
  await stream.execute({ writer: { write: (chunk) => chunks.push(chunk) } });
  return chunks;
}
`)}`;

export async function resolve(specifier, context, nextResolve) {
  if (specifier === "ai") return { url: SINK, shortCircuit: true };
  if (specifier.startsWith(".") && !/\.[cm]?[jt]s$/.test(specifier)) {
    try {
      return await nextResolve(`${specifier}.ts`, context);
    } catch {
      // fall through to the default resolution
    }
  }
  return nextResolve(specifier, context);
}
