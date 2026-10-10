import * as monaco from 'monaco-editor/editor/editor.main.js';
import {registerHighlighting, highlightingRules} from './editor-highlighting.js';

registerHighlighting(monaco);

// Same-origin resources; V1 has highlighting but no language services or schemas.
self.MonacoEnvironment = {
  getWorker() { return new Worker('/static/vendor/editor.worker.js', {type: 'module'}); }
};
monaco.typescript?.typescriptDefaults.setModeConfiguration({});
monaco.typescript?.javascriptDefaults.setModeConfiguration({});
monaco.json?.jsonDefaults.setModeConfiguration({tokens:true, colors:false});
monaco.css?.cssDefaults.setModeConfiguration({});
monaco.html?.htmlDefaults.setModeConfiguration({});

monaco.languages.register({id:'bitbake', extensions:['.bb','.bbappend','.inc','.bbclass']});
monaco.languages.setMonarchTokensProvider('bitbake', {
  tokenizer: {root: [
    [/#.*$/, 'comment'], [/\$\{[^}]*\}/, 'variable'],
    [/"/, 'string', '@string'], [/'[^']*'/, 'string'],
    [/\b(?:inherit|include|require|export|python|def|addtask|deltask|before|after|do_[\w]+)\b/, 'keyword'],
    [/[\w:+.-]+(?=\s*(?:\?\?=|\?=|:=|\+=|=\+|\.=|=\.|=))/, 'variable'],
    [/\b\d+\b/, 'number'], [/[{}()]/, '@brackets']
  ], string: [[/\$\{[^}]*\}/,'variable'], [/\\./,'string.escape'], [/"/,'string','@pop'], [/[^"\\$]+/,'string'], [/./,'string']]}
});
monaco.languages.setLanguageConfiguration('bitbake', {comments:{lineComment:'#'}, brackets:[['{','}'],['(',')']], autoClosingPairs:[{open:'{',close:'}'},{open:'"',close:'"'}], indentationRules:{increaseIndentPattern:/\{\s*$/,decreaseIndentPattern:/^\s*\}/}});
monaco.languages.register({id:'systemd', extensions:['.service','.socket','.target','.timer','.mount','.automount','.path','.slice','.scope']});
monaco.languages.setMonarchTokensProvider('systemd', {tokenizer:{root:[[/^\s*[#;].*$/,'comment'],[/^\s*\[[^\]]+\]/,'type'],[/^[\w]+(?==)/,'key'],[/%[\w%]|\$\{?\w+\}?/,'variable'],[/"[^"]*"/,'string'],[/\b(?:yes|no|true|false|on|off)\b/,'keyword']]}});
monaco.languages.setLanguageConfiguration('systemd',{comments:{lineComment:'#'},folding:{markers:{start:/^\s*\[/,end:/^\s*$/}}});
monaco.languages.register({id:'makefile', filenames:['Makefile','makefile','GNUmakefile'], extensions:['.mk']});
monaco.languages.setMonarchTokensProvider('makefile',{tokenizer:{root:[[/#.*/,'comment'],[/\$\([^)]+\)|\$\{[^}]+\}|\$[@<^?*%+|]/,'variable'],[/^[^\s#:=]+(?=\s*:)/,'type'],[/\b(?:include|ifeq|ifneq|ifdef|ifndef|else|endif|define|endef|export|override)\b/,'keyword'],[/"[^"]*"|'[^']*'/,'string'],[/[:?+]?=/,'operator']]}});
monaco.languages.setLanguageConfiguration('makefile',{comments:{lineComment:'#'}});
export {monaco, highlightingRules};
