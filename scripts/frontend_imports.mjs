// 使用 TypeScript AST 和项目解析规则识别导入、再导出及静态动态导入。
import fs from 'node:fs';
import path from 'node:path';
import ts from '../frontend/node_modules/typescript/lib/typescript.js';

const root = process.argv[2];
const paths = JSON.parse(fs.readFileSync(0, 'utf8'));
const configPath = ts.findConfigFile(path.join(root, 'frontend'), ts.sys.fileExists, 'tsconfig.json');
const options = configPath
  ? ts.parseJsonConfigFileContent(ts.readConfigFile(configPath, ts.sys.readFile).config, ts.sys, path.dirname(configPath)).options
  : {moduleResolution: ts.ModuleResolutionKind.Bundler};
const result = {};
for (const relative of paths) {
  const file = path.join(root, relative);
  const source = ts.createSourceFile(file, fs.readFileSync(file, 'utf8'), ts.ScriptTarget.Latest, true);
  const imports = [], exports = [], namespaces = new Map();
  let wildcard = false;
  const add = (specifier, names) => {
    const resolved = ts.resolveModuleName(specifier, file, options, ts.sys).resolvedModule?.resolvedFileName;
    const style = path.resolve(path.dirname(file), specifier);
    const target = resolved ?? (specifier.startsWith('.') && fs.existsSync(style) ? style : undefined);
    if (target && !target.includes('node_modules')) imports.push({target: path.relative(root, target), names});
  };
  function visit(node) {
    if (ts.isImportDeclaration(node) && ts.isStringLiteral(node.moduleSpecifier)) {
      const clause = node.importClause;
      const names = clause?.name ? ['default'] : [];
      if (clause?.namedBindings && ts.isNamedImports(clause.namedBindings)) {
        names.push(...clause.namedBindings.elements.map(item => (item.propertyName ?? item.name).text));
      }
      if (clause?.namedBindings && ts.isNamespaceImport(clause.namedBindings)) {
        namespaces.set(clause.namedBindings.name.text, node.moduleSpecifier.text);
      }
      add(node.moduleSpecifier.text, names);
    }
    if (ts.isExportDeclaration(node)) {
      const names = node.exportClause && ts.isNamedExports(node.exportClause)
        ? node.exportClause.elements.map(item => (item.propertyName ?? item.name).text) : [];
      if (node.moduleSpecifier && ts.isStringLiteral(node.moduleSpecifier)) add(node.moduleSpecifier.text, names);
      if (!node.exportClause) wildcard = true;
      if (node.exportClause && ts.isNamedExports(node.exportClause)) exports.push(...node.exportClause.elements.map(item => item.name.text));
    }
    if (node.modifiers?.some(item => item.kind === ts.SyntaxKind.ExportKeyword)) {
      if (node.modifiers.some(item => item.kind === ts.SyntaxKind.DefaultKeyword)) exports.push('default');
      if (node.name && ts.isIdentifier(node.name)) exports.push(node.name.text);
      if (ts.isVariableStatement(node)) exports.push(...node.declarationList.declarations.filter(item => ts.isIdentifier(item.name)).map(item => item.name.text));
    }
    if (ts.isCallExpression(node) && (node.expression.kind === ts.SyntaxKind.ImportKeyword || node.expression.getText(source) === 'require')) {
      if (node.arguments.length && ts.isStringLiteral(node.arguments[0])) add(node.arguments[0].text, []);
      else imports.push({target: '<dynamic>', names: []});
    }
    if (ts.isPropertyAccessExpression(node) && ts.isIdentifier(node.expression) && namespaces.has(node.expression.text)) {
      add(namespaces.get(node.expression.text), [node.name.text]);
    }
    if (ts.isElementAccessExpression(node) && ts.isIdentifier(node.expression) && namespaces.has(node.expression.text)) {
      add(namespaces.get(node.expression.text), ts.isStringLiteral(node.argumentExpression) ? [node.argumentExpression.text] : ['*']);
    }
    ts.forEachChild(node, visit);
  }
  visit(source);
  result[relative] = {imports, exports, wildcard};
}
process.stdout.write(JSON.stringify(result));
