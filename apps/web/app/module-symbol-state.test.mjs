import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import React, { useCallback, useEffect, useRef, useState } from "react";
import TestRenderer, { act } from "react-test-renderer";
import ts from "typescript";

const sourcePath = new URL("./workbench.tsx", import.meta.url);
const productionSource = await readFile(sourcePath, "utf8");
const sourceFile = ts.createSourceFile(sourcePath.pathname, productionSource, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function findProductionCallback(name) {
  let found;
  function visit(node) {
    if (ts.isVariableDeclaration(node) && node.name.getText(sourceFile) === name && ts.isCallExpression(node.initializer)) {
      found = node.initializer.arguments[0].getText(sourceFile);
    }
    ts.forEachChild(node, visit);
  }
  visit(sourceFile);
  assert.ok(found, `production callback ${name} exists`);
  return found;
}

function findIndexOverviewEffect() {
  let found;
  function visit(node) {
    if (ts.isCallExpression(node) && node.expression.getText(sourceFile) === "useEffect" && node.arguments[0]?.getText(sourceFile).includes("loadOverview(selectedProjectId, indexId)")) {
      found = { callback: node.arguments[0].getText(sourceFile), dependencies: node.arguments[1].getText(sourceFile) };
    }
    ts.forEachChild(node, visit);
  }
  visit(sourceFile);
  assert.ok(found, "production index-change overview effect exists");
  return found;
}

const resetSource = findProductionCallback("resetModuleSymbolSource");
const resetList = findProductionCallback("resetModuleSymbolListState");
const resetAll = findProductionCallback("resetModuleSymbols");
const loadOverview = findProductionCallback("loadOverview");
const indexEffect = findIndexOverviewEffect();

const componentTs = `
function WorkbenchLifecycleSeam({ scope, sourceRead, observe }) {
  const [indexResponse, setIndexResponse] = useState(null);
  const [panelMode, setPanelMode] = useState("none");
  const [sourceStatus, setSourceStatus] = useState("idle");
  const [sourceResponse, setSourceResponse] = useState(null);
  const [sourceError, setSourceError] = useState(null);
  const [sourceGenerationId, setSourceGenerationId] = useState(null);
  const [selectedId, setSelectedId] = useState(null);
  const [linkedSymbol, setLinkedSymbol] = useState(null);
  const [moduleSymbolsRoot, setModuleSymbolsRoot] = useState({ symbol_id: "old" });
  const [moduleSymbolsPage, setModuleSymbolsPage] = useState({ items: ["old"] });
  const [moduleSymbolsStatus, setModuleSymbolsStatus] = useState("success");
  const [moduleSymbolsError, setModuleSymbolsError] = useState("old");
  const [moduleSymbolsRetry, setModuleSymbolsRetry] = useState({ cursor: "old", append: true });
  const [, setOverviewStatus] = useState("idle");
  const [, setOverviewError] = useState(null);
  const [, setOverview] = useState(null);
  const [, setSelectedOverviewEntryId] = useState(null);
  const [, setSelectedOverviewGroupId] = useState(null);
  const sourceController = useRef(null);
  const sourceRequestId = useRef(0);
  const moduleSymbolsController = useRef(null);
  const moduleSymbolsRequestId = useRef(0);
  const overviewController = useRef(null);
  const overviewRequestId = useRef(0);
  const isLatestRequest = (requestId, currentRequestId) => requestId === currentRequestId;
  const isAbortError = (error) => error?.name === "AbortError";
  const errorMessage = (error) => String(error);
  const resetModuleSymbolSource = useCallback(${resetSource}, []);
  const resetModuleSymbolListState = useCallback(${resetList}, []);
  const resetModuleSymbols = useCallback(${resetAll}, [resetModuleSymbolListState, resetModuleSymbolSource]);
  const loadOverview = useCallback(${loadOverview}, [resetModuleSymbolListState]);
  const selectedProjectId = scope.projectId;
  useEffect(${indexEffect.callback}, ${indexEffect.dependencies});
  useEffect(() => {
    setIndexResponse({ index: { index_id: scope.indexId } });
    const controller = new AbortController();
    sourceController.current = controller;
    setPanelMode("source");
    setSelectedId("evidence");
    setSourceStatus("loading");
    void sourceRead(controller.signal).then((value) => {
      if (!controller.signal.aborted) { setSourceResponse(value); setSourceStatus("success"); }
    }).catch((error) => {
      if (!controller.signal.aborted) { setSourceError(String(error)); setSourceStatus("error"); }
    });
    return () => controller.abort();
  }, [scope.projectId, scope.indexId, sourceRead]);
  useEffect(() => observe({ panelMode, sourceStatus, sourceResponse, sourceError, selectedId,
    moduleSymbolsRoot, moduleSymbolsPage, moduleSymbolsStatus, moduleSymbolsError, moduleSymbolsRetry,
    sourceRequestId: sourceRequestId.current, moduleSymbolsRequestId: moduleSymbolsRequestId.current,
    resetModuleSymbolSource, resetModuleSymbols }),
    [panelMode, sourceStatus, sourceResponse, sourceError, selectedId, moduleSymbolsRoot, moduleSymbolsPage,
      moduleSymbolsStatus, moduleSymbolsError, moduleSymbolsRetry, resetModuleSymbolSource, resetModuleSymbols]);
  return React.createElement("state", null, JSON.stringify({ panelMode, sourceStatus, sourceResponse, sourceError,
    moduleSymbolsStatus, moduleSymbolsRoot, moduleSymbolsPage }));
}`;
const transpiled = ts.transpile(componentTs, { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS });
const createSeam = new Function("React", "useCallback", "useEffect", "useRef", "useState", "getProjectOverview", `${transpiled}; return WorkbenchLifecycleSeam;`)(
  React, useCallback, useEffect, useRef, useState,
  async () => ({ entries: [], overview_id: "overview" }),
);

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

function mount(scope = { projectId: "p1", indexId: "i1" }) {
  const read = deferred();
  let state;
  const sourceRead = (signal) => {
    signal.addEventListener("abort", () => { state = { ...state, aborted: true }; });
    return read.promise;
  };
  let root;
  act(() => { root = TestRenderer.create(React.createElement(createSeam, { scope, sourceRead, observe: (next) => { state = { ...state, ...next }; } })); });
  return { root, read, state: () => state };
}

test("mounted index effect preserves restored source read and list-only overview reset", async () => {
  const mounted = mount();
  await act(async () => { await Promise.resolve(); });
  assert.equal(mounted.state().panelMode, "source");
  assert.equal(mounted.state().sourceStatus, "loading");
  assert.equal(mounted.state().moduleSymbolsStatus, "idle");
  await act(async () => { mounted.read.resolve({ content: "restored citation" }); await mounted.read.promise; await Promise.resolve(); });
  assert.equal(mounted.state().panelMode, "source");
  assert.equal(mounted.state().sourceStatus, "success");
  assert.equal(mounted.state().sourceResponse.content, "restored citation");
  assert.equal(mounted.state().aborted, undefined);
  act(() => mounted.root.unmount());
});

test("production source close aborts the pending read and clears only the source panel", async () => {
  const mounted = mount();
  await act(async () => { await Promise.resolve(); });
  act(() => mounted.state().resetModuleSymbolSource());
  assert.equal(mounted.state().aborted, true);
  assert.equal(mounted.state().panelMode, "none");
  assert.equal(mounted.state().sourceStatus, "idle");
  assert.equal(mounted.state().moduleSymbolsStatus, "idle");
  act(() => mounted.root.unmount());
});

test("production project/index invalidation clears module list and source scope", async () => {
  const mounted = mount();
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
  const requestId = mounted.state().moduleSymbolsRequestId;
  act(() => mounted.state().resetModuleSymbols());
  assert.equal(mounted.state().aborted, true);
  assert.equal(mounted.state().panelMode, "none");
  assert.equal(mounted.state().moduleSymbolsRoot, null);
  assert.equal(mounted.state().moduleSymbolsPage, null);
  assert.equal(mounted.state().moduleSymbolsStatus, "idle");
  assert.equal(mounted.state().moduleSymbolsRequestId, requestId + 1);
  act(() => mounted.root.unmount());
});

test("project or index remount aborts the previous citation request", async () => {
  const mounted = mount();
  await act(async () => { await Promise.resolve(); });
  act(() => mounted.root.update(React.createElement(createSeam, {
    scope: { projectId: "p2", indexId: "i2" },
    sourceRead: (signal) => {
      signal.addEventListener("abort", () => { mounted.scopeAbort = true; });
      return new Promise(() => {});
    },
    observe: (next) => { mounted.latest = next; },
  })));
  assert.equal(mounted.state().aborted, true);
  assert.equal(JSON.parse(mounted.root.toJSON().children[0]).panelMode, "source");
  act(() => mounted.root.unmount());
});
