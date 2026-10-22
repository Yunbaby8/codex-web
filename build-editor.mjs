import { build } from 'esbuild';
import { copyFile } from 'node:fs/promises';
await build({entryPoints:['frontend/editor-entry.js','node_modules/monaco-editor/esm/vs/editor/editor.worker.js'],bundle:true,format:'esm',outdir:'frontend/vendor',entryNames:'[name]',loader:{'.ttf':'file'},minify:true});
await copyFile('node_modules/monaco-editor/LICENSE','frontend/vendor/MONACO-LICENSE');
await copyFile('node_modules/monaco-editor/ThirdPartyNotices.txt','frontend/vendor/MONACO-ThirdPartyNotices.txt');
