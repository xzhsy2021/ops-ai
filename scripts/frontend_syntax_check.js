#!/usr/bin/env node
const fs = require('fs');
const path = require('path');
let ts;
try {
  ts = require('typescript');
} catch (err) {
  console.log('[frontend] typescript module not available, skip syntax check');
  process.exit(0);
}

const roots = [path.join(process.cwd(), 'frontend', 'src')];
const files = [];
function walk(dir) {
  if (!fs.existsSync(dir)) return;
  for (const entry of fs.readdirSync(dir, {withFileTypes: true})) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) walk(full);
    else if (/\.(ts|tsx)$/.test(entry.name) && !entry.name.endsWith('.d.ts')) files.push(full);
  }
}
roots.forEach(walk);
let failed = false;
for (const file of files) {
  const source = fs.readFileSync(file, 'utf8');
  const out = ts.transpileModule(source, {
    compilerOptions: {
      jsx: ts.JsxEmit.ReactJSX,
      module: ts.ModuleKind.ESNext,
      target: ts.ScriptTarget.ES2020,
      isolatedModules: true,
    },
    fileName: file,
    reportDiagnostics: true,
  });
  const errors = (out.diagnostics || []).filter((d) => d.category === ts.DiagnosticCategory.Error);
  if (errors.length) {
    failed = true;
    console.error(`[frontend] syntax errors in ${path.relative(process.cwd(), file)}`);
    for (const d of errors) {
      const msg = ts.flattenDiagnosticMessageText(d.messageText, '\n');
      const pos = d.file && typeof d.start === 'number' ? d.file.getLineAndCharacterOfPosition(d.start) : null;
      console.error(pos ? `  ${pos.line + 1}:${pos.character + 1} ${msg}` : `  ${msg}`);
    }
  }
}
if (failed) process.exit(1);
console.log(`[frontend] syntax check passed (${files.length} TS/TSX files)`);
