// Minimal resolve hook: stub the optional peers of @cortexkit/pi-magic-context
// so its raw storage chunk can be imported outside a Pi/OMP harness. Used only
// by migrate.mjs (the one-shot `magic-hermes db` driver). The TUI stub mirrors
// loader.mjs's stub shape.
const TUI_SOURCE = [
  "export class Box {}",
  "export class Text {}",
  "export const matchesKey = () => false;",
  "export const truncateToWidth = (value) => String(value ?? '');",
  "export const visibleWidth = (value) => String(value ?? '').length;",
].join("\n");
const STUB_URLS = new Map([
  [
    "@earendil-works/pi-tui",
    "data:text/javascript," + encodeURIComponent(TUI_SOURCE),
  ],
  ["@earendil-works/pi-coding-agent", "data:text/javascript,export default {};"],
  ["typebox", "data:text/javascript,export default {};"],
]);

export function resolve(specifier, context, nextResolve) {
  const stub = STUB_URLS.get(specifier);
  if (stub !== undefined) {
    return { url: stub, shortCircuit: true };
  }
  return nextResolve(specifier, context);
}
