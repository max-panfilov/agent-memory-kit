import fs from 'node:fs';
import path from 'node:path';
import mermaid from 'mermaid';

const root = process.argv[2] || '/data';
const files = [];
function walk(directory) {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    if (['.git', '.venv', 'node_modules', 'backups'].includes(entry.name)) continue;
    const target = path.join(directory, entry.name);
    if (entry.isDirectory()) walk(target);
    else if (entry.isFile() && entry.name.endsWith('.md')) files.push(target);
  }
}
walk(root);
let count = 0;
for (const file of files) {
  const text = fs.readFileSync(file, 'utf8');
  for (const match of text.matchAll(/```mermaid\s*\n([\s\S]*?)```/g)) {
    count += 1;
    try {
      await mermaid.parse(match[1]);
    } catch (error) {
      console.error(`${file}: Mermaid block ${count} failed: ${error.message}`);
      process.exitCode = 1;
    }
  }
}
if (count === 0) {
  console.error('No Mermaid blocks found');
  process.exitCode = 1;
} else if (!process.exitCode) {
  console.log(`mermaid: ${count} blocks parsed successfully`);
}
