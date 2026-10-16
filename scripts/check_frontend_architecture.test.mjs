import assert from "node:assert/strict";
import test from "node:test";

import { checkFrontendArchitecture } from "./check_frontend_architecture.mjs";

test("accepts directed dependencies and type-only cycles", () => {
  const files = new Map([
    ["app/App.tsx", 'import { plan } from "@/features/planner/model";'],
    ["features/planner/model.ts", 'import type { Input } from "./types"; export const plan = 1;'],
    ["features/planner/types.ts", 'import type { Output } from "./model";'],
  ]);
  assert.deepEqual(checkFrontendArchitecture(files), []);
});

test("rejects layer inversion through relative imports and re-exports", () => {
  const files = new Map([
    ["shared/index.ts", 'export { plan } from "../features/planner/model";'],
    ["features/planner/model.ts", "export const plan = 1;"],
  ]);
  assert.match(checkFrontendArchitecture(files).join("\n"), /Layer violation/);
});

test("detects runtime cycles through dynamic imports", () => {
  const files = new Map([
    ["features/a.ts", 'export const next = () => import("./b");'],
    ["features/b.ts", 'import "./a";'],
  ]);
  assert.match(checkFrontendArchitecture(files).join("\n"), /Runtime import cycle/);
});

test("reports unresolved local imports", () => {
  const files = new Map([["app/App.tsx", 'import "@/removed/module";']]);
  assert.match(checkFrontendArchitecture(files).join("\n"), /Unresolved local import/);
});
