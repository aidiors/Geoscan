import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

import ts from "typescript";

const allowedLayers = {
  shared: new Set(["shared"]),
  features: new Set(["shared", "features"]),
  widgets: new Set(["shared", "features", "widgets"]),
  app: new Set(["shared", "features", "widgets", "app"]),
};

function localTarget(file, specifier, sources) {
  const base = specifier.startsWith("@/")
    ? specifier.slice(2)
    : path.posix.normalize(path.posix.join(path.posix.dirname(file), specifier));
  return [base, `${base}.ts`, `${base}.tsx`, `${base}/index.ts`, `${base}/index.tsx`]
    .find((candidate) => sources.has(candidate));
}

function runtimeImport(node) {
  if (ts.isImportDeclaration(node)) {
    const clause = node.importClause;
    if (!clause) return true;
    if (clause.isTypeOnly) return false;
    if (clause.name || !clause.namedBindings || ts.isNamespaceImport(clause.namedBindings)) return true;
    return clause.namedBindings.elements.some((element) => !element.isTypeOnly);
  }
  if (ts.isExportDeclaration(node)) {
    if (node.isTypeOnly) return false;
    if (node.exportClause && ts.isNamedExports(node.exportClause)) {
      return node.exportClause.elements.some((element) => !element.isTypeOnly);
    }
  }
  return true;
}

export function checkFrontendArchitecture(sources) {
  const errors = [];
  const graph = new Map([...sources.keys()].map((file) => [file, new Set()]));

  for (const [file, source] of sources) {
    const tree = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true);
    const layer = file.split("/")[0];

    function checkImport(specifier, runtime) {
      if (!specifier.startsWith("@/") && !specifier.startsWith(".")) return;
      // Styles and media are build assets, not TypeScript modules.
      if (/\.(?:css|svg|png|jpe?g|webp)$/.test(specifier)) return;
      const target = localTarget(file, specifier, sources);
      if (!target) {
        errors.push(`Unresolved local import: ${file} -> ${specifier}`);
        return;
      }
      const targetLayer = target.split("/")[0];
      if (allowedLayers[layer] && !allowedLayers[layer].has(targetLayer)) {
        errors.push(`Layer violation: ${file} -> ${target}`);
      }
      if (runtime) graph.get(file).add(target);
    }

    function visit(node) {
      if ((ts.isImportDeclaration(node) || ts.isExportDeclaration(node))
        && node.moduleSpecifier && ts.isStringLiteral(node.moduleSpecifier)) {
        checkImport(node.moduleSpecifier.text, runtimeImport(node));
      } else if (ts.isCallExpression(node) && node.expression.kind === ts.SyntaxKind.ImportKeyword
        && node.arguments.length === 1 && ts.isStringLiteral(node.arguments[0])) {
        checkImport(node.arguments[0].text, true);
      }
      ts.forEachChild(node, visit);
    }
    visit(tree);
  }

  const visited = new Set();
  const active = [];
  function visitModule(file) {
    if (active.includes(file)) {
      errors.push(`Runtime import cycle: ${[...active, file].join(" -> ")}`);
      return;
    }
    if (visited.has(file)) return;
    active.push(file);
    for (const dependency of graph.get(file)) visitModule(dependency);
    active.pop();
    visited.add(file);
  }
  for (const file of graph.keys()) visitModule(file);
  return errors;
}

function readSources(root, directory = root, sources = new Map()) {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const absolute = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      readSources(root, absolute, sources);
    } else if (/\.tsx?$/.test(entry.name) && !/\.(?:test|spec)\.tsx?$/.test(entry.name)) {
      const relative = path.relative(root, absolute).split(path.sep).join("/");
      sources.set(relative, fs.readFileSync(absolute, "utf8"));
    }
  }
  return sources;
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) {
  const root = path.resolve(import.meta.dirname, "../frontend/src");
  const sources = readSources(root);
  const errors = checkFrontendArchitecture(sources);
  if (errors.length) {
    console.error(errors.join("\n"));
    process.exitCode = 1;
  } else {
    console.log(`Frontend architecture passed: ${sources.size} modules, layers and runtime cycles.`);
  }
}
