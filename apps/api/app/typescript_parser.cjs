const fs = require("node:fs");
const path = require("node:path");

const compilerPath = process.env.CODETRAIL_TYPESCRIPT_COMPILER || path.resolve(__dirname, "../../web/node_modules/typescript/lib/typescript.js");
const ts = require(compilerPath);
const compilerVersion = "5.9.3";
const maxDefinitions = 20000;
const maxImports = 20000;
const maxRequests = 10000;
const maxCalls = 5000;

if (ts.version !== compilerVersion) throw new Error("locked TypeScript compiler version mismatch");

const input = JSON.parse(fs.readFileSync(0, "utf8"));
if (!input || !Array.isArray(input.files) || input.files.length > 2000) {
  throw new Error("invalid parser input");
}

let definitionCount = 0;
let importCount = 0;
let requestCount = 0;
let callCount = 0;
let callBudgetTruncated = false;
let uiActionBudgetTruncated = false;
let uiPropBindingBudgetTruncated = false;
const files = input.files.map(({ path: filePath, text }) => {
  if (typeof filePath !== "string" || typeof text !== "string") throw new Error("invalid source record");
  const source = ts.createSourceFile(
    filePath,
    text,
    ts.ScriptTarget.Latest,
    true,
    filePath.toLowerCase().endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
  );
  const diagnostic = source.parseDiagnostics[0];
  if (diagnostic) {
    const line = diagnostic.start == null ? 1 : source.getLineAndCharacterOfPosition(diagnostic.start).line + 1;
    return {
      path: filePath,
      syntax_error: { line, message: ts.flattenDiagnosticMessageText(diagnostic.messageText, " ").slice(0, 1000) },
    };
  }

  const definitions = [];
  const imports = [];
  const http_requests = [];
  const ui_actions = [];
  const ui_prop_bindings = [];
  let ui_actions_omitted = 0;
  let ui_prop_bindings_omitted = 0;
  const calls = [];
  const exports = [];
  const range = (node) => {
    const start = source.getLineAndCharacterOfPosition(node.getStart(source)).line + 1;
    const end = source.getLineAndCharacterOfPosition(Math.max(node.getStart(source), node.end - 1)).line + 1;
    return { start_line: start, end_line: end };
  };
  const addImport = (node, specifier, kind) => {
    if (imports.length >= maxImports || ++importCount > maxImports) throw new Error("TypeScript import budget exceeded");
    imports.push({ specifier: String(specifier).slice(0, 1000), kind, ...range(node) });
  };
  const declarationName = (node) => {
    if (ts.isConstructorDeclaration(node)) return "constructor";
    if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name)) return node.name.text;
    if (node.name && (ts.isIdentifier(node.name) || ts.isPrivateIdentifier(node.name))) return node.name.text;
    return null;
  };
  const isFunctionValue = (node) => ts.isArrowFunction(node) || ts.isFunctionExpression(node);
  const functionValueFor = (declaration) => {
    const initializer = declaration.initializer;
    if (initializer && isFunctionValue(initializer)) return initializer;
    if (!initializer || !ts.isCallExpression(initializer)) return null;
    const values = initializer.arguments.filter(isFunctionValue);
    return values.length === 1 ? values[0] : null;
  };
  const isCallableBinding = (declaration) =>
    ts.isFunctionDeclaration(declaration) ||
    ts.isVariableDeclaration(declaration) && Boolean(declaration.initializer) && isFunctionValue(declaration.initializer);
  const functionInfos = [];
  const moduleName = filePath.replace(/\\/g, "/").replace(/\.(tsx?|mts|cts)$/i, "").replaceAll("/", ".");
  const visitDefinitions = (node, parentNames, scopeNode = source) => {
    let nextNames = parentNames;
    let nextScopeNode = scopeNode;
    let kind = null;
    if (ts.isClassDeclaration(node)) kind = "class";
    else if (
      ts.isFunctionDeclaration(node) ||
      ts.isMethodDeclaration(node) ||
      ts.isConstructorDeclaration(node) ||
      ts.isGetAccessorDeclaration(node) ||
      ts.isSetAccessorDeclaration(node) ||
      ts.isVariableDeclaration(node) && functionValueFor(node)
    ) kind = "function";
    if (kind) {
      const name = declarationName(node);
      if (name) {
        const qualified_name = [...parentNames, name].join(".");
        if (qualified_name.length > 500 || ++definitionCount > maxDefinitions) {
          throw new Error("TypeScript definition budget exceeded");
        }
        definitions.push({ qualified_name, kind, ...range(node) });
        nextNames = [...parentNames, name];
        if (kind === "function") {
          const functionNode = ts.isVariableDeclaration(node) ? functionValueFor(node) : node;
          const statementNode = ts.isVariableDeclaration(node) ? node.parent.parent : node;
          const directBinding = scopeNode === source
            ? statementNode.parent === source
            : Boolean(scopeNode.body && statementNode.parent === scopeNode.body);
          const info = {
            qualified_name,
            parent_names: parentNames,
            start_line: range(node).start_line,
            declaration: node,
            function_node: functionNode,
            callable_binding: isCallableBinding(node),
            direct_binding: directBinding,
            named_binding: ts.isFunctionDeclaration(node) || ts.isVariableDeclaration(node),
            immutable_binding: ts.isFunctionDeclaration(node) ||
              ts.isVariableDeclaration(node) && Boolean(node.parent.flags & ts.NodeFlags.Const),
          };
          functionInfos.push(info);
          nextScopeNode = functionNode;
        }
      }
    }
    if (ts.isClassDeclaration(node)) nextScopeNode = node;
    ts.forEachChild(node, (child) => visitDefinitions(child, nextNames, nextScopeNode));
  };
  visitDefinitions(source, [moduleName]);

  const topLevel = source.statements;
  const requestWrappers = topLevel.filter(
    (node) => ts.isFunctionDeclaration(node) && node.name && node.name.text === "requestJson",
  );
  const baseBindings = topLevel.flatMap((node) =>
    ts.isVariableStatement(node)
      ? node.declarationList.declarations.filter((item) => ts.isIdentifier(item.name) && item.name.text === "apiBaseUrl")
      : [],
  );
  const bindingNames = (binding) => {
    const names = [];
    const collect = (item) => {
      if (ts.isIdentifier(item)) names.push(item.text);
      else if (ts.isObjectBindingPattern(item) || ts.isArrayBindingPattern(item)) {
        for (const element of item.elements) if (ts.isBindingElement(element)) collect(element.name);
      }
    };
    collect(binding);
    return names;
  };
  const isCallableNode = (node) => ts.isFunctionDeclaration(node) || ts.isFunctionExpression(node) ||
    ts.isArrowFunction(node) || ts.isMethodDeclaration(node) || ts.isConstructorDeclaration(node) ||
    ts.isGetAccessorDeclaration(node) || ts.isSetAccessorDeclaration(node);
  const isExported = (node) => Boolean(ts.canHaveModifiers(node) &&
    ts.getModifiers(node)?.some((modifier) => modifier.kind === ts.SyntaxKind.ExportKeyword));
  const moduleBindings = new Map();
  const importBindings = new Map();
  const addModuleBinding = (name, binding) => {
    const values = moduleBindings.get(name) ?? [];
    values.push(binding);
    moduleBindings.set(name, values);
  };
  for (const statement of topLevel) {
    if (ts.isImportDeclaration(statement) && ts.isStringLiteral(statement.moduleSpecifier) && statement.importClause) {
      const clause = statement.importClause;
      const addImportBinding = (name, binding) => {
        addModuleBinding(name, binding);
        importBindings.set(name, [...(importBindings.get(name) ?? []), binding]);
      };
      if (clause.name) addImportBinding(clause.name.text, {
        kind: "unsupported_import", specifier: statement.moduleSpecifier.text, imported_name: "default",
      });
      const named = clause.namedBindings;
      if (named && ts.isNamespaceImport(named)) addImportBinding(named.name.text, {
        kind: "unsupported_import", specifier: statement.moduleSpecifier.text, imported_name: "*",
      });
      else if (named && ts.isNamedImports(named)) for (const element of named.elements) {
        addImportBinding(element.name.text, {
          kind: element.isTypeOnly || clause.isTypeOnly ? "type_import" : "named_import",
          specifier: statement.moduleSpecifier.text,
          imported_name: (element.propertyName ?? element.name).text,
        });
      }
    } else if (ts.isFunctionDeclaration(statement) && statement.name) {
      addModuleBinding(statement.name.text, { kind: "declaration" });
    } else if (ts.isClassDeclaration(statement) && statement.name) {
      addModuleBinding(statement.name.text, { kind: "declaration" });
    } else if (ts.isVariableStatement(statement)) {
      for (const declaration of statement.declarationList.declarations) {
        for (const name of bindingNames(declaration.name)) addModuleBinding(name, { kind: "declaration" });
      }
    }
  }
  const topLevelFunctions = functionInfos.filter((info) => info.direct_binding &&
    info.callable_binding && info.immutable_binding && info.parent_names.length === 1 && info.named_binding);
  const addExport = (exportedName, localName) => {
    const matches = topLevelFunctions.filter((info) => info.qualified_name === `${moduleName}.${localName}`);
    if (matches.length === 1) exports.push({
      exported_name: exportedName,
      target_qualified_name: matches[0].qualified_name,
      target_start_line: matches[0].start_line,
    });
  };
  for (const statement of topLevel) {
    if (isExported(statement)) {
      if (ts.isFunctionDeclaration(statement) && statement.name) addExport(statement.name.text, statement.name.text);
      else if (ts.isVariableStatement(statement)) for (const declaration of statement.declarationList.declarations) {
        if (ts.isIdentifier(declaration.name) && isCallableBinding(declaration)) addExport(declaration.name.text, declaration.name.text);
      }
    } else if (ts.isExportDeclaration(statement) && !statement.moduleSpecifier &&
      statement.exportClause && ts.isNamedExports(statement.exportClause)) {
      for (const element of statement.exportClause.elements) {
        if (!element.isTypeOnly) addExport(element.name.text, (element.propertyName ?? element.name).text);
      }
    }
  }
  const namedBindingInStatements = (statements, name, blockScopedOnly = true) => {
    const matches = [];
    for (const statement of statements) {
      if ((ts.isFunctionDeclaration(statement) || ts.isClassDeclaration(statement)) &&
        statement.name?.text === name) matches.push(statement);
      if (!ts.isVariableStatement(statement)) continue;
      const blockScoped = Boolean(statement.declarationList.flags & ts.NodeFlags.BlockScoped);
      if (blockScopedOnly !== blockScoped) continue;
      matches.push(...statement.declarationList.declarations.filter((item) => bindingNames(item.name).includes(name)));
    }
    return matches.length > 1 ? { ambiguous: true } : matches[0] ?? null;
  };
  const scopedBinding = (declaration, owner) => declaration
    ? declaration.ambiguous ? { ambiguous: true, owner } : { declaration, owner }
    : null;
  const bindingAt = (info, position, name) => {
    const fn = info.function_node;
    let current = position;
    while (current && current !== fn) {
      if (ts.isBlock(current)) {
        const declaration = namedBindingInStatements(current.statements, name);
        if (declaration) return scopedBinding(declaration, info);
      } else if (ts.isCatchClause(current) && current.variableDeclaration &&
        bindingNames(current.variableDeclaration.name).includes(name)) {
        return { declaration: current.variableDeclaration, owner: info };
      } else if (ts.isForStatement(current) || ts.isForInStatement(current) || ts.isForOfStatement(current)) {
        const initializer = current.initializer;
        if (initializer && ts.isVariableDeclarationList(initializer) &&
          initializer.declarations.some((item) => bindingNames(item.name).includes(name))) {
          return { declaration: initializer.declarations.find((item) => bindingNames(item.name).includes(name)), owner: info };
        }
      } else if (ts.isCaseBlock(current)) {
        const declaration = namedBindingInStatements(current.clauses.flatMap((clause) => clause.statements), name);
        if (declaration) return scopedBinding(declaration, info);
      }
      current = current.parent;
    }
    if (fn.parameters.some((parameter) => bindingNames(parameter.name).includes(name))) {
      return { declaration: null, owner: info };
    }
    let varDeclaration = null;
    const findVar = (node, isRoot = false) => {
      if (!isRoot && isCallableNode(node)) return;
      if (ts.isVariableDeclaration(node) && !(node.parent.flags & ts.NodeFlags.BlockScoped) &&
        bindingNames(node.name).includes(name)) varDeclaration ??= node;
      ts.forEachChild(node, (child) => findVar(child));
    };
    if (fn.body) findVar(fn.body, true);
    if (varDeclaration) return { declaration: varDeclaration, owner: info };
    if ((ts.isFunctionDeclaration(fn) || ts.isFunctionExpression(fn)) && fn.name?.text === name) {
      return { functionInfo: info, owner: info };
    }
    return null;
  };
  const callerScopePaths = (info) => {
    const paths = new Set([moduleName]);
    let current = moduleName;
    for (const name of info.parent_names.slice(1)) {
      current = `${current}.${name}`;
      paths.add(current);
    }
    paths.add(info.qualified_name);
    return paths;
  };
  const localTarget = (name, allowedScopes) => {
    const visible = functionInfos.filter((info) => info.direct_binding && info.immutable_binding &&
      info.callable_binding && info.named_binding && declarationName(info.declaration) === name &&
      allowedScopes.has(info.parent_names.join(".")));
    if (!visible.length) return { target: null, reason: "call_target_not_in_snapshot" };
    const nearest = Math.max(...visible.map((info) => info.parent_names.length));
    const matches = visible.filter((info) => info.parent_names.length === nearest);
    return matches.length === 1
      ? { target: matches[0], reason: null }
      : { target: null, reason: "ambiguous_call_target" };
  };
  const lexicalBinding = (info, callNode, name) => {
    const own = bindingAt(info, callNode, name);
    if (own) return own;
    const parentPaths = [];
    let parentPath = moduleName;
    for (const namePart of info.parent_names.slice(1)) {
      parentPath = `${parentPath}.${namePart}`;
      parentPaths.push(parentPath);
    }
    for (const parentName of parentPaths.reverse()) {
      const parent = functionInfos.find((candidate) => candidate.qualified_name === parentName);
      if (parent) {
        const binding = bindingAt(parent, info.declaration, name);
        if (binding) return binding;
      }
    }
    return null;
  };
  const functionInfoForBinding = (binding) => binding.functionInfo ??
    functionInfos.find((candidate) => candidate.declaration === binding.declaration);
  for (const info of functionInfos) {
    if (callBudgetTruncated) break;
    const body = info.function_node.body;
    if (!body) continue;
    const inspect = (node, isRoot = false) => {
      if (callBudgetTruncated || !isRoot && isCallableNode(node)) return;
      if (ts.isCallExpression(node)) {
        const callRange = range(node);
        const expression = node.expression.getText(source).slice(0, 500);
        let targetQualifiedName = null;
        let targetStartLine = null;
        let importSpecifier = null;
        let importedName = null;
        let reason = null;
        if (ts.isIdentifier(node.expression)) {
          const name = node.expression.text;
          const binding = lexicalBinding(info, node, name);
          if (binding) {
            const boundFunction = binding.ambiguous ? null : functionInfoForBinding(binding);
            const target = boundFunction && boundFunction.direct_binding && boundFunction.callable_binding &&
              boundFunction.immutable_binding && boundFunction.named_binding &&
              !hasWrites(binding.owner.function_node.body, new Set([name]))
              ? boundFunction
              : null;
            if (target) {
              targetQualifiedName = target.qualified_name;
              targetStartLine = target.start_line;
            } else if (binding.ambiguous) reason = "ambiguous_call_target";
            else if (binding.owner && binding.owner.function_node.body &&
              hasWrites(binding.owner.function_node.body, new Set([name]))) reason = "name_rebound_in_scope";
            else reason = "name_shadowed_in_scope";
          } else {
            const bindings = moduleBindings.get(name) ?? [];
            const imported = importBindings.get(name) ?? [];
            if (bindings.length > 1 || imported.length > 1) reason = "ambiguous_call_target";
            else if (imported.length === 1) {
              if (hasWrites(source, new Set([name]))) reason = "name_rebound_in_scope";
              else if (imported[0].kind !== "named_import") reason = "unsupported_import_binding";
              else {
                importSpecifier = imported[0].specifier;
                importedName = imported[0].imported_name;
              }
            } else if (bindings.length === 1) {
              if (hasWrites(source, new Set([name]))) reason = "name_rebound_in_scope";
              else {
                const target = localTarget(name, callerScopePaths(info));
                if (target.target) {
                  targetQualifiedName = target.target.qualified_name;
                  targetStartLine = target.target.start_line;
                } else reason = target.reason;
              }
            } else {
              const target = localTarget(name, callerScopePaths(info));
              if (target.target) {
                targetQualifiedName = target.target.qualified_name;
                targetStartLine = target.target.start_line;
              } else reason = target.reason;
            }
          }
        } else if (ts.isPropertyAccessExpression(node.expression) || ts.isElementAccessExpression(node.expression)) {
          reason = "object_method_not_resolved";
        } else {
          reason = "dynamic_call_target";
        }
        if (++callCount > maxCalls) {
          callBudgetTruncated = true;
          return;
        }
        calls.push({
          caller_qualified_name: info.qualified_name,
          caller_start_line: info.start_line,
          target_qualified_name: targetQualifiedName,
          target_start_line: targetStartLine,
          import_specifier: importSpecifier,
          imported_name: importedName,
          expression,
          reason,
          ...callRange,
        });
      }
      ts.forEachChild(node, (child) => inspect(child));
    };
    inspect(body, true);
  }
  const hasLocalBinding = (root, name) => {
    let found = false;
    const inspect = (node, isRoot = false) => {
      if (!isRoot && (ts.isFunctionDeclaration(node) || ts.isFunctionExpression(node) || ts.isArrowFunction(node) || ts.isMethodDeclaration(node))) {
        if (node.name && ts.isIdentifier(node.name) && node.name.text === name) found = true;
        return;
      }
      if (ts.isParameter(node) && bindingNames(node.name).includes(name)) found = true;
      if (ts.isVariableDeclaration(node) && bindingNames(node.name).includes(name)) found = true;
      if (ts.isClassDeclaration(node) && node.name && node.name.text === name) found = true;
      ts.forEachChild(node, (child) => inspect(child));
    };
    inspect(root, true);
    return found;
  };
  const hasBodyBinding = (root, name) => {
    if (!root.body) return true;
    let found = false;
    const inspect = (node, isRoot = false) => {
      if (!isRoot && (ts.isFunctionDeclaration(node) || ts.isFunctionExpression(node) || ts.isArrowFunction(node) || ts.isMethodDeclaration(node))) return;
      if (ts.isVariableDeclaration(node) && bindingNames(node.name).includes(name)) found = true;
      if ((ts.isFunctionDeclaration(node) || ts.isClassDeclaration(node)) && node.name && node.name.text === name) found = true;
      ts.forEachChild(node, (child) => inspect(child));
    };
    inspect(root.body, true);
    return found;
  };
  const queryParamsName = (fn, name) => {
    const declarations = [];
    let validUses = true;
    const inspect = (node, parent = null) => {
      if (node !== fn.body && (ts.isFunctionDeclaration(node) || ts.isFunctionExpression(node) || ts.isArrowFunction(node) || ts.isMethodDeclaration(node))) return;
      if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && node.name.text === name) declarations.push(node);
      if (ts.isIdentifier(node) && node.text === name) {
        const isBindingName = ts.isVariableDeclaration(node.parent) && node.parent.name === node;
        const isAllowedMethodCall =
          ts.isPropertyAccessExpression(node.parent) && node.parent.expression === node &&
          ts.isCallExpression(node.parent.parent) && node.parent.parent.expression === node.parent &&
          ["set", "toString"].includes(node.parent.name.text);
        if (!isBindingName && !isAllowedMethodCall) validUses = false;
      }
      if (ts.isPropertyAccessExpression(node) && ts.isIdentifier(node.expression) && node.expression.text === name && !["set", "toString"].includes(node.name.text)) validUses = false;
      ts.forEachChild(node, (child) => inspect(child, node));
    };
    inspect(fn.body);
    const declaration = declarations[0];
    const topLevelDeclarations = fn.body && ts.isBlock(fn.body)
      ? fn.body.statements.flatMap((statement) => ts.isVariableStatement(statement)
        ? statement.declarationList.declarations.filter((item) => ts.isIdentifier(item.name) && item.name.text === name)
        : [])
      : [];
    if (!validUses || declarations.length !== 1 || !declaration.initializer ||
      topLevelDeclarations.length !== 1 || topLevelDeclarations[0] !== declaration ||
      fn.parameters.some((parameter) => bindingNames(parameter.name).includes(name)) ||
      !(declaration.parent.flags & ts.NodeFlags.Const) || !ts.isNewExpression(declaration.initializer) ||
      !ts.isIdentifier(declaration.initializer.expression) || declaration.initializer.expression.text !== "URLSearchParams" ||
      topLevelBindingCount("URLSearchParams") !== 0 || hasLocalBinding(fn, "URLSearchParams") ||
      hasWrites(source, new Set(["URLSearchParams"])) ||
      hasWrites(fn.body, new Set([name]))) return false;
    let safeCalls = true;
    const checkCalls = (node) => {
      if (node !== fn.body && (ts.isFunctionDeclaration(node) || ts.isFunctionExpression(node) || ts.isArrowFunction(node) || ts.isMethodDeclaration(node))) return;
      if (ts.isCallExpression(node) && ts.isPropertyAccessExpression(node.expression) &&
        ts.isIdentifier(node.expression.expression) && node.expression.expression.text === name) {
        const method = node.expression.name.text;
        if (method === "set") {
          if (node.arguments.length !== 2 || !ts.isStringLiteral(node.arguments[0])) safeCalls = false;
        } else if (method === "toString") {
          if (node.arguments.length !== 0) safeCalls = false;
        } else safeCalls = false;
      }
      ts.forEachChild(node, checkCalls);
    };
    checkCalls(fn.body);
    return safeCalls;
  };
  const describePath = (url, fn) => {
    if (url && (ts.isStringLiteral(url) || ts.isNoSubstitutionTemplateLiteral(url))) {
      return { path: url.text, path_kind: "literal", path_template: null, valid: true };
    }
    if (!url || !ts.isTemplateExpression(url)) return { path: null, path_kind: "literal", path_template: null, valid: false };
    const spans = url.templateSpans;
    let parts = [url.head.text];
    let querySeen = false;
    let parameterCount = 0;
    for (let i = 0; i < spans.length; i++) {
      const span = spans[i];
      const expression = span.expression;
      if (!querySeen && ts.isCallExpression(expression) && ts.isPropertyAccessExpression(expression.expression) &&
        ts.isIdentifier(expression.expression.expression) && expression.expression.name.text === "toString" &&
        expression.arguments.length === 0 && span.literal.text === "" &&
        parts[parts.length - 1].endsWith("?") && i === spans.length - 1 &&
        queryParamsName(fn, expression.expression.expression.text)) {
        parts[parts.length - 1] = parts[parts.length - 1].slice(0, -1);
        querySeen = true;
        continue;
      }
      if (querySeen || !ts.isCallExpression(expression) || !ts.isIdentifier(expression.expression) ||
        expression.expression.text !== "encodeURIComponent" || expression.arguments.length !== 1 ||
        !ts.isIdentifier(expression.arguments[0])) {
        return { path: null, path_kind: "literal", path_template: null, valid: false };
      }
      const identifier = expression.arguments[0].text;
      if (topLevelBindingCount("encodeURIComponent") !== 0 || hasLocalBinding(fn, "encodeURIComponent") ||
        hasWrites(source, new Set(["encodeURIComponent"])) ||
        !fn.parameters.some((parameter) => bindingNames(parameter.name).includes(identifier)) ||
        hasBodyBinding(fn, identifier) || hasWrites(fn.body, new Set([identifier])) ||
        !parts[parts.length - 1].endsWith("/") ||
        !span.literal.text.startsWith("/") && !(
          span.literal.text.endsWith("?") && i === spans.length - 2 &&
          ts.isCallExpression(spans[i + 1].expression) &&
          ts.isPropertyAccessExpression(spans[i + 1].expression.expression) &&
          ts.isIdentifier(spans[i + 1].expression.expression.expression) &&
          spans[i + 1].expression.expression.name.text === "toString" &&
          spans[i + 1].expression.arguments.length === 0 &&
          spans[i + 1].literal.text === "" &&
          queryParamsName(fn, spans[i + 1].expression.expression.expression.text)
        ) && !(i === spans.length - 1 && span.literal.text === "")) {
        return { path: null, path_kind: "literal", path_template: null, valid: false };
      }
      parts.push("{*}");
      parameterCount++;
      parts.push(span.literal.text);
    }
    const path = parts.join("");
    if (!path.startsWith("/api/") || /[?#]/.test(path)) return { path: null, path_kind: "literal", path_template: null, valid: false };
    if (parameterCount > 0) {
      if (parts.some((part) => part !== "{*}" && /[{}]/.test(part))) {
        return { path: null, path_kind: "literal", path_template: null, valid: false };
      }
      return { path: null, path_kind: "parameterized", path_template: path, valid: true };
    }
    return { path, path_kind: "literal", path_template: null, valid: true };
  };
  const topLevelBindingCount = (name) => {
    let count = 0;
    for (const statement of topLevel) {
      if (ts.isImportDeclaration(statement) && statement.importClause) {
        if (statement.importClause.name && statement.importClause.name.text === name) count++;
        const bindings = statement.importClause.namedBindings;
        if (bindings && ts.isNamespaceImport(bindings) && bindings.name.text === name) count++;
        if (bindings && ts.isNamedImports(bindings)) for (const item of bindings.elements) if (item.name.text === name) count++;
      } else if (ts.isVariableStatement(statement)) {
        for (const declaration of statement.declarationList.declarations) {
          if (bindingNames(declaration.name).includes(name)) count++;
        }
      } else if (
        (ts.isFunctionDeclaration(statement) || ts.isClassDeclaration(statement) || ts.isInterfaceDeclaration(statement) || ts.isTypeAliasDeclaration(statement)) &&
        statement.name && statement.name.text === name
      ) count++;
    }
    return count;
  };
  let wrapperName = null;
  if (
    requestWrappers.length === 1 && baseBindings.length === 1 &&
    topLevelBindingCount("requestJson") === 1 && topLevelBindingCount("fetch") === 0 &&
    topLevelBindingCount("apiBaseUrl") === 1
  ) {
    const wrapper = requestWrappers[0];
    const calls = [];
    const inspectFetch = (node, isRoot = false) => {
      if (!node) return;
      if (!isRoot && isCallableNode(node)) return;
      if (ts.isCallExpression(node) && ts.isIdentifier(node.expression) && node.expression.text === "fetch") calls.push(node);
      ts.forEachChild(node, (child) => inspectFetch(child));
    };
    inspectFetch(wrapper.body, true);
    const fetchCall = calls[0];
    const template = fetchCall && fetchCall.arguments[0];
    const options = fetchCall && fetchCall.arguments[1];
    const forwardedInit = options && ts.isObjectLiteralExpression(options) &&
      options.properties.length >= 1 && ts.isSpreadAssignment(options.properties[0]) &&
      ts.isIdentifier(options.properties[0].expression) && options.properties[0].expression.text === "init" &&
      options.properties.filter(ts.isSpreadAssignment).length === 1 &&
      options.properties.slice(1).every((property) =>
        ts.isShorthandPropertyAssignment(property)
          ? property.name.text !== "method"
          : ts.isPropertyAssignment(property) &&
            property.name && !ts.isComputedPropertyName(property.name) &&
            (ts.isIdentifier(property.name) || ts.isStringLiteral(property.name)) &&
            property.name.text !== "method"
      );
    const initRestName = options && ts.isObjectLiteralExpression(options) &&
      options.properties.length >= 1 && ts.isSpreadAssignment(options.properties[0]) &&
      ts.isIdentifier(options.properties[0].expression) && options.properties[0].expression.text;
    const initRestBinding = (name) => {
      if (!name || !wrapper.body || !ts.isBlock(wrapper.body)) return false;
      const matches = [];
      for (const statement of wrapper.body.statements) {
        if (!ts.isVariableStatement(statement)) continue;
        for (const declaration of statement.declarationList.declarations) {
          if (bindingNames(declaration.name).includes(name)) matches.push({ statement, declaration });
        }
      }
      if (matches.length !== 1) return false;
      const { statement, declaration } = matches[0];
      const pattern = declaration.name;
      if (!(statement.declarationList.flags & ts.NodeFlags.Const) || !ts.isObjectBindingPattern(pattern) ||
        !declaration.initializer || !ts.isIdentifier(declaration.initializer) || declaration.initializer.text !== "init" ||
        pattern.elements.length !== 2) return false;
      const [timeoutBinding, requestInitRest] = pattern.elements;
      if (!ts.isBindingElement(timeoutBinding) || timeoutBinding.dotDotDotToken || timeoutBinding.propertyName ||
        !ts.isIdentifier(timeoutBinding.name) || timeoutBinding.name.text !== "timeoutMs" || timeoutBinding.initializer ||
        !ts.isBindingElement(requestInitRest) || !requestInitRest.dotDotDotToken || requestInitRest.propertyName ||
        !ts.isIdentifier(requestInitRest.name) || requestInitRest.name.text !== name || requestInitRest.initializer) return false;
      let otherBindings = 0;
      const countOtherBindings = (node) => {
        if (node !== wrapper.body && isCallableNode(node)) return;
        if (ts.isVariableDeclaration(node) && bindingNames(node.name).includes(name) && node !== declaration) otherBindings++;
        if (ts.isParameter(node) && bindingNames(node.name).includes(name)) otherBindings++;
        if (ts.isCatchClause(node) && node.variableDeclaration && bindingNames(node.variableDeclaration.name).includes(name)) otherBindings++;
        ts.forEachChild(node, countOtherBindings);
      };
      countOtherBindings(wrapper.body);
      return otherBindings === 0 && !hasWrites(wrapper.body, new Set([name]));
    };
    const forwardedRestInit = initRestName && initRestBinding(initRestName) &&
      options.properties.filter(ts.isSpreadAssignment).length === 1 &&
      options.properties.slice(1).every((property) =>
        ts.isShorthandPropertyAssignment(property)
          ? property.name.text !== "method"
          : ts.isPropertyAssignment(property) &&
            property.name && !ts.isComputedPropertyName(property.name) &&
            (ts.isIdentifier(property.name) || ts.isStringLiteral(property.name)) &&
            property.name.text !== "method"
      );
    const templateForwardsPath = template && ts.isTemplateExpression(template) &&
      template.head.text === "" && template.templateSpans.length === 2 &&
      ts.isIdentifier(template.templateSpans[0].expression) && template.templateSpans[0].expression.text === "apiBaseUrl" &&
      template.templateSpans[0].literal.text === "" &&
      ts.isIdentifier(template.templateSpans[1].expression) && template.templateSpans[1].expression.text === "path" &&
      template.templateSpans[1].literal.text === "";
    const params = wrapper.parameters;
    const wrapperHasBody = Boolean(wrapper.body && ts.isBlock(wrapper.body));
    const defaultsToEmptyInit = params.length === 3 && ts.isIdentifier(params[0].name) && params[0].name.text === "path" &&
      ts.isIdentifier(params[2].name) && params[2].name.text === "init" &&
      params[2].initializer && ts.isObjectLiteralExpression(params[2].initializer) && params[2].initializer.properties.length === 0;
    const stableBindings = params.length === 3 && ts.isIdentifier(params[0].name) && params[0].name.text === "path" &&
      ts.isIdentifier(params[2].name) && params[2].name.text === "init" &&
      !hasBodyBinding(wrapper, "path") && !hasBodyBinding(wrapper, "init") &&
      !hasWrites(wrapper.body, new Set(["path", "init"]));
    const immutableApiBase = ts.isVariableDeclaration(baseBindings[0]) &&
      baseBindings[0].parent.flags & ts.NodeFlags.Const && Boolean(baseBindings[0].initializer);
    if (
      wrapperHasBody && calls.length === 1 && fetchCall.arguments.length === 2 && (forwardedInit || forwardedRestInit) && templateForwardsPath &&
      defaultsToEmptyInit && stableBindings && immutableApiBase &&
      !hasLocalBinding(wrapper, "fetch") && !hasLocalBinding(wrapper, "apiBaseUrl") &&
      !hasWrites(source, new Set(["fetch"])) &&
      !hasWrites(source, new Set(["requestJson", "apiBaseUrl"]))
    ) wrapperName = "requestJson";
  }

  const collectRequests = (fn, clientName) => {
    if (!wrapperName || !clientName || hasLocalBinding(fn, wrapperName)) return;
    const inspect = (node, isRoot = false) => {
      if (!isRoot && (ts.isFunctionDeclaration(node) || ts.isFunctionExpression(node) || ts.isArrowFunction(node) || ts.isMethodDeclaration(node))) return;
      if (ts.isCallExpression(node) && ts.isIdentifier(node.expression) && node.expression.text === wrapperName) {
        const url = node.arguments[0];
        const describedPath = describePath(url, fn);
        const requestPath = describedPath.path;
        const init = node.arguments[2];
        let method = "GET";
        let methodKnown = node.arguments.length === 2 || node.arguments.length === 3;
        if (init && ts.isObjectLiteralExpression(init) && !init.properties.some(ts.isSpreadAssignment)) {
          methodKnown = true;
          if (init.properties.some((property) => property.name && ts.isComputedPropertyName(property.name))) methodKnown = false;
          const methodProperties = init.properties.filter((property) => {
            const name = property.name && (ts.isIdentifier(property.name) || ts.isStringLiteral(property.name)) ? property.name.text : null;
            return name === "method";
          });
          if (methodProperties.length > 1) methodKnown = false;
          else if (methodProperties.length === 1) {
            const methodProperty = methodProperties[0];
            if (!ts.isPropertyAssignment(methodProperty) || !ts.isStringLiteral(methodProperty.initializer)) methodKnown = false;
            else method = methodProperty.initializer.text.toUpperCase();
          }
        } else if (init) methodKnown = false;
        let unknown_reason = null;
        if (!describedPath.valid) unknown_reason = "dynamic_path";
        else if (!methodKnown) unknown_reason = "dynamic_method";
        else if (describedPath.path_kind === "literal" && (!requestPath.startsWith("/api/") || /[?#]/.test(requestPath))) unknown_reason = "unsupported_path_shape";
        else if (!["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD", "TRACE"].includes(method)) unknown_reason = "unsupported_method";
        if (++requestCount > maxRequests) throw new Error("TypeScript HTTP request budget exceeded");
        http_requests.push({
          method: methodKnown ? method : null,
          path: requestPath,
          path_kind: describedPath.path_kind,
          path_template: describedPath.path_template,
          unknown_reason,
          client_name: clientName,
          ...range(node),
        });
      }
      ts.forEachChild(node, (child) => inspect(child));
    };
    inspect(fn.body, true);
  };

  function hasWrites(root, names) {
    let found = false;
    const assignedName = (node) => {
      if (ts.isIdentifier(node)) return names.has(node.text);
      if (ts.isPropertyAccessExpression(node)) return ts.isIdentifier(node.expression) && names.has(node.expression.text);
      if (ts.isElementAccessExpression(node)) return ts.isIdentifier(node.expression) && names.has(node.expression.text);
      return false;
    };
    const inspect = (node) => {
      if (ts.isBinaryExpression(node) && node.operatorToken.kind >= ts.SyntaxKind.FirstAssignment && node.operatorToken.kind <= ts.SyntaxKind.LastAssignment && assignedName(node.left)) found = true;
      if ((ts.isPrefixUnaryExpression(node) || ts.isPostfixUnaryExpression(node)) &&
        [ts.SyntaxKind.PlusPlusToken, ts.SyntaxKind.MinusMinusToken].includes(node.operator) && assignedName(node.operand)) found = true;
      ts.forEachChild(node, inspect);
    };
    inspect(root);
    return found;
  }

  for (const statement of topLevel) {
    if (ts.isImportDeclaration(statement) && ts.isStringLiteral(statement.moduleSpecifier)) {
      addImport(statement, statement.moduleSpecifier.text, "static");
    } else if (ts.isExportDeclaration(statement) && statement.moduleSpecifier && ts.isStringLiteral(statement.moduleSpecifier)) {
      addImport(statement, statement.moduleSpecifier.text, "static");
    }
    if (ts.isFunctionDeclaration(statement) && statement.name) collectRequests(statement, statement.name.text);
    if (ts.isVariableStatement(statement) && statement.declarationList.flags & ts.NodeFlags.Const) {
      for (const declaration of statement.declarationList.declarations) {
        if (!ts.isIdentifier(declaration.name) || !declaration.initializer) continue;
        if (ts.isArrowFunction(declaration.initializer) || ts.isFunctionExpression(declaration.initializer)) {
          collectRequests(declaration.initializer, declaration.name.text);
        }
      }
    }
  }
  const visitModuleReferences = (node) => {
    if (ts.isImportTypeNode(node) && ts.isLiteralTypeNode(node.argument) && ts.isStringLiteral(node.argument.literal)) {
      addImport(node, node.argument.literal.text, "static");
    } else if (ts.isCallExpression(node) && node.expression.kind === ts.SyntaxKind.ImportKeyword) {
      const argument = node.arguments[0];
      addImport(node, argument && (ts.isStringLiteral(argument) || ts.isNoSubstitutionTemplateLiteral(argument)) ? argument.text : "<dynamic>", "dynamic");
    }
    ts.forEachChild(node, visitModuleReferences);
  };
  visitModuleReferences(source);
  const jsxAttributeValue = (attribute) => {
    if (!attribute.initializer || !ts.isJsxExpression(attribute.initializer)) return null;
    return attribute.initializer.expression ?? null;
  };
  const staticJsxText = (node) => {
    const parts = [];
    const candidates = new Set();
    let dynamic = false;
    const collectLiterals = (expression) => {
      if (ts.isStringLiteral(expression) || ts.isNoSubstitutionTemplateLiteral(expression)) {
        const value = expression.text.trim();
        if (value) candidates.add(value);
        return;
      }
      if (ts.isConditionalExpression(expression)) {
        collectLiterals(expression.whenTrue);
        collectLiterals(expression.whenFalse);
      }
    };
    const collect = (child) => {
      if (ts.isJsxText(child)) {
        const value = child.text.replace(/\s+/g, " ").trim();
        if (value) { parts.push(value); candidates.add(value); }
      } else if (ts.isJsxExpression(child)) {
        const expression = child.expression;
        if (expression && (ts.isStringLiteral(expression) || ts.isNoSubstitutionTemplateLiteral(expression))) {
          parts.push(expression.text.trim());
          if (expression.text.trim()) candidates.add(expression.text.trim());
        } else if (expression) {
          dynamic = true;
          collectLiterals(expression);
        }
      } else if (ts.isJsxElement(child)) {
        child.children.forEach(collect);
      }
    };
    if (ts.isJsxElement(node)) node.children.forEach(collect);
    return { text: parts.join(" ").replace(/\s+/g, " ").trim(), candidates: [...candidates].slice(0, 5), dynamic };
  };
  const formSubmitLabel = (element) => {
    const buttons = [];
    const visit = (node) => {
      if (ts.isJsxElement(node) || ts.isJsxSelfClosingElement(node)) {
        const opening = ts.isJsxElement(node) ? node.openingElement : node;
        if (opening.tagName.getText(source).toLowerCase() === "button") {
          const typeAttribute = opening.attributes.properties.find((attribute) =>
            ts.isJsxAttribute(attribute) && attribute.name.getText(source) === "type",
          );
          const typeValue = typeAttribute && typeAttribute.initializer && ts.isStringLiteral(typeAttribute.initializer)
            ? typeAttribute.initializer.text.toLowerCase()
            : null;
          if (typeValue !== "button" && typeValue !== "reset") {
            const text = ts.isJsxElement(node) ? staticJsxText(node) : { text: "", dynamic: false };
            buttons.push(text);
          }
        }
      }
      if (ts.isJsxElement(node)) node.children.forEach(visit);
      else if (ts.isJsxFragment(node)) node.children.forEach(visit);
    };
    if (ts.isJsxElement(element)) element.children.forEach(visit);
    if (buttons.length === 1 && buttons[0].text) return {
      label: buttons[0].text.slice(0, 200),
      label_candidates: buttons[0].candidates,
      dynamic: buttons[0].dynamic,
    };
    const candidates = [...new Set(buttons.flatMap((item) => item.candidates ?? []))].slice(0, 5);
    return { label: "表单提交", label_candidates: candidates, dynamic: buttons.length !== 1 || buttons.some((item) => item.dynamic) };
  };
  const jsxOwner = (node) => {
    const owners = functionInfos.filter((info) => {
      const start = info.function_node.getStart(source);
      return start <= node.getStart(source) && info.function_node.end >= node.end;
    }).sort((left, right) => (left.function_node.end - left.function_node.getStart(source)) -
      (right.function_node.end - right.function_node.getStart(source)));
    return owners[0] ?? null;
  };
  const visitUiActions = (node) => {
    if (ts.isJsxAttribute(node) && ["onSubmit", "onClick"].includes(node.name.getText(source))) {
      const opening = node.parent.parent;
      const element = ts.isJsxSelfClosingElement(opening) ? opening : opening.parent;
      const expression = jsxAttributeValue(node);
      const owner = jsxOwner(node);
      const handlerExpression = expression ? expression.getText(source).slice(0, 500) : "<missing handler>";
      let receiver_name = null;
      let callback_property = null;
      let handler_name = null;
      let handler_qualified_name = null;
      let handler_start_line = null;
      let handler_binding_static = false;
      if (expression && ts.isPropertyAccessExpression(expression) && ts.isIdentifier(expression.expression)) {
        receiver_name = expression.expression.text;
        callback_property = expression.name.text;
      } else if (expression && (ts.isArrowFunction(expression) || ts.isFunctionExpression(expression))) {
        const callbackReferences = new Set();
        const collectCallbackReferences = (candidate) => {
          if (ts.isPropertyAccessExpression(candidate) && ts.isIdentifier(candidate.expression) &&
            candidate.name.text === node.name.getText(source)) {
            callbackReferences.add(`${candidate.expression.text}\0${candidate.name.text}`);
          }
          ts.forEachChild(candidate, collectCallbackReferences);
        };
        collectCallbackReferences(expression.body);
        if (callbackReferences.size === 1) {
          [receiver_name, callback_property] = [...callbackReferences][0].split("\0");
        }
      } else if (expression && ts.isIdentifier(expression)) {
        handler_name = expression.text;
        if (owner) {
          const target = localTarget(handler_name, callerScopePaths(owner)).target;
          if (target && !hasWrites(owner.function_node.body, new Set([handler_name]))) {
            handler_qualified_name = target.qualified_name;
            handler_start_line = target.start_line;
            handler_binding_static = true;
          }
        }
      }
      let label = "";
      let labelCandidates = [];
      let label_dynamic = false;
      const tag = opening.tagName.getText(source).toLowerCase();
      if (node.name.getText(source) === "onSubmit" && tag === "form") {
        const described = formSubmitLabel(element);
        label = described.label;
        labelCandidates = described.label_candidates;
        label_dynamic = described.dynamic;
      } else {
        const described = ts.isJsxElement(element) ? staticJsxText(element) : { text: "", candidates: [], dynamic: false };
        label = described.text.slice(0, 200);
        labelCandidates = described.candidates;
        label_dynamic = described.dynamic;
        if (!label) label = node.name.getText(source) === "onSubmit" ? "表单提交" : "页面操作";
      }
      if (ui_actions.length < 1000) ui_actions.push({
        event_name: node.name.getText(source),
        element_name: tag.slice(0, 100),
        label,
        label_candidates: labelCandidates,
        label_dynamic,
        handler_expression: handlerExpression,
        receiver_name,
        callback_property,
        handler_name,
        handler_qualified_name,
        handler_start_line,
        handler_binding_static,
        owner_qualified_name: owner?.qualified_name ?? null,
        owner_start_line: owner?.start_line ?? null,
        element_start_line: range(element).start_line,
        element_end_line: range(element).end_line,
        ...range(node),
      });
      else { uiActionBudgetTruncated = true; ui_actions_omitted += 1; }
    }
    if (ts.isJsxAttribute(node) && ts.isIdentifier(node.name)) {
      const expression = jsxAttributeValue(node);
      if (expression && ts.isObjectLiteralExpression(expression)) {
        for (const property of expression.properties) {
          if (!ts.isPropertyAssignment(property) || !property.name || !ts.isIdentifier(property.name) && !ts.isStringLiteral(property.name)) continue;
          const value = property.initializer;
          if (!ts.isIdentifier(value)) continue;
          const owner = jsxOwner(node);
          if (ui_prop_bindings.length < 2000) ui_prop_bindings.push({
            prop_name: node.name.text,
            callback_property: property.name.text,
            target_name: value.text,
            owner_qualified_name: owner?.qualified_name ?? null,
            ...range(property),
          });
          else { uiPropBindingBudgetTruncated = true; ui_prop_bindings_omitted += 1; }
        }
      }
    }
    ts.forEachChild(node, visitUiActions);
  };
  if (filePath.toLowerCase().endsWith(".tsx")) visitUiActions(source);
  return {
    path: filePath,
    definitions,
    imports,
    http_requests,
    ui_actions,
    ui_prop_bindings,
    ui_actions_truncated: uiActionBudgetTruncated,
    ui_prop_bindings_truncated: uiPropBindingBudgetTruncated,
    ui_actions_omitted,
    ui_prop_bindings_omitted,
    calls,
    exports,
    calls_truncated: callBudgetTruncated,
    calls_truncation_reason: callBudgetTruncated ? "max_typescript_calls reached" : null,
  };
});

const output = JSON.stringify({
  compiler_version: compilerVersion,
  files,
});
if (Buffer.byteLength(output, "utf8") > 8 * 1024 * 1024) throw new Error("TypeScript parser output budget exceeded");
process.stdout.write(output);
