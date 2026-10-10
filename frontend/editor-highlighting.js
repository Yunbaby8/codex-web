import {language as cpp} from 'monaco-editor/languages/definitions/cpp/cpp.js';

// Extend Monaco's lexer, retaining its comments, raw strings and directives.
// These are lexical hints, not type resolution: no project scan or language server.
export function registerHighlighting(monaco) {
  const identifier = fallback => ({cases:{'@keywords':{token:'keyword.$0'}, '@default':fallback}});
  const root = [...cpp.tokenizer.root];
  root.splice(1, 1,
    [/\b(?:class|struct|union|enum)\b/, {token:'keyword', next:'@declaredType'}],
    [/(->|\.)(\s*)([a-zA-Z_]\w*)(?=\s*\()/, ['delimiter', '', 'codex.function']],
    [/(->|\.)(\s*)([a-zA-Z_]\w*)/, ['delimiter', '', 'codex.member']],
    [/[A-Z][A-Z0-9_]+\b/, identifier('codex.macro')],
    [/[a-zA-Z_]\w*(?=\s*\()/, identifier('codex.function')],
    [/[a-zA-Z_]\w*(?=\s*::)/, identifier('codex.type')],
    [/(?:[A-Z][a-zA-Z0-9_]*|[a-zA-Z_]\w*_t)\b/, identifier('codex.type')],
    [/[a-zA-Z_]\w*/, identifier('codex.variable')]
  );
  const language = {...cpp, tokenizer:{...cpp.tokenizer, root, declaredType:[
    {include:'@whitespace'},
    [/\b(?:class|struct)\b/, 'keyword'], // enum class / enum struct
    [/[a-zA-Z_]\w*/, {token:'codex.type', next:'@pop'}],
    [/./, {token:'@rematch', next:'@pop'}]
  ]}};
  for (const id of ['c','cpp']) monaco.languages.setMonarchTokensProvider(id, language);
}

export function highlightingRules(light = false) {
  const colors = light
    ? {type:'267F79', function:'795E26', member:'005A9E', variable:'173D75', macro:'8B397A'}
    : {type:'4EC9B0', function:'DCDCAA', member:'9CDCFE', variable:'C4DDF2', macro:'C586C0'};
  return Object.entries(colors).map(([name,foreground]) => ({token:'codex.'+name,foreground}));
}
