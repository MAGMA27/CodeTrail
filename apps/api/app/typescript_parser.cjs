const fs = require("node:fs");
const path = require("node:path");

const compilerPath = path.resolve(__dirname, "../../web/node_modules/typescript/lib/typescript.js");
const ts = require(compilerPath);
const compilerVersion = "5.9.3";
const maxDefinitions = 20000;
const maxImports = 20000;
const maxRequests = 10000;

if (ts.version !== compilerVersion) throw new Error("locked TypeScript compiler version mismatch");

const input = JSON.parse(fs.readFileSync(0, "utf8"));
if (!input || !Array.isArray(input.files) || input.files.length > 2000) {
  throw new Error("invalid parser input");
}

let definitionCount = 0;
let importCount = 0;
let requestCount = 0;
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
  const visitDefinitions = (node, parentNames) => {
    let nextNames = parentNames;
    let kind = null;
    if (ts.isClassDeclaration(node)) kind = "class";
    else if (
      ts.isFunctionDeclaration(node) ||
      ts.isMethodDeclaration(node) ||
      ts.isConstructorDeclaration(node) ||
      ts.isGetAccessorDeclaration(node) ||
      ts.isSetAccessorDeclaration(node) ||
      ts.isVariableDeclaration(node) && node.initializer &&
        (ts.isArrowFunction(node.initializer) || ts.isFunctionExpression(node.initializer))
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
      }
    }
    ts.forEachChild(node, (child) => visitDefinitions(child, nextNames));
  };
  visitDefinitions(source, [filePath.replace(/\\/g, "/").replace(/\.(tsx?|mts|cts)$/i, "").replaceAll("/", ".")]);

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
  const hasNestedFunction = (root) => {
    let found = false;
    const inspect = (node) => {
      if (ts.isFunctionDeclaration(node) || ts.isFunctionExpression(node) || ts.isArrowFunction(node) || ts.isMethodDeclaration(node)) {
        found = true;
        return;
      }
      ts.forEachChild(node, inspect);
    };
    ts.forEachChild(root.body, inspect);
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
    const inspectFetch = (node) => {
      if (!node) return;
      if (ts.isCallExpression(node) && ts.isIdentifier(node.expression) && node.expression.text === "fetch") calls.push(node);
      ts.forEachChild(node, inspectFetch);
    };
    inspectFetch(wrapper.body);
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
      wrapperHasBody && calls.length === 1 && fetchCall.arguments.length === 2 && forwardedInit && templateForwardsPath &&
      defaultsToEmptyInit && stableBindings && immutableApiBase &&
      !hasLocalBinding(wrapper, "fetch") && !hasLocalBinding(wrapper, "apiBaseUrl") &&
      !hasNestedFunction(wrapper) &&
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
  return { path: filePath, definitions, imports, http_requests };
});

const output = JSON.stringify({ compiler_version: compilerVersion, files });
if (Buffer.byteLength(output, "utf8") > 8 * 1024 * 1024) throw new Error("TypeScript parser output budget exceeded");
process.stdout.write(output);
