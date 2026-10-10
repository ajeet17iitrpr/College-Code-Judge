"use strict";

const app = document.getElementById("app");
const nav = document.getElementById("main-nav");
const toastRegion = document.getElementById("toast-region");
const modal = document.getElementById("modal");
const modalContent = document.getElementById("modal-content");
const themeToggle = document.getElementById("theme-toggle");
const menuButton = document.getElementById("menu-button");

const state = {
  user: null,
  polling: null,
  sessionCheck: null,
  examSessionId: null,
  heartbeat: null,
  monitorCleanup: [],
  adminTab: "dashboard",
  examModeActive: false,
  violationCount: 0,
  currentProblemId: null,
  fullscreenRequested: false,
  examIdeClipboard: "",
};
const UNSAFE = new Set(["POST", "PATCH", "PUT", "DELETE"]);
const FINAL = new Set(["COMPLETED", "FAILED"]);

function esc(value) {
  return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");
}
function fmt(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
}
function durationText(seconds) {
  const total = Math.max(0, Number(seconds || 0));
  const h = Math.floor(total / 3600), m = Math.floor((total % 3600) / 60), s = Math.floor(total % 60);
  if (h) return `${h} hr ${m} min ${s} sec`;
  if (m) return `${m} min ${s} sec`;
  return `${s} sec`;
}
function cookie(name) {
  const prefix = `${encodeURIComponent(name)}=`;
  for (const part of document.cookie.split(";")) {
    const item = part.trim();
    if (item.startsWith(prefix)) return decodeURIComponent(item.slice(prefix.length));
  }
  return null;
}
class ApiError extends Error { constructor(message, status) { super(message); this.status = status; } }
async function api(path, options = {}) {
  const method = String(options.method || "GET").toUpperCase();
  const headers = new Headers(options.headers || {});
  let body = options.body;
  if (body && !(body instanceof FormData) && typeof body !== "string") {
    headers.set("Content-Type", "application/json"); body = JSON.stringify(body);
  }
  if (UNSAFE.has(method)) {
    const token = cookie("judge_csrf"); if (token) headers.set("X-CSRF-Token", token);
  }
  const response = await fetch(path, { ...options, method, headers, body, credentials: "include", cache: "no-store" });
  const type = response.headers.get("content-type") || "";
  const payload = type.includes("application/json") ? await response.json() : { detail: await response.text() };
  if (!response.ok) throw new ApiError(payload.detail || `Request failed (${response.status})`, response.status);
  return payload;
}
function toast(message, error = false) {
  const el = document.createElement("div"); el.className = `toast${error ? " error" : ""}`; el.textContent = message; toastRegion.append(el); setTimeout(() => el.remove(), 4500);
}
function fail(error) { toast(error instanceof Error ? error.message : String(error), true); }
const VERDICT_INFO = {
  AC: ["Accepted", "The program produced the correct output for all required tests."],
  WA: ["Wrong Answer", "The program ran, but one or more outputs did not match the expected output."],
  CE: ["Compilation Error", "The source code could not be compiled or failed a syntax/preparation check."],
  RE: ["Runtime Error", "The program started but crashed or terminated abnormally while running."],
  TLE: ["Time Limit Exceeded", "The program exceeded the allowed execution time."],
  MLE: ["Memory Limit Exceeded", "The program exceeded the configured memory limit."],
  OLE: ["Output Limit Exceeded", "The program produced more output than the judge allows."],
  SE: ["System Error", "The judge infrastructure encountered an internal error. This is not a normal wrong-solution verdict."],
  QUEUED: ["Queued", "The submission has been received and is waiting for the judge worker."],
  COMPILING: ["Compiling", "The judge is compiling or preparing the submitted program."],
  RUNNING: ["Running", "The judge is currently executing the program against test cases."],
  OK: ["Run completed", "Manual run finished normally. No official test cases were used and no score was recorded."],
  COMPLETED: ["Completed", "Judging has finished."],
  FAILED: ["Failed", "Judging could not finish normally."],
};
function verdictLabel(value) {
  const v = String(value || "PENDING").toUpperCase();
  return VERDICT_INFO[v] ? `${v} — ${VERDICT_INFO[v][0]}` : v;
}
function badge(value) {
  const v = String(value || "PENDING").toUpperCase();
  const title = VERDICT_INFO[v] ? `${v} — ${VERDICT_INFO[v][0]}: ${VERDICT_INFO[v][1]}` : v;
  return `<span class="badge verdict-${esc(v)}" title="${esc(title)}">${esc(v)}</span>`;
}

let codeMirrorLoadPromise = null;
const activeCodeEditors = new Set();
function languageLabel(lang) {
  if (lang === "cpp") return "C++";
  if (lang === "python") return "Python";
  if (lang === "java") return "Java";
  return "C";
}
function codeMirrorMode(lang) {
  if (lang === "cpp") return "text/x-c++src";
  if (lang === "python") return "python";
  if (lang === "java") return "text/x-java";
  return "text/x-csrc";
}
function codeMirrorTheme() { return document.documentElement.dataset.theme === "dark" ? "material-darker" : "default"; }

function loadCssOnce(id, href) {
  if (document.getElementById(id)) return;
  const link = document.createElement("link");
  link.id = id; link.rel = "stylesheet"; link.href = href;
  document.head.appendChild(link);
}
function loadScriptOnce(src) {
  return new Promise((resolve, reject) => {
    const existing = [...document.scripts].find(s => s.src.endsWith(src));
    if (existing) { if (existing.dataset.loaded === "1") resolve(); else existing.addEventListener("load", resolve, { once: true }); return; }
    const script = document.createElement("script");
    script.src = src; script.async = false;
    script.onload = () => { script.dataset.loaded = "1"; resolve(); };
    script.onerror = () => reject(new Error(`Could not load editor asset: ${src}`));
    document.head.appendChild(script);
  });
}
async function loadCodeMirror() {
  if (window.CodeMirror) return window.CodeMirror;
  if (codeMirrorLoadPromise) return codeMirrorLoadPromise;
  codeMirrorLoadPromise = (async () => {
    const base = "/static/vendor/codemirror";
    loadCssOnce("cm-core-css", `${base}/codemirror.css`);
    loadCssOnce("cm-fold-css", `${base}/foldgutter.min.css`);
    loadCssOnce("cm-dialog-css", `${base}/dialog.min.css`);
    loadCssOnce("cm-dark-css", `${base}/material-darker.min.css`);
    await loadScriptOnce(`${base}/codemirror.min.js`);
    for (const file of [
      "closebrackets.min.js", "matchbrackets.min.js", "comment.min.js",
      "foldcode.min.js", "foldgutter.min.js", "brace-fold.min.js", "indent-fold.min.js",
      "searchcursor.min.js", "search.min.js", "dialog.min.js", "active-line.min.js",
      "clike.min.js", "python.min.js"
    ]) await loadScriptOnce(`${base}/${file}`);
    if (!window.CodeMirror) throw new Error("CodeMirror failed to initialize.");
    return window.CodeMirror;
  })();
  return codeMirrorLoadPromise;
}

const EDITOR_COMPLETIONS = {
  c: [
    ["printf", "printf()", 1, "stdio output"], ["scanf", "scanf()", 1, "stdio input"], ["fgets", "fgets()", 1, "read a line"],
    ["puts", "puts()", 1, "write a line"], ["strlen", "strlen()", 1, "string length"], ["strcmp", "strcmp()", 1, "compare strings"],
    ["malloc", "malloc()", 1, "allocate memory"], ["calloc", "calloc()", 1, "allocate zeroed memory"], ["realloc", "realloc()", 1, "resize allocation"],
    ["free", "free()", 1, "release memory"], ["sizeof", "sizeof()", 1, "object/type size"], ["main", "int main(void) {\n    \n    return 0;\n}", 15, "main function"],
    ["for", "for (int i = 0; i < n; ++i) {\n    \n}", 2, "for loop"], ["while", "while () {\n    \n}", 8, "while loop"],
    ["if", "if () {\n    \n}", 8, "if statement"], ["else", "else {\n    \n}", 2, "else block"], ["return", "return 0;", 0, "return statement"],
    ["struct", "struct Name {\n    \n};", 3, "structure"], ["typedef", "typedef ", 0, "type alias"], ["switch", "switch () {\n    \n}", 8, "switch statement"]
  ],
  cpp: [
    ["cout", "cout << ", 0, "standard output"], ["cin", "cin >> ", 0, "standard input"], ["vector", "vector<>", 1, "dynamic array"],
    ["string", "string ", 0, "string type"], ["sort", "sort()", 1, "sort range"], ["lower_bound", "lower_bound()", 1, "binary search"],
    ["upper_bound", "upper_bound()", 1, "binary search"], ["push_back", "push_back()", 1, "append element"], ["emplace_back", "emplace_back()", 1, "construct at end"],
    ["size", "size()", 1, "container size"], ["begin", "begin()", 1, "begin iterator"], ["end", "end()", 1, "end iterator"],
    ["map", "map<>", 1, "ordered map"], ["unordered_map", "unordered_map<>", 1, "hash map"], ["set", "set<>", 1, "ordered set"],
    ["queue", "queue<>", 1, "FIFO queue"], ["stack", "stack<>", 1, "LIFO stack"], ["priority_queue", "priority_queue<>", 1, "priority queue"],
    ["getline", "getline()", 1, "read line"], ["accumulate", "accumulate()", 1, "sum/fold range"], ["min", "min()", 1, "minimum"],
    ["max", "max()", 1, "maximum"], ["for", "for (int i = 0; i < n; ++i) {\n    \n}", 2, "for loop"],
    ["while", "while () {\n    \n}", 8, "while loop"], ["if", "if () {\n    \n}", 8, "if statement"], ["return", "return 0;", 0, "return statement"]
  ],
  python: [
    ["print", "print()", 1, "standard output"], ["input", "input()", 1, "read one line"], ["range", "range()", 1, "integer range"],
    ["len", "len()", 1, "length"], ["enumerate", "enumerate()", 1, "index and value"], ["zip", "zip()", 1, "zip iterables"],
    ["sorted", "sorted()", 1, "sorted copy"], ["list", "list()", 1, "list constructor"], ["dict", "dict()", 1, "dictionary constructor"],
    ["set", "set()", 1, "set constructor"], ["tuple", "tuple()", 1, "tuple constructor"], ["append", "append()", 1, "append item"],
    ["extend", "extend()", 1, "extend list"], ["split", "split()", 1, "split string"], ["strip", "strip()", 1, "trim whitespace"],
    ["map", "map()", 1, "map function"], ["int", "int()", 1, "integer conversion"], ["float", "float()", 1, "float conversion"],
    ["def", "def function():\n    pass", 4, "function definition"], ["class", "class Name:\n    pass", 4, "class definition"],
    ["if", "if condition:\n    pass", 4, "if statement"], ["elif", "elif condition:\n    pass", 4, "else-if branch"],
    ["else", "else:\n    pass", 4, "else branch"], ["for", "for item in iterable:\n    pass", 4, "for loop"],
    ["while", "while condition:\n    pass", 4, "while loop"], ["return", "return ", 0, "return statement"],
    ["import", "import ", 0, "import module"], ["from", "from module import ", 0, "import names"], ["try", "try:\n    pass\nexcept Exception:\n    pass", 4, "exception handling"]
  ],
  java: [
    ["println", "System.out.println()", 1, "standard output"], ["print", "System.out.print()", 1, "standard output"],
    ["sout", "System.out.println()", 1, "standard output shortcut"], ["Scanner", "Scanner", 0, "java.util.Scanner"],
    ["BufferedReader", "BufferedReader", 0, "buffered text input"], ["StringTokenizer", "StringTokenizer", 0, "tokenize input"],
    ["ArrayList", "ArrayList<>", 1, "dynamic list"], ["HashMap", "HashMap<>", 1, "hash map"], ["HashSet", "HashSet<>", 1, "hash set"],
    ["Arrays", "Arrays.", 0, "array utilities"], ["Collections", "Collections.", 0, "collection utilities"],
    ["StringBuilder", "StringBuilder()", 1, "mutable string builder"], ["parseInt", "Integer.parseInt()", 1, "parse int"],
    ["parseLong", "Long.parseLong()", 1, "parse long"], ["Math", "Math.", 0, "math utilities"],
    ["main", "public static void main(String[] args) {\n        \n    }", 6, "Java entry point"],
    ["for", "for (int i = 0; i < n; i++) {\n    \n}", 2, "for loop"], ["while", "while () {\n    \n}", 8, "while loop"],
    ["if", "if () {\n    \n}", 8, "if statement"], ["else", "else {\n    \n}", 2, "else block"], ["return", "return;", 0, "return statement"],
    ["class", "class Name {\n    \n}", 2, "class declaration"], ["public", "public ", 0, "access modifier"], ["static", "static ", 0, "static member"]
  ]
};

function completionPrefix(cm) {
  const cursor = cm.getCursor();
  const token = cm.getTokenAt(cursor);
  if (token.type && (String(token.type).includes("comment") || String(token.type).includes("string"))) return null;
  const line = cm.getLine(cursor.line);
  let start = cursor.ch;
  while (start > 0 && /[A-Za-z0-9_]/.test(line.charAt(start - 1))) start--;
  return { cursor, start, prefix: line.slice(start, cursor.ch) };
}

function userDefinedFunctionCompletions(cm, language) {
  const source = cm.getValue();
  const found = new Map();
  const add = (name, args = "", kind = "function") => {
    if (!name || /^(if|for|while|switch|catch|sizeof)$/.test(name)) return;
    const cleanArgs = String(args || "").trim();
    let argCount = 0;
    if (cleanArgs && cleanArgs !== "void") {
      argCount = cleanArgs.split(",").map(x => x.trim()).filter(Boolean).length;
    }
    const detail = argCount ? `user-defined ${kind} · ${argCount} arg${argCount === 1 ? "" : "s"}` : `user-defined ${kind}`;
    if (!found.has(name)) found.set(name, [name, `${name}()`, 1, detail, "user"]);
  };

  if (language === "python") {
    const re = /^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\(([^)]*)\)\s*(?:->[^:]*)?:/gm;
    let m;
    while ((m = re.exec(source)) !== null) add(m[1], m[2], "function");
  } else if (language === "java") {
    const re = /^\s*(?:(?:public|private|protected|static|final|synchronized|abstract|native|strictfp|default)\s+)*(?:<[^>\n]+>\s*)?[A-Za-z_$][\w$<>,.?\[\]]*(?:\s+|\s*[\[\]]+\s+)([A-Za-z_$][\w$]*)\s*\(([^)]*)\)\s*(?:throws\s+[^\{]+)?\{/gm;
    let m;
    while ((m = re.exec(source)) !== null) add(m[1], m[2], "method");
  } else {
    // C/C++ function definitions. Requiring an opening brace avoids treating
    // ordinary function calls as declarations.
    const re = /^\s*(?!if\b|for\b|while\b|switch\b|catch\b)(?:[A-Za-z_][\w:<>,~]*\s+|(?:const|static|inline|extern|constexpr|virtual|unsigned|signed|long|short|struct\s+\w+|class\s+\w+)\s+|[*&]\s*)+([A-Za-z_]\w*)\s*\(([^;{}()]*)\)\s*(?:const\s*)?(?:noexcept\s*)?(?:->\s*[^\{]+)?\{/gm;
    let m;
    while ((m = re.exec(source)) !== null) add(m[1], m[2], "function");
  }
  return [...found.values()];
}

function mergedJudgeCompletions(cm, language) {
  const merged = [];
  const seen = new Set();
  for (const item of [...userDefinedFunctionCompletions(cm, language), ...(EDITOR_COMPLETIONS[language] || [])]) {
    const key = String(item[0]).toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    merged.push(item);
  }
  return merged;
}
function hideJudgeCompletions(cm) {
  const state = cm.state.judgeCompletion;
  if (state?.popup) state.popup.remove();
  cm.state.judgeCompletion = null;
}
function renderJudgeCompletionSelection(cm) {
  const state = cm.state.judgeCompletion;
  if (!state) return;
  [...state.popup.querySelectorAll(".judge-completion-item")].forEach((el, index) => {
    el.classList.toggle("active", index === state.index);
  });
  state.popup.querySelector(".judge-completion-item.active")?.scrollIntoView({ block: "nearest" });
}
function pickJudgeCompletion(cm, index = null) {
  const state = cm.state.judgeCompletion;
  if (!state) return false;
  const i = index ?? state.index;
  const item = state.items[i];
  if (!item) return false;
  const from = { line: state.cursor.line, ch: state.start };
  const to = state.cursor;
  cm.operation(() => {
    cm.replaceRange(item[1], from, to, "complete");
    const inserted = item[1].split("\n");
    const endLine = from.line + inserted.length - 1;
    const endCh = (inserted.length === 1 ? from.ch : 0) + inserted[inserted.length - 1].length;
    cm.setCursor({ line: endLine, ch: Math.max(0, endCh - (item[2] || 0)) });
  });
  hideJudgeCompletions(cm);
  cm.focus();
  return true;
}
function moveJudgeCompletion(cm, delta) {
  const state = cm.state.judgeCompletion;
  if (!state) return false;
  state.index = (state.index + delta + state.items.length) % state.items.length;
  renderJudgeCompletionSelection(cm);
  return true;
}
function showJudgeCompletions(cm, { force = false } = {}) {
  hideJudgeCompletions(cm);
  const info = completionPrefix(cm);
  if (!info) return false;
  const prefix = info.prefix;
  if (!force && prefix.length < 2) return false;
  const language = cm.state.judgeLanguage || "c";
  const items = mergedJudgeCompletions(cm, language)
    .filter(item => !prefix || item[0].toLowerCase().startsWith(prefix.toLowerCase()))
    .sort((a, b) => {
      const aUser = a[4] === "user" ? 0 : 1, bUser = b[4] === "user" ? 0 : 1;
      if (aUser !== bUser) return aUser - bUser;
      return a[0].localeCompare(b[0]);
    })
    .slice(0, 18);
  if (!items.length) return false;

  const popup = document.createElement("div");
  popup.className = "judge-completion-popup";
  items.forEach((item, index) => {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "judge-completion-item";
    row.innerHTML = `<strong>${esc(item[0])}</strong>${item[3] ? `<small>${esc(item[3])}</small>` : ""}`;
    row.onmousedown = e => e.preventDefault();
    row.onclick = () => pickJudgeCompletion(cm, index);
    popup.appendChild(row);
  });
  // During browser fullscreen, only the fullscreen element and its descendants
  // are rendered. Mount the completion popup inside that element instead of
  // document.body so suggestions remain available in both student and admin
  // fullscreen editors. Fixed positioning keeps the geometry viewport-based.
  const popupRoot = document.fullscreenElement || document.body;
  popupRoot.appendChild(popup);
  const coords = cm.cursorCoords(null, "window");
  const width = Math.min(360, Math.max(250, popup.offsetWidth || 280));
  popup.style.width = `${width}px`;
  let left = coords.left;
  let top = coords.bottom + 4;
  if (left + width > window.innerWidth - 12) left = window.innerWidth - width - 12;
  if (top + Math.min(260, popup.offsetHeight) > window.innerHeight - 12) {
    top = Math.max(8, coords.top - Math.min(260, popup.offsetHeight) - 4);
  }
  popup.style.left = `${Math.max(8, left)}px`;
  popup.style.top = `${Math.max(8, top)}px`;
  cm.state.judgeCompletion = { popup, items, index: 0, cursor: info.cursor, start: info.start };
  renderJudgeCompletionSelection(cm);
  return true;
}
function scheduleJudgeCompletions(cm, change) {
  if (change.origin !== "+input") return;
  const typed = (change.text || []).join("");
  if (!/[A-Za-z0-9_]$/.test(typed)) { hideJudgeCompletions(cm); return; }
  clearTimeout(cm.state.judgeCompletionTimer);
  cm.state.judgeCompletionTimer = setTimeout(() => showJudgeCompletions(cm), 110);
}

function installSmartTextarea(textarea, initialLanguage = "c") {
  let currentLanguage = initialLanguage;
  const pairs = { "(": ")", "[": "]", "{": "}", '"': '"', "'": "'" };
  const closers = new Set(Object.values(pairs));
  textarea.addEventListener("keydown", e => {
    const start = textarea.selectionStart, end = textarea.selectionEnd, value = textarea.value;
    if (e.key === "Tab") {
      e.preventDefault();
      if (start === end) {
        if (e.shiftKey) {
          const lineStart = value.lastIndexOf("\n", start - 1) + 1;
          const before = value.slice(lineStart, start);
          const remove = Math.min(4, (before.match(/^ {1,4}/) || [""])[0].length);
          textarea.value = value.slice(0, lineStart) + value.slice(lineStart + remove);
          textarea.selectionStart = textarea.selectionEnd = Math.max(lineStart, start - remove);
        } else {
          textarea.value = value.slice(0, start) + "    " + value.slice(end);
          textarea.selectionStart = textarea.selectionEnd = start + 4;
        }
      } else {
        const lineStart = value.lastIndexOf("\n", start - 1) + 1;
        let block = value.slice(lineStart, end);
        block = e.shiftKey ? block.replace(/^ {1,4}/gm, "") : block.replace(/^/gm, "    ");
        textarea.value = value.slice(0, lineStart) + block + value.slice(end);
        textarea.selectionStart = lineStart; textarea.selectionEnd = lineStart + block.length;
      }
      textarea.dispatchEvent(new Event("input")); return;
    }
    if (e.key === "Enter") {
      const lineStart = value.lastIndexOf("\n", start - 1) + 1;
      const current = value.slice(lineStart, start);
      const indent = (current.match(/^\s*/) || [""])[0];
      const trimmed = current.trimEnd();
      const pythonBlock = currentLanguage === "python" && trimmed.endsWith(":");
      const braceBlock = currentLanguage !== "python" && trimmed.endsWith("{");
      const betweenBraces = currentLanguage !== "python" && value[start - 1] === "{" && value[start] === "}";
      if (pythonBlock || braceBlock || betweenBraces) {
        e.preventDefault();
        if (betweenBraces) {
          textarea.value = value.slice(0, start) + "\n" + indent + "    \n" + indent + value.slice(end);
          textarea.selectionStart = textarea.selectionEnd = start + 1 + indent.length + 4;
        } else {
          textarea.value = value.slice(0, start) + "\n" + indent + "    " + value.slice(end);
          textarea.selectionStart = textarea.selectionEnd = start + 1 + indent.length + 4;
        }
        textarea.dispatchEvent(new Event("input")); return;
      }
    }
    if (pairs[e.key]) {
      const next = value.slice(start, end);
      if (start === end && (e.key === '"' || e.key === "'") && value[start] === e.key) {
        e.preventDefault(); textarea.selectionStart = textarea.selectionEnd = start + 1; return;
      }
      e.preventDefault();
      const close = pairs[e.key];
      textarea.value = value.slice(0, start) + e.key + next + close + value.slice(end);
      textarea.selectionStart = start + 1; textarea.selectionEnd = start + 1 + next.length;
      textarea.dispatchEvent(new Event("input")); return;
    }
    if (closers.has(e.key) && start === end && value[start] === e.key) {
      e.preventDefault(); textarea.selectionStart = textarea.selectionEnd = start + 1; return;
    }
    if (e.key === "Backspace" && start === end && start > 0 && pairs[value[start - 1]] === value[start]) {
      e.preventDefault(); textarea.value = value.slice(0, start - 1) + value.slice(start + 1); textarea.selectionStart = textarea.selectionEnd = start - 1; textarea.dispatchEvent(new Event("input")); return;
    }
  });
  return { setLanguage(lang) { currentLanguage = lang; } };
}
async function createJudgeEditor({ textareaId, hostId, statusId, language, value, onChange }) {
  const textarea = document.getElementById(textareaId);
  const host = document.getElementById(hostId);
  const status = document.getElementById(statusId);
  const fallback = () => {
    host.classList.add("hidden"); textarea.classList.remove("hidden"); textarea.value = value;
    const smart = installSmartTextarea(textarea, language);
    const updateStatus = () => { const before = textarea.value.slice(0, textarea.selectionStart); const lines = before.split("\n"); if (status) status.textContent = `Ln ${lines.length}, Col ${lines.at(-1).length + 1} · Spaces: 4 · ${languageLabel(language)} · Basic editor`; };
    textarea.addEventListener("input", () => { updateStatus(); onChange?.(textarea.value); });
    textarea.addEventListener("click", updateStatus); textarea.addEventListener("keyup", updateStatus); updateStatus();
    let fallbackFont = 14, fallbackWrap = false;
    const adapter = {
      mode: "fallback", getValue: () => textarea.value, setValue: v => { textarea.value = String(v ?? ""); textarea.dispatchEvent(new Event("input")); },
      focus: () => textarea.focus(), setLanguage: l => { language = l; smart.setLanguage(l); updateStatus(); }, layout: () => { },
      setTheme: () => { },
      setFontSize: delta => { fallbackFont = Math.max(11, Math.min(24, fallbackFont + delta)); textarea.style.fontSize = `${fallbackFont}px`; },
      toggleWrap: () => { fallbackWrap = !fallbackWrap; textarea.style.whiteSpace = fallbackWrap ? "pre-wrap" : "pre"; textarea.style.overflowWrap = fallbackWrap ? "anywhere" : "normal"; return fallbackWrap; },
      dispose: () => { activeCodeEditors.delete(adapter); },
    };
    activeCodeEditors.add(adapter); return adapter;
  };
  try {
    const CodeMirror = await loadCodeMirror();
    textarea.value = String(value ?? "");
    textarea.classList.remove("hidden");
    host.classList.add("hidden");
    const cm = CodeMirror.fromTextArea(textarea, {
      mode: codeMirrorMode(language), theme: codeMirrorTheme(),
      lineNumbers: true, lineWrapping: false, indentUnit: 4, tabSize: 4, indentWithTabs: false,
      smartIndent: true, electricChars: true, autoCloseBrackets: true, matchBrackets: true,
      styleActiveLine: true, foldGutter: true,
      gutters: ["CodeMirror-linenumbers", "CodeMirror-foldgutter"],
      viewportMargin: 20,
      extraKeys: {
        "Tab": editor => { if (pickJudgeCompletion(editor)) return; if (editor.somethingSelected()) editor.indentSelection("add"); else editor.replaceSelection("    ", "end", "+input"); },
        "Up": editor => moveJudgeCompletion(editor, -1) || CodeMirror.Pass,
        "Down": editor => moveJudgeCompletion(editor, 1) || CodeMirror.Pass,
        "Enter": editor => pickJudgeCompletion(editor) || CodeMirror.Pass,
        "Esc": editor => { if (editor.state.judgeCompletion) { hideJudgeCompletions(editor); return; } return CodeMirror.Pass; },
        "Shift-Tab": editor => editor.indentSelection("subtract"),
        "Ctrl-/": "toggleComment", "Cmd-/": "toggleComment",
        "Ctrl-F": "find", "Cmd-F": "find",
        "Ctrl-H": "replace", "Cmd-Alt-F": "replace",
        "Ctrl-Space": editor => showJudgeCompletions(editor, { force: true }),
        "Ctrl-S": editor => { onChange?.(editor.getValue()); toast("Draft saved locally"); },
        "Cmd-S": editor => { onChange?.(editor.getValue()); toast("Draft saved locally"); },
      },
    });
    const wrapper = cm.getWrapperElement();
    host.parentNode.insertBefore(wrapper, host.nextSibling);
    wrapper.classList.add("judge-codemirror");
    wrapper.__judgeCodeMirror = cm;
    cm.state.judgeLanguage = language;
    let wrap = false, fontSize = 15;
    wrapper.style.fontSize = `${fontSize}px`;
    const updateStatus = () => { const pos = cm.getCursor(); if (status) status.textContent = `Ln ${pos.line + 1}, Col ${pos.ch + 1} · Spaces: 4 · ${languageLabel(language)} · Advanced editor`; };
    const refresh = () => { try { cm.refresh(); } catch { } };
    cm.on("cursorActivity", updateStatus);
    cm.on("change", (editor, change) => { updateStatus(); onChange?.(cm.getValue()); scheduleJudgeCompletions(editor, change); });
    cm.on("focus", updateStatus);
    cm.on("blur", () => setTimeout(() => hideJudgeCompletions(cm), 120));
    cm.on("scroll", () => hideJudgeCompletions(cm));
    requestAnimationFrame(refresh); setTimeout(refresh, 80); setTimeout(() => cm.focus(), 120);
    const adapter = {
      mode: "codemirror", getValue: () => cm.getValue(), setValue: v => { const next = String(v ?? ""); if (cm.getValue() !== next) cm.setValue(next); }, focus: () => cm.focus(),
      setLanguage: l => { language = l; cm.state.judgeLanguage = l; hideJudgeCompletions(cm); cm.setOption("mode", codeMirrorMode(l)); updateStatus(); },
      layout: () => { refresh(); setTimeout(refresh, 60); }, setTheme: () => { cm.setOption("theme", codeMirrorTheme()); refresh(); },
      setFontSize: delta => { fontSize = Math.max(11, Math.min(24, fontSize + delta)); wrapper.style.fontSize = `${fontSize}px`; refresh(); },
      toggleWrap: () => { wrap = !wrap; cm.setOption("lineWrapping", wrap); refresh(); return wrap; },
      dispose: () => { hideJudgeCompletions(cm); clearTimeout(cm.state.judgeCompletionTimer); activeCodeEditors.delete(adapter); try { cm.toTextArea(); } catch { } textarea.classList.add("hidden"); }, editor: cm,
    };
    activeCodeEditors.add(adapter); updateStatus(); return adapter;
  } catch (error) { console.warn("Advanced editor unavailable; using smart textarea fallback.", error); return fallback(); }
}
function identityHtml(name, email, extra = "") { return `<span class="identity-cell"><strong>${esc(name || "—")}</strong>${email ? `<small>${esc(email)}</small>` : ""}${extra ? `<small>${esc(extra)}</small>` : ""}</span>`; }
function busy(button, value, label = "Working…") { if (!button) return; if (value) { button.dataset.text = button.textContent; button.textContent = label; button.disabled = true; } else { button.textContent = button.dataset.text || button.textContent; button.disabled = false; } }
function openModal(html) { modalContent.innerHTML = `<button class="btn close-modal" type="button">Close</button>${html}`; modalContent.querySelector(".close-modal").onclick = () => modal.close(); modal.showModal(); }

function initTheme() {
  const saved = localStorage.getItem("judge-theme");
  const theme = saved || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  document.documentElement.dataset.theme = theme; themeToggle.textContent = theme === "dark" ? "☀" : "☾";
}
themeToggle.onclick = () => { const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark"; document.documentElement.dataset.theme = next; localStorage.setItem("judge-theme", next); themeToggle.textContent = next === "dark" ? "☀" : "☾"; activeCodeEditors.forEach(editor => editor.setTheme?.()); };
menuButton.onclick = () => nav.classList.toggle("open");

function renderNav() {
  if (!state.user) { nav.innerHTML = `<a class="nav-link" href="#/login">Login</a><a class="nav-link" href="#/register">Register</a>`; return; }
  const pending = state.user.role !== "admin" && state.user.exam_code_required;
  nav.innerHTML = `
    ${pending ? "" : `<a class="nav-link" href="#/problems">Problems</a>${state.user.role !== "admin" && !state.examModeActive ? `<a class="nav-link" href="#/practice">Practice</a>` : ""}<a class="nav-link" href="#/submissions">Submissions</a><a class="nav-link" href="#/reference/c">Syntax</a><a class="nav-link" href="#/verdicts">Verdicts</a>`}
    ${state.user.role === "admin" ? `<a class="nav-link" href="#/admin">Admin</a>` : ""}
    ${!pending && state.user.role !== "admin" ? `<a class="nav-link" href="#/results">Results</a><a class="nav-link" href="#/account">Account</a>` : ""}
    ${!pending && state.user.role !== "admin" && state.examModeActive ? `<span id="nav-violations" class="violation-pill">Violations: ${state.violationCount}</span>` : ""}
    <span class="user-chip"><span class="user-chip-name">${esc(state.user.full_name || state.user.username)}</span><small>${esc(state.user.email || "")}</small></span>
    <button id="logout" class="nav-button" type="button">Logout</button>`;
  document.getElementById("logout").onclick = logout;
  nav.querySelectorAll("a").forEach(a => a.onclick = () => nav.classList.remove("open"));
}

async function logout() { try { await api("/api/auth/logout", { method: "POST" }); } catch (e) { } state.user = null; stopAll(); location.hash = "#/login"; renderNav(); }
async function loadMe() { try { state.user = await api("/api/auth/me"); } catch (e) { if (e.status === 401) state.user = null; else throw e; } }
function stopAll() { if (state.polling) clearTimeout(state.polling); if (state.heartbeat) clearInterval(state.heartbeat); state.polling = null; state.heartbeat = null; stopMonitoring(); }
function routeParts() { return (location.hash || "#/problems").replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent); }

async function refreshExamState() {
  if (!state.user || state.user.role === "admin" || state.user.exam_code_required) { state.examModeActive = false; state.violationCount = 0; return; }
  try {
    const status = await api("/api/exam/status");
    state.examModeActive = Boolean(status.enabled);
    if (state.examModeActive) {
      const counts = await api("/api/exam/flags/count");
      state.violationCount = Number(counts.violation_count || 0);
    } else state.violationCount = 0;
  } catch (_e) { state.examModeActive = false; }
}
function updateViolationDisplays(count, eventType = "") {
  state.violationCount = Number(count || 0);
  const navCount = document.getElementById("nav-violations"); if (navCount) navCount.textContent = `Violations: ${state.violationCount}`;
  const local = document.getElementById("violation-count"); if (local) local.textContent = `Violations: ${state.violationCount}`;
  const warning = document.getElementById("exam-warning");
  if (warning && eventType) { warning.classList.remove("hidden"); warning.innerHTML = `<strong>Warning:</strong> ${esc(eventType.replaceAll("_", " "))} was recorded. Total violations: <strong>${state.violationCount}</strong>.`; }
}
function passwordField(name, label, id, extra = "") {
  return `<div class="field"><label for="${id}">${esc(label)}</label><div class="password-wrap"><input id="${id}" name="${name}" type="password" ${extra}><button type="button" class="password-eye" data-target="${id}" aria-label="Show password" title="Show/hide password">👁</button></div></div>`;
}
function wirePasswordToggles(root = document) {
  root.querySelectorAll(".password-eye").forEach(button => button.onclick = () => { const input = document.getElementById(button.dataset.target); if (!input) return; const showing = input.type === "text"; input.type = showing ? "password" : "text"; button.textContent = showing ? "👁" : "🙈"; button.setAttribute("aria-label", showing ? "Show password" : "Hide password"); });
}
function installEditorTab(editor) {
  editor.addEventListener("keydown", e => {
    if (e.key !== "Tab") return;
    e.preventDefault();
    const start = editor.selectionStart, end = editor.selectionEnd, value = editor.value;
    if (start === end) {
      if (e.shiftKey) { const lineStart = value.lastIndexOf("\n", start - 1) + 1; const before = value.slice(lineStart, start); const remove = Math.min(4, (before.match(/^ {1,4}/) || [""])[0].length); editor.value = value.slice(0, lineStart) + value.slice(lineStart + remove); editor.selectionStart = editor.selectionEnd = Math.max(lineStart, start - remove); }
      else { editor.value = value.slice(0, start) + "    " + value.slice(end); editor.selectionStart = editor.selectionEnd = start + 4; }
    } else {
      const lineStart = value.lastIndexOf("\n", start - 1) + 1; let block = value.slice(lineStart, end);
      if (e.shiftKey) block = block.replace(/^ {1,4}/gm, ""); else block = block.replace(/^/gm, "    ");
      editor.value = value.slice(0, lineStart) + block + value.slice(end); editor.selectionStart = lineStart; editor.selectionEnd = lineStart + block.length;
    }
    editor.dispatchEvent(new Event("input"));
  });
}

async function route() {
  stopAll(); state.currentProblemId = null; renderNav(); const parts = routeParts(); const section = parts[0] || "problems";
  if (!state.user && !["login", "register", "recover", "recover-complete"].includes(section)) { location.hash = "#/login"; return; }
  if (state.user && state.user.role !== "admin" && state.user.exam_code_required && section !== "exam-code") { location.hash = "#/exam-code"; return; }
  if (state.user && !state.user.exam_code_required && ["login", "register", "exam-code"].includes(section)) { location.hash = "#/problems"; return; }
  try {
    if (state.user && state.user.role !== "admin" && !state.user.exam_code_required) await refreshExamState();
    renderNav();
    if (section === "login") renderLogin();
    else if (section === "register") await renderRegister();
    else if (section === "recover") renderRecovery();
    else if (section === "account-recovery") renderAccountRecovery();
    else if (section === "recover-complete") renderRecoveryComplete();
    else if (section === "exam-code") renderExamCode();
    else if (section === "account") renderAccount();
    else if (section === "reference") renderReference(parts[1] || "c");
    else if (section === "verdicts") renderVerdicts();
    else if (section === "problems") await renderProblems(false);
    else if (section === "practice") await renderProblems(true);
    else if (section === "problem" && parts[1]) await renderProblem(Number(parts[1]));
    else if (section === "submissions") await renderSubmissions();
    else if (section === "submission" && parts[1]) await renderSubmission(parts[1]);
    else if (section === "results") await renderResults();
    else if (section === "admin" && parts[1] === "submissions" && parts[2] === "user" && parts[3]) await renderAdminUserSubmissions(Number(parts[3]));
    else if (section === "admin" && parts[1] === "problem-test" && parts[2]) await renderAdminProblemTest(Number(parts[2]));
    else if (section === "admin") await renderAdmin(parts[1] || state.adminTab);
    else app.innerHTML = `<section class="card"><h1>Not found</h1></section>`;
    if (state.user && state.user.role !== "admin" && state.examModeActive && !state.user.exam_code_required) { startMonitoring(state.currentProblemId); updateViolationDisplays(state.violationCount); }
  } catch (e) {
    if (e.status === 401) { state.user = null; renderNav(); location.hash = "#/login"; return; }
    if (e.status === 428) { state.user.exam_code_required = true; location.hash = "#/exam-code"; return; }
    app.innerHTML = `<section class="card"><h1>Unable to load</h1><div class="alert alert-danger">${esc(e.message)}</div></section>`;
  }
}

function renderLogin() {
  app.innerHTML = `<section class="auth-layout"><div class="auth-copy"><span class="badge">College LAN portal</span><h1>Secure programming assessment</h1><p class="lead muted">Sign in using your enrollment number or registered email. During Exam Mode, the lab code is requested after password verification.</p></div><div class="auth-card"><h2>Sign in</h2><form id="login-form" class="form-grid"><div class="field"><label>Enrollment number or email</label><input name="identifier" required autofocus></div>${passwordField("password", "Password", "login-password", "required autocomplete=\"current-password\"")}<button class="btn btn-primary">Sign in</button></form><p><a href="#/register">New student registration</a> · <a href="#/recover">Forgot password</a> · <a href="#/account-recovery">Account recovery</a></p></div></section>`;
  wirePasswordToggles(app);
  document.getElementById("login-form").onsubmit = async e => { e.preventDefault(); const b = e.currentTarget.querySelector("button.btn-primary"); busy(b, true, "Signing in…"); try { state.user = await api("/api/auth/login", { method: "POST", body: { identifier: e.currentTarget.identifier.value.trim(), password: e.currentTarget.password.value } }); await refreshExamState(); renderNav(); location.hash = state.user.exam_code_required ? "#/exam-code" : "#/problems"; } catch (x) { fail(x); } finally { busy(b, false); } };
}

async function renderRegister() {
  let policy = { mode: "ALL", allowed_domain: "all domains" };
  try { policy = await api("/api/auth/registration-policy"); } catch (_e) { }
  const policyText = policy.mode === "ALL" ? "Registration currently accepts all email domains." : `Registration currently requires an @${esc(policy.allowed_domain)} email address.`;
  app.innerHTML = `<section class="auth-layout"><div class="auth-copy"><h1>Student registration</h1><p class="lead muted">Register with your institute details. A six-digit OTP will be sent to your email; successful verification activates the account immediately.</p><div class="alert"><strong>Email policy:</strong> ${policyText}</div></div><div class="auth-card"><h2>Create account</h2><div id="registration-stage"><form id="register-form" class="form-grid"><div class="field"><label>Full name</label><input name="full_name" required></div><div class="field"><label>Email</label><input name="email" type="email" required></div><div class="field"><label>Enrollment number</label><input name="enrollment_number" required></div>${passwordField("password", "Password", "register-password", "minlength=\"10\" required autocomplete=\"new-password\"")}${passwordField("confirm_password", "Re-confirm password", "register-confirm", "minlength=\"10\" required autocomplete=\"new-password\"")}<small class="muted">Password must contain upper-case, lower-case and a number.</small><button class="btn btn-primary">Send verification OTP</button></form></div><p><a href="#/login">Back to login</a></p></div></section>`;
  wirePasswordToggles(app);
  document.getElementById("register-form").onsubmit = async e => {
    e.preventDefault(); const f = e.currentTarget, b = f.querySelector("button.btn-primary");
    if (f.password.value !== f.confirm_password.value) { fail(new Error("Passwords do not match.")); return; }
    busy(b, true, "Sending OTP…");
    try {
      const r = await api("/api/auth/register", { method: "POST", body: { full_name: f.full_name.value, email: f.email.value, enrollment_number: f.enrollment_number.value, password: f.password.value, confirm_password: f.confirm_password.value } });
      document.getElementById("registration-stage").innerHTML = `<div class="alert">${esc(r.message)} The code expires in ${r.expires_in_minutes} minutes.</div><form id="registration-otp-form" class="form-grid"><div class="field"><label>Verification OTP</label><input name="otp" inputmode="numeric" pattern="[0-9]{6}" maxlength="6" required autofocus></div><button class="btn btn-primary">Verify email and create account</button><button id="restart-registration" class="btn" type="button">Start again / resend</button></form>`;
      document.getElementById("restart-registration").onclick = () => renderRegister();
      document.getElementById("registration-otp-form").onsubmit = async ev => { ev.preventDefault(); const vf = ev.currentTarget, vb = vf.querySelector("button.btn-primary"); busy(vb, true, "Verifying…"); try { const done = await api("/api/auth/register/verify", { method: "POST", body: { challenge_id: r.challenge_id, otp: vf.otp.value } }); toast(done.message); location.hash = "#/login"; } catch (x) { fail(x); } finally { busy(vb, false); } };
    } catch (x) { fail(x); } finally { busy(b, false); }
  };
}

function renderRecovery() {
  app.innerHTML = `<section class="auth-layout"><div class="auth-copy"><h1>Forgot password</h1><p class="lead muted">Enter your enrollment number or registered email. A six-digit OTP will be sent to the registered email address; administrator approval is not required.</p></div><div class="auth-card"><div id="password-reset-stage"><form id="password-reset-request" class="form-grid"><div class="field"><label>Enrollment number or registered email</label><input name="identifier" required autofocus></div><button class="btn btn-primary">Send password-reset OTP</button></form></div><p><a href="#/login">Back to login</a> · <a href="#/account-recovery">Cannot access registered email?</a></p></div></section>`;
  document.getElementById("password-reset-request").onsubmit = async e => {
    e.preventDefault(); const f = e.currentTarget, b = f.querySelector("button.btn-primary"); busy(b, true, "Sending OTP…");
    try {
      const r = await api("/api/auth/password-reset/request", { method: "POST", body: { identifier: f.identifier.value.trim() } });
      document.getElementById("password-reset-stage").innerHTML = `<div class="alert">${esc(r.message)} The code expires in ${r.expires_in_minutes} minutes.</div><form id="password-reset-verify" class="form-grid"><div class="field"><label>Verification OTP</label><input name="otp" inputmode="numeric" pattern="[0-9]{6}" maxlength="6" required autofocus></div>${passwordField("new_password", "New password", "reset-new-password", "minlength=\"10\" required autocomplete=\"new-password\"")}${passwordField("confirm_new_password", "Re-confirm new password", "reset-confirm-password", "minlength=\"10\" required autocomplete=\"new-password\"")}<button class="btn btn-primary">Reset password</button><button id="restart-reset" class="btn" type="button">Start again / resend</button></form>`;
      wirePasswordToggles(document.getElementById("password-reset-stage"));
      document.getElementById("restart-reset").onclick = () => renderRecovery();
      document.getElementById("password-reset-verify").onsubmit = async ev => { ev.preventDefault(); const vf = ev.currentTarget, vb = vf.querySelector("button.btn-primary"); if (vf.new_password.value !== vf.confirm_new_password.value) { fail(new Error("New passwords do not match.")); return; } busy(vb, true, "Updating…"); try { const done = await api("/api/auth/password-reset/verify", { method: "POST", body: { challenge_id: r.challenge_id, otp: vf.otp.value, new_password: vf.new_password.value, confirm_new_password: vf.confirm_new_password.value } }); toast(done.message); location.hash = "#/login"; } catch (x) { fail(x); } finally { busy(vb, false); } };
    } catch (x) { fail(x); } finally { busy(b, false); }
  };
}

function renderAccountRecovery() {
  app.innerHTML = `<section class="auth-layout"><div class="auth-copy"><h1>Account recovery</h1><p class="lead muted">Use this only when you cannot use the registered email or the account needs administrative recovery. This request still requires administrator approval.</p></div><div class="auth-card"><form id="recovery-form" class="form-grid"><input type="hidden" name="request_type" value="ACCOUNT_RECOVERY"><div class="field"><label>Full name</label><input name="full_name" required></div><div class="field"><label>Enrollment number</label><input name="enrollment_number"></div><div class="field"><label>Registered email, if known</label><input name="email" type="email"></div><div class="field"><label>New email, if email access is lost</label><input name="new_email" type="email"></div><div class="field"><label>Reason</label><textarea name="reason" required></textarea></div><button class="btn btn-primary">Submit account-recovery request</button></form><hr><p>Already received an approved recovery token? <a href="#/recover-complete">Complete account recovery</a>.</p></div></section>`;
  document.getElementById("recovery-form").onsubmit = async e => { e.preventDefault(); const f = e.currentTarget, b = f.querySelector("button"); busy(b, true); try { const r = await api("/api/auth/recovery/request", { method: "POST", body: Object.fromEntries(new FormData(f)) }); openModal(`<h2>Account recovery submitted</h2><p>Request ID:</p><div class="code-box">${esc(r.request_id)}</div><p>${esc(r.message)}</p>`); } catch (x) { fail(x); } finally { busy(b, false); } };
}

function renderRecoveryComplete() {
  app.innerHTML = `<section class="auth-layout"><div class="auth-copy"><h1>Complete approved account recovery</h1><p class="lead muted">Enter the request ID and one-time recovery token supplied by the administrator.</p></div><div class="auth-card"><form id="complete-form" class="form-grid"><div class="field"><label>Request ID</label><input name="request_id" required></div><div class="field"><label>One-time token</label><input name="token" required></div>${passwordField("new_password", "New password", "recovery-new-password", "minlength=\"10\" required")}<button class="btn btn-primary">Update password</button></form></div></section>`;
  wirePasswordToggles(app);
  document.getElementById("complete-form").onsubmit = async e => { e.preventDefault(); const f = e.currentTarget, b = f.querySelector("button"); busy(b, true); try { const r = await api("/api/auth/recovery/complete", { method: "POST", body: Object.fromEntries(new FormData(f)) }); toast(r.message); location.hash = "#/login"; } catch (x) { fail(x); } finally { busy(b, false); } };
}
function renderExamCode() {
  app.innerHTML = `<section class="auth-layout"><div class="auth-copy"><span class="badge warning">Exam Mode active</span><h1>Enter the common lab code</h1><p class="lead muted">The administrator announces one six-digit code in the lab. The current code can be used only once by your account.</p></div><div class="auth-card"><form id="code-form" class="form-grid"><div class="field"><label>Exam code</label><input name="code" inputmode="numeric" pattern="[0-9]{6}" maxlength="6" required autofocus></div><button class="btn btn-primary">Verify and enter</button></form></div></section>`;
  document.getElementById("code-form").onsubmit = async e => { e.preventDefault(); const f = e.currentTarget, b = f.querySelector("button"); busy(b, true); try { await api("/api/exam/verify-code", { method: "POST", body: { code: f.code.value } }); state.user = await api("/api/auth/me"); renderNav(); location.hash = "#/problems"; } catch (x) { fail(x); } finally { busy(b, false); } };
}

function renderAccount() {
  app.innerHTML = `<section class="section-head"><div><h1>Account</h1><p class="muted">Change your password using the current password. Administrator approval is not required.</p></div></section><section class="card" style="max-width:680px"><h2>Change password</h2><form id="change-password-form" class="form-grid">${passwordField("current_password", "Current password", "current-password", "required autocomplete=\"current-password\"")}${passwordField("new_password", "New password", "new-password", "minlength=\"10\" required autocomplete=\"new-password\"")}${passwordField("confirm_new_password", "Re-confirm new password", "confirm-new-password", "minlength=\"10\" required autocomplete=\"new-password\"")}<button class="btn btn-primary">Update password</button></form></section>`;
  wirePasswordToggles(app);
  document.getElementById("change-password-form").onsubmit = async e => { e.preventDefault(); const f = e.currentTarget, b = f.querySelector("button.btn-primary"); if (f.new_password.value !== f.confirm_new_password.value) { fail(new Error("New passwords do not match.")); return; } busy(b, true); try { await api("/api/auth/change-password", { method: "POST", body: { current_password: f.current_password.value, new_password: f.new_password.value, confirm_new_password: f.confirm_new_password.value } }); f.reset(); toast("Password changed successfully."); } catch (x) { fail(x); } finally { busy(b, false); } };
}
function renderVerdicts() {
  const ordered = ["AC", "WA", "CE", "RE", "TLE", "MLE", "OLE", "SE", "OK", "QUEUED", "COMPILING", "RUNNING"];
  app.innerHTML = `<section class="section-head"><div><h1>Judge verdict reference</h1><p class="muted">Quick reference for the status codes shown after submission.</p></div><a class="btn" href="#/problems">Back to problems</a></section><section class="card"><div class="verdict-reference-grid">${ordered.map(code => { const info = VERDICT_INFO[code]; return `<article class="verdict-reference-item"><div>${badge(code)} <strong>${esc(info[0])}</strong></div><p class="muted">${esc(info[1])}</p></article>`; }).join("")}</div></section>`;
}

function renderReference(language) {
  const allowed = ["c", "cpp", "python", "java"]; const lang = allowed.includes(language) ? language : "c"; const data = (window.REFERENCE_DATA || {})[lang];
  if (!data) { app.innerHTML = `<section class="card"><h1>Reference unavailable</h1></section>`; return; }
  app.innerHTML = `<section class="section-head"><div><h1>${esc(data.title)}</h1><p class="muted">Offline syntax reference curated from the official language standard/reference listed below. Search matches substrings across title, code and notes.</p>${data.source ? `<p class="source-note"><strong>Official source:</strong> ${esc(data.source)}</p>` : ""}</div><a class="btn" href="#/problems">Back to problems</a></section><div class="tabs"><a class="tab ${lang === "c" ? "active" : ""}" href="#/reference/c">C</a><a class="tab ${lang === "cpp" ? "active" : ""}" href="#/reference/cpp">C++</a><a class="tab ${lang === "python" ? "active" : ""}" href="#/reference/python">Python</a><a class="tab ${lang === "java" ? "active" : ""}" href="#/reference/java">Java</a></div><section class="card"><div class="field"><label>Search syntax</label><input id="syntax-search" type="search" placeholder="Type any substring, e.g. string, scanf, vector, loop..."></div><p id="syntax-count" class="muted"></p><div id="syntax-results" class="reference-grid"></div></section>`;
  const input = document.getElementById("syntax-search"), results = document.getElementById("syntax-results"), count = document.getElementById("syntax-count");
  const draw = () => { const q = input.value.trim().toLowerCase(); const rows = data.sections.filter(row => !q || row.join("\n").toLowerCase().includes(q)); count.textContent = `${rows.length} reference item${rows.length === 1 ? "" : "s"}`; results.innerHTML = rows.map(row => `<article class="reference-item"><h3>${esc(row[0])}</h3><pre class="code-box">${esc(row[1])}</pre><p class="muted">${esc(row[2])}</p></article>`).join("") || `<p class="muted">No matching syntax found.</p>`; };
  input.oninput = draw; draw();
}

async function renderProblems(practice = false) {
  if (practice && state.examModeActive) {
    app.innerHTML = `<section class="card"><h1>Practice problems</h1><p class="muted">Practice problems are hidden while Exam Mode is active.</p></section>`;
    return;
  }
  const problems = await api(`/api/problems?practice=${practice ? "true" : "false"}`);
  const heading = practice ? "Practice problems" : "Problems";
  const description = practice
    ? "Practice questions are separate from the assessment problem list. You may keep editing your code and use manual testing as usual."
    : "Your code remains editable after every submission.";
  app.innerHTML = `<section class="section-head"><div><h1>${heading}</h1><p class="muted">${description}</p></div>${practice ? `<a class="btn" href="#/problems">Assessment problems</a>` : (!state.examModeActive ? `<a class="btn" href="#/practice">Practice problems</a>` : "")}</section><section class="grid">${problems.length ? problems.map(p => `<article class="card problem-card"><div><div class="actions problem-badges">${p.is_practice ? `<span class="badge">Practice</span>` : ""}${p.accepting_submissions ? `<span class="badge verdict-AC">Submissions open</span>` : `<span class="badge verdict-WA">Submissions closed</span>`}</div><h3>${esc(p.title)}</h3><p class="muted">${p.time_limit_ms} ms · ${p.memory_limit_mb} MB · ${p.test_count} tests · ${p.attempts} language solution${p.attempts === 1 ? "" : "s"}</p><span class="badge">Best ${p.best_score}/${p.max_score}</span></div><a class="btn btn-primary" href="#/problem/${p.id}">Open problem</a></article>`).join("") : `<div class="card empty">No ${practice ? "practice" : "active"} problems.</div>`}</section>`;
}

const templates = { python: `import sys\n\ndef main():\n    data = sys.stdin.read().strip().split()\n    # Write your solution here\n\nif __name__ == "__main__":\n    main()\n`, c: `#include <stdio.h>\n\nint main(void) {\n    /* Write your solution here */\n    return 0;\n}\n`, cpp: `#include <iostream>\nusing namespace std;\n\nint main() {\n    // Write your solution here\n    return 0;\n}\n`, java: `import java.io.*;\nimport java.util.*;\n\npublic class Main {\n    public static void main(String[] args) throws Exception {\n        // Write your solution here\n    }\n}\n` };
async function renderProblem(id) {
  const [p, history] = await Promise.all([api(`/api/problems/${id}`), api(`/api/submissions?problem_id=${id}`)]);
  state.currentProblemId = id;
  let examSession = null; try { examSession = await api(`/api/exam/sessions/start?problem_id=${id}`, { method: "POST" }); state.examSessionId = examSession.session_id; } catch (e) { fail(e); }
  const key = (language) => `judge-draft:${state.user.id}:${id}:${language}`;
  const initialLang = "c";
  const initial = localStorage.getItem(key(initialLang)) || templates[initialLang];
  app.innerHTML = `<section class="section-head"><div><div class="actions problem-badges">${p.is_practice ? `<span class="badge">Practice</span>` : ""}${p.accepting_submissions ? `<span class="badge verdict-AC">Submissions open</span>` : `<span class="badge verdict-WA">Submissions closed</span>`}</div><h1>${esc(p.title)}</h1><p class="muted">${p.time_limit_ms} ms · ${p.memory_limit_mb} MB</p></div><div class="actions">${state.examModeActive ? `<span id="violation-count" class="violation-pill">Violations: ${state.violationCount}</span>` : ""}<a class="btn" href="#/reference/c">Syntax reference</a><a class="btn" href="#/verdicts">Verdict meanings</a><a class="btn" href="#/submissions">All submissions</a></div></section>${state.examModeActive ? `<div id="exam-warning" class="alert alert-danger hidden"></div>` : ""}${!p.accepting_submissions ? `<div class="alert"><strong>Submissions are closed for this problem.</strong> You can still read the question, edit your code, and use <strong>&#9658; Run Code</strong> and <strong>&#9658; Run Samples</strong>. &#128640; Submit is disabled until the administrator reopens submissions.</div>` : ""}<div class="split"><section><article class="card"><h2>Problem statement</h2><p>${esc(p.statement).replaceAll("\n", "<br>")}</p><h3>Input format</h3><p>${esc(p.input_format).replaceAll("\n", "<br>") || "—"}</p><h3>Output format</h3><p>${esc(p.output_format).replaceAll("\n", "<br>") || "—"}</p><h3>Constraints</h3><p>${esc(p.constraints).replaceAll("\n", "<br>") || "—"}</p></article><article class="card"><h2>Public examples</h2>${p.public_tests.map(t => `<div class="test-card"><strong>Test ${t.position}</strong><div class="grid grid-2"><div><p class="muted">Input</p><pre class="code-box">${esc(t.input_data)}</pre></div><div><p class="muted">Expected output</p><pre class="code-box">${esc(t.expected_output)}</pre></div></div></div>`).join("") || `<p class="muted">No public examples.</p>`}</article></section><section class="sticky"><article id="coding-panel" class="card coding-panel"><div class="section-head"><div><h2>Code editor</h2><p class="muted ide-subtitle">Advanced code editor · syntax highlighting · auto-indent · auto brackets · folding · find/replace</p></div><div class="actions"><span id="save-state" class="muted">Autosave on</span><button id="coding-fullscreen" class="btn btn-small" type="button">Full-screen coding</button></div></div><div class="inline-form"><div class="field"><label>Language</label><select id="language"><option value="c">C</option><option value="cpp">C++</option><option value="python">Python</option><option value="java">Java</option></select></div><div class="field"><label>Upload source</label><input id="file" type="file" accept=".py,.c,.cpp,.cc,.cxx,.java"></div></div><div class="ide-toolbar"><div class="actions"><button id="editor-font-down" class="btn btn-small" type="button" title="Decrease editor font">A−</button><button id="editor-font-up" class="btn btn-small" type="button" title="Increase editor font">A+</button><button id="editor-wrap" class="btn btn-small" type="button" title="Scroll keeps a long source line on one visual line. Wrap only displays it on multiple visual rows; it does not insert newlines or change your code.">Long lines: Scroll</button></div><span class="muted">Autocomplete: built-ins + your functions · Ctrl+Space Suggestions · Ctrl+F Find · Ctrl+H Replace · Ctrl+/ Comment</span>${state.examModeActive ? `<span class="badge" title="Copy, cut and paste work only with text copied or cut inside this code editor. Your computer clipboard is not imported into the IDE.">Exam clipboard: IDE-only</span>` : ""}</div><div id="monaco-editor" class="monaco-editor-host"></div><textarea id="editor" class="code-editor hidden" spellcheck="false">${esc(initial)}</textarea><div class="ide-statusbar"><span id="editor-status">Loading editor…</span><span>UTF-8</span></div><div class="manual-test-panel"><div class="tabs"><button id="tab-custom" class="tab active" type="button">Custom Input</button><button id="tab-samples" class="tab" type="button">Public Samples</button></div><div id="panel-custom" class="manual-test-grid"><div class="field"><label>Custom input (stdin)</label><textarea id="manual-input" class="manual-stdin" spellcheck="false" placeholder="Type the input your program should receive…"></textarea></div><div><label>Program output</label><div id="manual-run-result" class="manual-output muted">Run your code to see output here.</div></div></div><div id="panel-samples" class="hidden"><div id="samples-run-result" class="manual-output muted">Run samples to see output here.</div></div></div><div class="actions ide-actions"><button id="run-manual" class="btn">Run Custom Input</button><button id="run-samples" class="btn">Run Public Samples</button><button id="submit" class="btn btn-primary">Submit</button><button id="reset-template" class="btn">Reset template</button><a id="language-reference" class="btn" href="#/reference/c">C syntax</a><a class="btn" href="#/verdicts">AC / WA / CE / …</a></div><p class="muted run-submit-note"><strong>&#9658; Run Code</strong> = custom stdin, not scored. &middot; <strong>&#9658; Run Samples</strong> = runs public examples, not scored. &middot; <strong>&#128640; Submit</strong> = official grading against all hidden test cases &mdash; saves your solution.</p><div id="live-result"></div></article><article class="card"><h2>Saved language solutions</h2><p class="muted">Only one submitted solution is stored for each language. Submitting again in the same language updates and re-judges that solution.</p><div id="history">${history.length ? history.map(s => `<div class="test-card"><div class="section-head"><span><strong>${esc(languageLabel(s.language))}</strong> · ${badge(s.verdict || s.status)}</span><div class="actions"><button class="btn btn-small load-solution" data-id="${s.id}">Load submitted code</button><a class="btn btn-small" href="#/submission/${s.id}">Results</a></div></div><small class="muted">Last submitted ${fmt(s.created_at)} · Score ${s.final_score}</small></div>`).join("") : `<p class="muted">No submitted language solutions yet.</p>`}</div></article></section></div>`;
  const lang = document.getElementById("language"), panel = document.getElementById("coding-panel"), fsButton = document.getElementById("coding-fullscreen"), refLink = document.getElementById("language-reference");
  lang.value = initialLang;
  let currentLang = initialLang, saveTimer = null;
  const saveDraft = (value) => { const stateText = document.getElementById("save-state"); if (stateText) stateText.textContent = "Saving…"; clearTimeout(saveTimer); saveTimer = setTimeout(() => { localStorage.setItem(key(currentLang), value); if (stateText) stateText.textContent = "Saved locally"; }, 350); };
  const codeEditor = await createJudgeEditor({ textareaId: "editor", hostId: "monaco-editor", statusId: "editor-status", language: currentLang, value: initial, onChange: saveDraft });
  state.monitorCleanup.push(() => { try { codeEditor.dispose(); } catch (_e) { } });
  const syncRef = () => { refLink.href = `#/reference/${currentLang}`; refLink.textContent = `${languageLabel(currentLang)} syntax`; }; syncRef();
  const switchLanguage = (next, value = null) => { localStorage.setItem(key(currentLang), codeEditor.getValue()); currentLang = next; lang.value = next; codeEditor.setLanguage(next); codeEditor.setValue(value ?? localStorage.getItem(key(next)) ?? templates[next]); syncRef(); codeEditor.focus(); };
  lang.onchange = () => switchLanguage(lang.value);
  document.getElementById("reset-template").onclick = () => { if (confirm("Replace current draft with the language template?")) codeEditor.setValue(templates[currentLang]); };
  document.getElementById("file").onchange = async e => { const file = e.target.files[0]; if (!file) return; const ext = file.name.split(".").pop().toLowerCase(); const map = { py: "python", c: "c", cpp: "cpp", cc: "cpp", cxx: "cpp", java: "java" }; if (!map[ext]) { fail(new Error("Unsupported file type.")); return; } switchLanguage(map[ext], await file.text()); };
  document.getElementById("editor-font-down").onclick = () => codeEditor.setFontSize(-1);
  document.getElementById("editor-font-up").onclick = () => codeEditor.setFontSize(1);
  document.getElementById("editor-wrap").onclick = e => { const on = codeEditor.toggleWrap(); e.currentTarget.textContent = on ? "Long lines: Wrap" : "Long lines: Scroll"; };
  fsButton.onclick = async () => { try { if (document.fullscreenElement) { state.fullscreenRequested = false; await document.exitFullscreen(); } else { state.fullscreenRequested = true; await panel.requestFullscreen(); codeEditor.focus(); } setTimeout(() => codeEditor.layout(), 60); } catch (e) { fail(e); } };
  const onFs = () => { fsButton.textContent = document.fullscreenElement ? "Exit full screen" : "Full-screen coding"; panel.classList.toggle("is-fullscreen", Boolean(document.fullscreenElement)); setTimeout(() => codeEditor.layout(), 60); }; document.addEventListener("fullscreenchange", onFs); state.monitorCleanup.push(() => document.removeEventListener("fullscreenchange", onFs));
  document.querySelectorAll(".load-solution").forEach(b => b.onclick = async () => { try { const x = await api(`/api/submissions/${b.dataset.id}/source`); switchLanguage(x.language, x.source_code); toast(`Loaded submitted ${languageLabel(x.language)} solution`); } catch (e) { fail(e); } });
  // "\u25b6 Use this input" buttons: load a sample input into the custom stdin field
  document.querySelectorAll(".use-sample-input").forEach(btn => btn.onclick = () => {
    const idx = Number(btn.dataset.index);
    const test = p.public_tests[idx];
    if (!test) return;
    const inp = document.getElementById("manual-input");
    if (inp) { inp.value = test.input_data; inp.scrollIntoView({ behavior: "smooth", block: "nearest" }); inp.focus(); toast(`Sample input ${test.position} loaded \u2014 click \u25b6 Run Code`); }
  });
  // \u25b6 Run Code \u2014 custom stdin only, NEVER creates a submission record
  document.getElementById("run-manual").onclick = async () => {
    const b = document.getElementById("run-manual"), target = document.getElementById("samples-run-result") || document.getElementById("manual-run-result");
    busy(b, true, "Running\u2026"); target.classList.remove("muted"); target.innerHTML = `<div class="run-spinner">\u23f3 Compiling and running\u2026</div>`;
    try {
      localStorage.setItem(key(currentLang), codeEditor.getValue());
      const fd = new FormData(); fd.set("problem_id", String(id)); fd.set("language", currentLang); fd.set("code", codeEditor.getValue()); fd.set("stdin_data", document.getElementById("manual-input").value);
      if (state.examSessionId) fd.set("exam_session_id", state.examSessionId);
      const r = await api("/api/submissions/manual-run", { method: "POST", body: fd });
      target.innerHTML = renderManualRunResult(r);
    } catch (e) { target.innerHTML = `<div class="alert alert-danger">${esc(e.message)}</div>`; fail(e); } finally { busy(b, false); codeEditor.focus(); }
  };
  // \u25b6 Run Samples \u2014 runs all public test cases through manual-run (no official submission created)
  const tabCustom = document.getElementById("tab-custom"), tabSamples = document.getElementById("tab-samples"), panelCustom = document.getElementById("panel-custom"), panelSamples = document.getElementById("panel-samples");
  if (tabCustom) {
    tabCustom.onclick = () => { tabCustom.classList.add("active"); tabSamples.classList.remove("active"); panelCustom.classList.remove("hidden"); panelSamples.classList.add("hidden"); };
    tabSamples.onclick = () => { tabSamples.classList.add("active"); tabCustom.classList.remove("active"); panelSamples.classList.remove("hidden"); panelCustom.classList.add("hidden"); };
  }
  document.getElementById("run-samples").onclick = async () => {
    if (tabSamples) tabSamples.click();
    if (!p.public_tests || !p.public_tests.length) { toast("No public sample test cases for this problem.", true); return; }
    const b = document.getElementById("run-samples"), target = document.getElementById("samples-run-result") || document.getElementById("manual-run-result");
    busy(b, true, `Running ${p.public_tests.length} sample${p.public_tests.length === 1 ? "" : "s"}\u2026`); target.classList.remove("muted");
    target.innerHTML = `<div class="run-spinner">\u23f3 Running against ${p.public_tests.length} public sample case${p.public_tests.length === 1 ? "" : "s"}\u2026</div>`;
    localStorage.setItem(key(currentLang), codeEditor.getValue());
    const results = [];
    for (const test of p.public_tests) {
      try {
        const fd = new FormData(); fd.set("problem_id", String(id)); fd.set("language", currentLang); fd.set("code", codeEditor.getValue()); fd.set("stdin_data", test.input_data);
        if (state.examSessionId) fd.set("exam_session_id", state.examSessionId);
        const r = await api("/api/submissions/manual-run", { method: "POST", body: fd });
        results.push({ test, run: r });
      } catch (e) { results.push({ test, error: e.message }); }
    }
    target.innerHTML = renderSampleRunResults(results);
    busy(b, false); codeEditor.focus();
  };
  // \ud83d\ude80 Submit \u2014 the only official grading path; saves score to DB
  document.getElementById("submit").onclick = async () => {
    if (!p.accepting_submissions) { toast("Submissions are closed for this problem.", true); return; }
    const b = document.getElementById("submit");
    busy(b, true, "Submitting\u2026");
    try {
      localStorage.setItem(key(currentLang), codeEditor.getValue());
      const fd = new FormData(); fd.set("problem_id", String(id)); fd.set("language", currentLang); fd.set("code", codeEditor.getValue());
      if (state.examSessionId) fd.set("exam_session_id", state.examSessionId);
      const r = await api("/api/submissions", { method: "POST", body: fd });
      toast(r.replaced ? `Existing ${languageLabel(currentLang)} solution updated and queued for grading` : `${languageLabel(currentLang)} solution submitted \u2014 waiting for judge`);
      pollSubmission(r.submission_id, document.getElementById("live-result"));
    } catch (e) { fail(e); } finally { busy(b, false); codeEditor.focus(); }
  };
  if (!p.accepting_submissions) {
    const submitBtn = document.getElementById("submit");
    if (submitBtn) { submitBtn.disabled = true; submitBtn.title = "The administrator has closed submissions for this problem."; }
  }
  if (state.examSessionId) state.heartbeat = setInterval(() => api(`/api/exam/sessions/${state.examSessionId}/heartbeat`, { method: "POST" }).catch(() => { }), 30000);
}

/** Render the result of a single manual-run (custom stdin or sample stdin). */
function renderManualRunResult(r) {
  const v = String(r.verdict || "SE").toUpperCase();
  const ok = v === "OK";
  const tle = v === "TLE", ce = v === "CE", re = v === "RE";
  const statusBadge = ok ? `<span class="badge verdict-OK">✔ Run completed</span>` : badge(v);
  const meta = `<small class="muted">${Number(r.runtime_ms || 0)} ms · ${Number(r.memory_kb || 0)} KB · <em>not submitted</em></small>`;
  let body = "";
  if (ce) {
    body += `<div class="result-section"><div class="result-section-head ce">⛔ Compilation Error</div><pre class="code-box compiler-output">${esc(r.compile_message || "(no compiler output)")}</pre></div>`;
  } else if (r.compile_message) {
    body += `<details class="result-section"><summary class="result-section-head warning">⚠ Compiler warnings (code compiled successfully)</summary><pre class="code-box compiler-output">${esc(r.compile_message)}</pre></details>`;
  }
  if (!ce) {
    if (tle) body += `<div class="alert alert-danger result-tle">⏱ Time Limit Exceeded — your program ran longer than allowed.</div>`;
    if (re) body += `<div class="alert alert-danger result-re">💥 Runtime Error — your program crashed or exited with a non-zero code.</div>`;
    body += `<div class="result-section"><div class="result-section-head">stdout</div><pre class="code-box manual-result-box">${esc(r.stdout || (ok ? "(empty output)" : "(no output — program did not print anything)"))}</pre></div>`;
    if (r.stderr) body += `<div class="result-section"><div class="result-section-head warning">stderr</div><pre class="code-box manual-result-box stderr-box">${esc(r.stderr)}</pre></div>`;
  }
  return `<div class="manual-run-card"><div class="run-result-header">${statusBadge}${meta}</div>${body}</div>`;
}

/**
 * Render the results of running ALL public sample tests through manual-run.
 * Shows a clean diff for each case so students can see what went wrong.
 */
function renderSampleRunResults(results) {
  if (!results.length) return `<p class="muted">No sample results.</p>`;
  const allOk = results.every(({ run, error }) => !error && String(run?.verdict || "SE").toUpperCase() === "OK");
  const summary = allOk
    ? `<div class="sample-summary pass">✅ All ${results.length} sample case${results.length === 1 ? "" : "s"} passed</div>`
    : `<div class="sample-summary fail">Results for ${results.length} sample case${results.length === 1 ? "" : "s"}</div>`;
  const cards = results.map(({ test, run, error }, i) => {
    if (error) return `<div class="sample-case-card error"><div class="sample-case-head"><strong>Test ${test.position}</strong><span class="badge verdict-SE">Error</span></div><div class="alert alert-danger">${esc(error)}</div></div>`;
    const v = String(run.verdict || "SE").toUpperCase();
    const ok = v === "OK";
    const ce = v === "CE";
    // Determine pass/fail by comparing actual vs expected output (client-side normalization)
    const normalizeClient = (s) => (s || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n").map(l => l.trimEnd()).filter((l, i, a) => !(i === a.length - 1 && l === "")).join("\n");
    const actualNorm = normalizeClient(run.stdout || "")
    const expectedNorm = normalizeClient(test.expected_output || "")
    const matchesExpected = ok && (actualNorm === expectedNorm);
    const passBadge = ce ? badge("CE") : matchesExpected ? `<span class="badge verdict-AC">✔ Correct</span>` : `<span class="badge verdict-WA">✘ Wrong Answer</span>`;
    let body = "";
    if (ce) {
      body = `<pre class="code-box compiler-output">${esc(run.compile_message || "(no compiler output)")}</pre>`;
    } else {
      let diffViewerHtml = "";
      if (!matchesExpected && window.Diff) {
        const diff = Diff.diffWords(expectedNorm, actualNorm);
        let diffHtml = "";
        diff.forEach(part => {
          const bg = part.added ? 'rgba(239, 68, 68, 0.3)' : part.removed ? 'rgba(34, 197, 94, 0.3)' : 'transparent';
          const textDeco = part.removed ? 'text-decoration: line-through;' : '';
          diffHtml += `<span style="background-color: ${bg}; ${textDeco}">${esc(part.value)}</span>`;
        });
        diffViewerHtml = `<div class="diff-col-full"><div class="diff-label">Visual Diff (Green = Missing, Red = Extra)</div><pre class="code-box diff-box">${diffHtml}</pre></div>`;
      }

      body = `<div class="sample-diff-grid">
        <div><div class="diff-label">Input</div><pre class="code-box diff-box">${esc(test.input_data || "(empty)")}</pre></div>
        <div><div class="diff-label">Expected output</div><pre class="code-box diff-box diff-expected">${esc(test.expected_output || "(empty)")}</pre></div>
        <div><div class="diff-label">Your output${run.runtime_ms ? ` <small class="muted">${run.runtime_ms} ms</small>` : ""}</div><pre class="code-box diff-box ${matchesExpected ? "diff-match" : "diff-mismatch"}">${esc(run.stdout || (ok ? "(empty output)" : "(no output)"))}</pre></div>
        ${diffViewerHtml}
        ${run.stderr ? `<div class="diff-col-full"><div class="diff-label warning">stderr</div><pre class="code-box stderr-box">${esc(run.stderr)}</pre></div>` : ""}
      </div>`;
    }
    return `<div class="sample-case-card ${matchesExpected && !ce ? "pass" : (ce ? "error" : "fail")}"><div class="sample-case-head"><strong>Test ${test.position}</strong>${passBadge}</div>${body}</div>`;
  }).join("");
  return `<div class="sample-run-container">${summary}${cards}</div>`;
}

/** Poll a submitted job and update the live-result panel with status animations. */
function pollSubmission(id, target) {
  let dotCount = 0;
  const spinDots = () => { ".".repeat(dotCount = (dotCount % 3) + 1) };
  const showPending = (status) => {
    const dots = ".".repeat(dotCount = (dotCount % 3) + 1);
    const label = VERDICT_INFO[status] ? VERDICT_INFO[status][0] : status;
    target.innerHTML = `<div class="poll-status-card"><div class="poll-spinner"></div><strong>${esc(label)}${dots}</strong><p class="muted">Waiting for judge worker — this usually takes a few seconds.</p><a class="btn btn-small" href="#/submission/${id}">View submission page</a></div>`;
  };
  const run = async () => {
    try {
      const s = await api(`/api/submissions/${id}`);
      if (!FINAL.has(s.status)) {
        showPending(s.status);
        state.polling = setTimeout(run, 1400);
      } else {
        target.innerHTML = renderSubmissionResult(s, true);
        const v = s.verdict || s.status;
        toast(v === "AC" ? `✔ Accepted — ${s.final_score} pts` : `Judging complete: ${v}`, v !== "AC");
      }
    } catch (e) { target.innerHTML = `<div class="alert alert-danger">${esc(e.message)}</div>`; }
  };
  showPending("QUEUED");
  run();
}

/** Render a submission result card with diff-viewer for public test results. */
function renderSubmissionResult(s, compact = false) {
  const isInFlight = !FINAL.has(s.status);
  const score = `${s.final_score ?? "—"}/${s.total_points ?? "—"}`;
  const ceBlock = s.compile_message ? `<div class="result-section"><div class="result-section-head ${s.verdict === "CE" ? "ce" : "warning"}">${s.verdict === "CE" ? "⛔ Compilation Error" : "⚠ Compiler warnings"}</div><pre class="code-box compiler-output">${esc(s.compile_message)}</pre></div>` : "";
  const publicTests = (s.public_results || []).map(r => {
    const hasOutput = r.actual_output != null && r.expected_output != null;
    let diffViewerHtml = "";
    if (hasOutput && r.verdict !== "AC" && window.Diff) {
      const normalizeClient = (s) => (s || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n").map(l => l.trimEnd()).filter((l, i, a) => !(i === a.length - 1 && l === "")).join("\n");
      const diff = Diff.diffWords(normalizeClient(r.expected_output), normalizeClient(r.actual_output));
      let diffHtml = "";
      diff.forEach(part => {
        const bg = part.added ? 'rgba(239, 68, 68, 0.3)' : part.removed ? 'rgba(34, 197, 94, 0.3)' : 'transparent';
        const textDeco = part.removed ? 'text-decoration: line-through;' : '';
        diffHtml += `<span style="background-color: ${bg}; ${textDeco}">${esc(part.value)}</span>`;
      });
      diffViewerHtml = `<div class="diff-col-full"><div class="diff-label">Visual Diff (Green = Missing, Red = Extra)</div><pre class="code-box diff-box">${diffHtml}</pre></div>`;
    }
    
    return `<div class="sample-case-card ${r.verdict === "AC" ? "pass" : "fail"}"><div class="sample-case-head"><strong>Test ${r.position}</strong>${badge(r.verdict)}<small class="muted">${r.runtime_ms} ms · ${r.memory_kb} KB</small></div>${hasOutput ? `<div class="sample-diff-grid"><div><div class="diff-label">Expected output</div><pre class="code-box diff-box diff-expected">${esc(r.expected_output)}</pre></div><div><div class="diff-label">Your output</div><pre class="code-box diff-box ${r.verdict === "AC" ? "diff-match" : "diff-mismatch"}">${esc(r.actual_output)}</pre></div>${diffViewerHtml}${r.stderr ? `<div class="diff-col-full"><div class="diff-label warning">stderr</div><pre class="code-box stderr-box">${esc(r.stderr)}</pre></div>` : ""}</div>` : ""}</div>`;
  }).join("");
  const privateBlock = `<div class="private-summary"><strong>Private tests:</strong> ${s.private_passed || 0}/${s.private_total || 0} passed.</div>`;
  return `<div class="submission-result-card"><div class="submission-result-header"><strong class="sr-lang">${esc(languageLabel(s.language))}</strong>${badge(s.verdict || s.status)}<span class="sr-score">${score} pts</span></div>${ceBlock}${isInFlight ? "" : `<h4 class="sr-section-title">Public test cases</h4>${publicTests || `<p class="muted">No public test results available yet.</p>`}${privateBlock}`}${compact ? `<a class="btn btn-small sr-detail-link" href="#/submission/${s.id}">Open full report →</a>` : ""}</div>`;
}

function examIdeClipboardTarget(event) {
  const path = typeof event.composedPath === "function" ? event.composedPath() : [];
  let wrapper = path.find(node => node?.classList?.contains?.("CodeMirror"));
  if (!wrapper && event.target?.closest) wrapper = event.target.closest(".CodeMirror");
  if (!wrapper && document.activeElement?.closest) wrapper = document.activeElement.closest(".CodeMirror");
  if (wrapper?.__judgeCodeMirror) return { kind: "codemirror", editor: wrapper.__judgeCodeMirror };

  let textarea = path.find(node => node?.tagName === "TEXTAREA" && node.classList?.contains?.("code-editor") && !node.classList?.contains?.("hidden"));
  if (!textarea && event.target?.matches?.("textarea.code-editor:not(.hidden)")) textarea = event.target;
  if (!textarea && document.activeElement?.matches?.("textarea.code-editor:not(.hidden)")) textarea = document.activeElement;
  return textarea ? { kind: "textarea", editor: textarea } : null;
}
function examIdeSelectedText(target) {
  if (target.kind === "codemirror") return target.editor.getSelection();
  const el = target.editor; return el.value.slice(el.selectionStart, el.selectionEnd);
}
function examIdeDeleteSelection(target) {
  if (target.kind === "codemirror") { target.editor.replaceSelection("", "end", "+cut"); target.editor.focus(); return; }
  const el = target.editor, start = el.selectionStart, end = el.selectionEnd;
  el.setRangeText("", start, end, "end"); el.dispatchEvent(new Event("input", { bubbles: true })); el.focus();
}
function examIdeInsertClipboard(target, text) {
  if (target.kind === "codemirror") { target.editor.replaceSelection(text, "end", "paste"); target.editor.focus(); return; }
  const el = target.editor, start = el.selectionStart, end = el.selectionEnd;
  el.setRangeText(text, start, end, "end"); el.dispatchEvent(new Event("input", { bubbles: true })); el.focus();
}

function stopMonitoring() { state.monitorCleanup.forEach(fn => fn()); state.monitorCleanup = []; state.examSessionId = null; state.fullscreenRequested = false; state.examIdeClipboard = ""; }

function startMonitoring(problemId) {
  if (!state.user || state.user.role === "admin" || !state.examModeActive) return; const sent = new Map();
  const flag = async (eventType, details = "") => { const now = Date.now(); if (now - (sent.get(eventType) || 0) < 1500) return; sent.set(eventType, now); try { const r = await api("/api/exam/flags", { method: "POST", body: { session_id: state.examSessionId, problem_id: problemId, event_type: eventType, details, client_timestamp: new Date().toISOString() } }); if (r.status === "recorded") { updateViolationDisplays(r.violation_count, eventType); toast(`Warning: ${eventType.replaceAll("_", " ")} recorded. Violations: ${r.violation_count}`, true); } } catch (_e) { } };
  const onVisibility = () => { if (document.hidden) flag("TAB_SWITCH") }; document.addEventListener("visibilitychange", onVisibility); state.monitorCleanup.push(() => document.removeEventListener("visibilitychange", onVisibility));
  const onBlur = () => flag("WINDOW_BLUR"); window.addEventListener("blur", onBlur); state.monitorCleanup.push(() => window.removeEventListener("blur", onBlur));
  const onFull = () => { if (state.fullscreenRequested && !document.fullscreenElement) flag("FULLSCREEN_EXIT") }; document.addEventListener("fullscreenchange", onFull); state.monitorCleanup.push(() => document.removeEventListener("fullscreenchange", onFull));
  state.examIdeClipboard = "";
  const examClipboardHandler = (name, type) => {
    const fn = e => {
      if (name === "contextmenu") { e.preventDefault(); flag(type); return; }
      const target = examIdeClipboardTarget(e);
      if (!target) { e.preventDefault(); flag(type); return; }

      // Never read from or write to the operating-system/browser clipboard in
      // Exam Mode. Ctrl+C/X/V is implemented against this page-memory buffer.
      e.preventDefault();
      e.stopPropagation();

      if (name === "copy" || name === "cut") {
        const selected = examIdeSelectedText(target);
        if (!selected) { toast("Select code first to copy or cut.", true); return; }
        state.examIdeClipboard = selected;
        if (name === "cut") examIdeDeleteSelection(target);
        return;
      }

      if (name === "paste") {
        if (!state.examIdeClipboard) {
          flag("EXTERNAL_PASTE_BLOCKED", "Paste was blocked because no text had been copied or cut inside the Exam Mode code editor.");
          toast("External clipboard paste is blocked in Exam Mode. Copy or cut code inside the IDE first.", true);
          return;
        }
        examIdeInsertClipboard(target, state.examIdeClipboard);
      }
    };
    document.addEventListener(name, fn, true);
    state.monitorCleanup.push(() => document.removeEventListener(name, fn, true));
  };
  [["copy", "COPY_ATTEMPT"], ["cut", "CUT_ATTEMPT"], ["paste", "PASTE_ATTEMPT"], ["contextmenu", "RIGHT_CLICK"]].forEach(([name, type]) => examClipboardHandler(name, type));

  // Prevent drag/drop from becoming a clipboard bypass into the code editor.
  const drop = e => { const target = examIdeClipboardTarget(e); if (!target) return; e.preventDefault(); flag("EXTERNAL_DROP_BLOCKED", "External drag/drop into the Exam Mode code editor was blocked."); toast("External drag/drop into the code editor is blocked in Exam Mode.", true); };
  document.addEventListener("drop", drop, true); state.monitorCleanup.push(() => document.removeEventListener("drop", drop, true));
  const dragover = e => { if (examIdeClipboardTarget(e)) e.preventDefault(); }; document.addEventListener("dragover", dragover, true); state.monitorCleanup.push(() => document.removeEventListener("dragover", dragover, true));
  const key = e => { const k = e.key.toLowerCase(); let type = ""; if (e.key === "F12") type = "F12_ATTEMPT"; else if (e.ctrlKey && e.shiftKey && ["i", "j", "c"].includes(k)) type = "DEVTOOLS_SHORTCUT"; else if (e.ctrlKey && k === "u") type = "VIEW_SOURCE_ATTEMPT"; else if (e.ctrlKey && k === "p") type = "PRINT_ATTEMPT"; if (type) { e.preventDefault(); flag(type); } }; document.addEventListener("keydown", key); state.monitorCleanup.push(() => document.removeEventListener("keydown", key));
}

async function renderSubmissions() {
  const items = await api("/api/submissions");
  app.innerHTML = `<section class="section-head"><div><h1>My submissions</h1><p class="muted">One solution is stored per problem per language. Resubmitting the same language replaces and re-judges that solution.</p></div></section><section class="card"><div class="table-wrap"><table><thead><tr><th>Problem</th><th>Language</th><th>Verdict</th><th>Score</th><th>Execution Time</th><th>Last submitted</th><th></th></tr></thead><tbody>${items.map(s => `<tr><td><a href="#/problem/${s.problem_id}">${esc(s.problem_title)}</a></td><td>${esc(languageLabel(s.language))}</td><td>${badge(s.verdict || s.status)}</td><td>${s.final_score}</td><td>${s.execution_time_ms != null ? s.execution_time_ms + " ms" : "—"}</td><td>${fmt(s.created_at)}</td><td><a class="btn btn-small" href="#/submission/${s.id}">View</a></td></tr>`).join("") || `<tr><td colspan="7" class="empty">No submissions yet. Open a problem to start coding.</td></tr>`}</tbody></table></div></section>`;
}
async function renderSubmission(id) {
  const s = await api(`/api/submissions/${id}`); const languageName = languageLabel(s.language); app.innerHTML = `<section class="section-head"><div><h1>${esc(s.problem.title)} — ${esc(languageName)}</h1><p class="muted">Last submitted ${fmt(s.created_at)}</p></div><div class="actions"><a class="btn" href="#/problem/${s.problem.id}">Continue editing</a><button id="load-source" class="btn">View source</button></div></section><section class="card">${renderSubmissionResult(s, false)}</section>${state.user.role === "admin" ? `<section class="card"><h2>Administrator grading</h2><form id="grade-form" class="inline-form"><div class="field"><label>Manual score (blank keeps automatic)</label><input name="manual_score" type="number" step="0.01" min="0" value="${s.manual_score ?? ""}"></div><div class="field"><label>Remarks</label><input name="notes" value="${esc(s.manual_notes || "")}"></div><button class="btn btn-primary">Save grade</button><button id="official" class="btn" type="button">Select official solution</button></form>${s.results ? `<h2>All test details</h2>${s.results.map(r => `<div class="test-card"><strong>${r.visibility} Test ${r.position}: ${badge(r.verdict)}</strong><div class="grid grid-2"><pre class="code-box">Expected:
${esc(r.expected_output)}</pre><pre class="code-box">Actual:
${esc(r.actual_output)}</pre></div><pre class="code-box">${esc(r.stderr || "")}</pre></div>`).join("")}` : ""}</section>` : ""}`;
  document.getElementById("load-source").onclick = async () => { const x = await api(`/api/submissions/${id}/source`); openModal(`<h2>${esc(languageName)} source code</h2><pre class="code-box">${esc(x.source_code)}</pre>`); };
  if (state.user.role === "admin") { document.getElementById("grade-form").onsubmit = async e => { e.preventDefault(); const f = e.currentTarget; const score = f.manual_score.value === "" ? null : Number(f.manual_score.value); try { const r = await api(`/api/admin/submissions/${id}/grade`, { method: "POST", body: { manual_score: score, notes: f.notes.value } }); toast(`Final score: ${r.final_score}`); route(); } catch (x) { fail(x); } }; document.getElementById("official").onclick = async () => { try { await api(`/api/admin/submissions/${id}/official`, { method: "POST" }); toast("Official language solution selected"); route(); } catch (x) { fail(x); } }; }
}

async function renderResults() { try { const r = await api("/api/reports/my"); app.innerHTML = `<section class="section-head"><div><h1>Published result</h1><p class="muted">${esc(r.summary.exam_name)}</p></div><a class="btn btn-primary" href="/api/reports/my.pdf">Download PDF</a></section><section class="card"><div class="identity-block"><strong>${esc(r.student.full_name)}</strong><small>${esc(r.student.email || "")}</small><small>${esc(r.student.enrollment_number)}</small></div><div class="grid grid-4"><div class="stat"><small>Total</small><strong>${r.summary.total}/${r.summary.maximum}</strong></div><div class="stat"><small>Percentage</small><strong>${r.summary.percentage.toFixed(2)}%</strong></div><div class="stat"><small>Attempted</small><strong>${r.summary.attempted}</strong></div><div class="stat"><small>Result</small><strong>${r.summary.passed ? "PASS" : "FAIL"}</strong></div></div><div class="table-wrap"><table><thead><tr><th>Problem</th><th>Language</th><th>Verdict</th><th>Automatic</th><th>Manual</th><th>Final</th><th>Maximum</th></tr></thead><tbody>${r.rows.map(x => `<tr><td>${esc(x.problem)}</td><td>${esc(x.language === "cpp" ? "C++" : x.language === "python" ? "Python" : x.language === "c" ? "C" : "—")}</td><td>${badge(x.verdict)}</td><td>${x.automatic_score}</td><td>${x.manual_score ?? "—"}</td><td>${x.final_score}</td><td>${x.maximum}</td></tr>`).join("")}</tbody></table></div></section>`; } catch (e) { if (e.status === 403) app.innerHTML = `<section class="card"><h1>Results</h1><p class="muted">Final results have not been published yet.</p></section>`; else throw e; } }

async function renderAdmin(tab) {
  state.adminTab = tab;
  const exam = await api("/api/admin/exam-settings");
  const tabs = ["dashboard", "exam", "users", "admins", "registration", "recovery", "problems", "submissions", "flags", "ip-access", "results"];
  const label = x => x === "ip-access" ? "IP Access" : x === "registration" ? "Registration" : x[0].toUpperCase() + x.slice(1);
  app.innerHTML = `<section class="section-head"><div><h1>Administrator portal</h1><p class="muted">Full student, submission, grading and monitoring access. Passwords are never displayed; administrators reset them securely.</p></div>${exam.enabled ? `<div class="exam-mode-admin-alert">● EXAM MODE ON</div>` : `<span class="badge">Exam Mode OFF</span>`}</section>${exam.enabled ? `<div class="alert alert-danger admin-exam-banner"><strong>EXAM MODE IS ON</strong> — student exam access and monitoring are active.</div>` : ""}<div class="tabs">${tabs.map(x => `<a class="tab ${x === tab ? "active" : ""} ${x === "exam" && exam.enabled ? "tab-exam-on" : ""}" href="#/admin/${x}">${label(x)}${x === "exam" && exam.enabled ? " ●" : ""}</a>`).join("")}</div><section id="admin-content"></section>`;
  const c = document.getElementById("admin-content");
  if (tab === "dashboard") await adminDashboard(c);
  else if (tab === "exam") await adminExam(c);
  else if (tab === "users") await adminUsers(c);
  else if (tab === "admins") await adminAdmins(c);
  else if (tab === "registration") await adminRegistration(c);
  else if (tab === "recovery") await adminRecovery(c);
  else if (tab === "problems") await adminProblems(c);
  else if (tab === "submissions") await adminSubmissions(c);
  else if (tab === "flags") await adminFlags(c);
  else if (tab === "ip-access") await adminIpAccess(c);
  else await adminResults(c);
}

async function adminDashboard(c) { const d = await api("/api/admin/dashboard"); c.innerHTML = `<div class="grid grid-4">${Object.entries(d).map(([k, v]) => `<div class="stat"><small>${esc(k.replaceAll("_", " "))}</small><strong>${v}</strong></div>`).join("")}</div>`; }
async function adminExam(c) {
  const s = await api("/api/admin/exam-settings"); c.innerHTML = `<div class="grid grid-2"><article class="card"><h2>Exam settings</h2><form id="exam-settings" class="form-grid"><div class="field"><label>Exam name</label><input name="exam_name" value="${esc(s.exam_name)}"></div><div class="field"><label><input name="enabled" type="checkbox" ${s.enabled ? "checked" : ""}> Exam Mode enabled</label></div><div class="field"><label><input name="results_published" type="checkbox" ${s.results_published ? "checked" : ""}> Publish results</label></div><div class="field"><label>Official submission policy</label><select name="official_submission_policy"><option ${s.official_submission_policy === "BEST" ? "selected" : ""}>BEST</option><option ${s.official_submission_policy === "LATEST" ? "selected" : ""}>LATEST</option><option ${s.official_submission_policy === "ADMIN_SELECTED" ? "selected" : ""}>ADMIN_SELECTED</option></select></div><div class="field"><label>Passing percentage</label><input name="passing_percentage" type="number" min="0" max="100" value="${s.passing_percentage}"></div><button class="btn btn-primary">Save settings</button></form></article><article class="card"><h2>Common one-time exam code</h2><p>Generation: ${esc(s.generation_id || "Not generated")}</p><p>Expires: ${fmt(s.expires_at)}</p><form id="code-generate" class="inline-form"><div class="field"><label>Valid minutes</label><input name="valid_minutes" type="number" min="5" value="180"></div><button class="btn btn-primary">Generate / regenerate</button></form><hr><button id="force-all" class="btn btn-danger">Force re-login all students</button><p class="muted">A student who logs out or is force-logged-out cannot reuse the current code. Regenerate the common code when re-entry is required.</p></article></div><article class="card"><div class="section-head"><h2>Code activity</h2><button id="activity" class="btn">Load activity</button></div><div id="activity-data"></div></article>`;
  document.getElementById("exam-settings").onsubmit = async e => { e.preventDefault(); const f = e.currentTarget; try { await api("/api/admin/exam-settings", { method: "PATCH", body: { exam_name: f.exam_name.value, enabled: f.enabled.checked, results_published: f.results_published.checked, official_submission_policy: f.official_submission_policy.value, passing_percentage: Number(f.passing_percentage.value) } }); toast("Exam settings saved"); route(); } catch (x) { fail(x); } };
  document.getElementById("code-generate").onsubmit = async e => { e.preventDefault(); try { const r = await api("/api/admin/exam-code/generate", { method: "POST", body: { valid_minutes: Number(e.currentTarget.valid_minutes.value) } }); openModal(`<h2>Announce this code in the lab</h2><div class="code-box" style="font-size:38px;text-align:center">${esc(r.code)}</div><p>${esc(r.message)}</p>`); route(); } catch (x) { fail(x); } };
  document.getElementById("force-all").onclick = async () => { if (!confirm("Log out every active student?")) return; try { const r = await api("/api/admin/force-relogin-all", { method: "POST", body: { remarks: "Forced from admin portal" } }); toast(`${r.revoked_sessions} sessions revoked`); } catch (x) { fail(x); } };
  document.getElementById("activity").onclick = async () => { try { const a = await api("/api/admin/exam-code/activity"); document.getElementById("activity-data").innerHTML = `<h3>Successful uses</h3><div class="table-wrap"><table><thead><tr><th>Student</th><th>Enrollment</th><th>IP</th><th>Time</th></tr></thead><tbody>${a.uses.map(x => `<tr><td>${esc(x.student)}</td><td>${esc(x.enrollment_number)}</td><td>${esc(x.client_ip)}</td><td>${fmt(x.used_at)}</td></tr>`).join("")}</tbody></table></div><h3>Attempts</h3><div class="table-wrap"><table><thead><tr><th>Student</th><th>Event</th><th>IP</th><th>Time</th></tr></thead><tbody>${a.attempts.map(x => `<tr><td>${esc(x.student)}</td><td>${esc(x.event_type)}</td><td>${esc(x.client_ip)}</td><td>${fmt(x.created_at)}</td></tr>`).join("")}</tbody></table></div>`; } catch (x) { fail(x); } };
}
async function adminUsers(c) {
  const users = await api("/api/admin/users?role=student");
  c.innerHTML = `<article class="card"><div class="section-head"><div><h2>Student users</h2><p class="muted">Student accounts are enabled by default. Disabled students cannot log in and any active session is revoked.</p></div><button id="create-user" class="btn btn-primary">Create student</button></div><div class="table-wrap"><table><thead><tr><th>Student</th><th>Enrollment</th><th>Status</th><th>Enabled</th><th>Online</th><th>Submissions</th><th>Flags</th><th>Actions</th></tr></thead><tbody>${users.map(u => `<tr><td>${identityHtml(u.full_name, u.email)}</td><td>${esc(u.enrollment_number)}</td><td>${badge(u.status)}</td><td>${u.active ? `<span class="badge verdict-AC">Enabled</span>` : `<span class="badge verdict-WA">Disabled</span>`}</td><td>${u.online ? "Yes" : "No"}<br><small>${esc(u.current_ip)}</small></td><td>${u.submission_count}</td><td>${u.flag_count}</td><td><div class="actions">${u.status === "PENDING" ? `<button class="btn btn-small approve" data-id="${u.id}">Approve</button><button class="btn btn-small btn-danger reject" data-id="${u.id}">Reject</button>` : ""}<button class="btn btn-small detail" data-id="${u.id}">Details</button><button class="btn btn-small ${u.active ? "btn-danger" : ""} user-active-toggle" data-id="${u.id}" data-active="${u.active}">${u.active ? "Disable" : "Enable"}</button><button class="btn btn-small relogin" data-id="${u.id}">Force re-login</button></div></td></tr>`).join("") || `<tr><td colspan="8">No students found.</td></tr>`}</tbody></table></div></article>`;
  c.querySelectorAll(".approve").forEach(b => b.onclick = () => userDecision(b.dataset.id, "approve"));
  c.querySelectorAll(".reject").forEach(b => b.onclick = () => userDecision(b.dataset.id, "reject"));
  c.querySelectorAll(".detail").forEach(b => b.onclick = () => showUser(b.dataset.id));
  c.querySelectorAll(".relogin").forEach(b => b.onclick = async () => { try { await api(`/api/admin/users/${b.dataset.id}/force-relogin`, { method: "POST", body: { remarks: "Forced from student list" } }); toast("Student session revoked"); } catch (x) { fail(x); } });
  c.querySelectorAll(".user-active-toggle").forEach(b => b.onclick = async () => {
    const active = b.dataset.active === "true"; const action = active ? "disable" : "enable";
    if (active && !confirm("Disable this student? Any active login will be revoked.")) return;
    try { await api(`/api/admin/users/${b.dataset.id}/${action}`, { method: "POST", body: { remarks: `${action}d from admin user list` } }); toast(`Student ${action}d`); route(); } catch (x) { fail(x); }
  });
  document.getElementById("create-user").onclick = () => openModal(`<h2>Create student</h2><form id="create-user-form" class="form-grid"><div class="field"><label>Full name</label><input name="full_name" required></div><div class="field"><label>Email</label><input name="email" type="email" required></div><div class="field"><label>Enrollment</label><input name="enrollment_number" required></div><div class="field"><label>Password</label><input name="password" type="password" required></div><button class="btn btn-primary">Create student</button></form>`);
  setTimeout(() => { document.getElementById("create-user-form").onsubmit = async e => { e.preventDefault(); const f = e.currentTarget; try { await api("/api/admin/users", { method: "POST", body: { ...Object.fromEntries(new FormData(f)), role: "student", approved: true } }); modal.close(); toast("Student created and enabled"); route(); } catch (x) { fail(x); } }; }, 0);
}

async function adminAdmins(c) {
  const admins = await api("/api/admin/admins");
  c.innerHTML = `<article class="card"><div class="section-head"><div><h2>Administrator accounts</h2><p class="muted">Create and manage administrators separately from student users. Passwords are never displayed.</p></div><button id="create-admin" class="btn btn-primary">Add new admin</button></div><div class="table-wrap"><table><thead><tr><th>Administrator</th><th>Username</th><th>Enabled</th><th>Last login</th><th>Current IP</th><th>Actions</th></tr></thead><tbody>${admins.map(a => `<tr><td>${identityHtml(a.full_name, a.email)}</td><td><code>${esc(a.username)}</code></td><td>${a.active ? `<span class="badge verdict-AC">Enabled</span>` : `<span class="badge verdict-WA">Disabled</span>`}</td><td>${fmt(a.last_login_at)}</td><td>${esc(a.current_ip) || "—"}</td><td><div class="actions">${a.id === state.user.id ? `<span class="muted">Current admin</span>` : `<button class="btn btn-small ${a.active ? "btn-danger" : ""} admin-active-toggle" data-id="${a.id}" data-active="${a.active}">${a.active ? "Disable" : "Enable"}</button><button class="btn btn-small admin-reset" data-id="${a.id}" data-name="${esc(a.full_name)}">Reset password</button>`}</div></td></tr>`).join("") || `<tr><td colspan="6">No administrator accounts.</td></tr>`}</tbody></table></div></article>`;
  document.getElementById("create-admin").onclick = () => openModal(`<h2>Add administrator</h2><form id="create-admin-form" class="form-grid"><div class="field"><label>Username</label><input name="username" pattern="[A-Za-z0-9._-]+" required></div><div class="field"><label>Full name</label><input name="full_name" required></div><div class="field"><label>Email</label><input name="email" type="email" required></div>${passwordField("password", "Password", "new-admin-password")}${passwordField("confirm_password", "Confirm password", "new-admin-confirm")}<button class="btn btn-primary">Create administrator</button></form>`);
  setTimeout(() => wirePasswordToggles(modalContent), 0);
  setTimeout(() => { const form = document.getElementById("create-admin-form"); if (form) form.onsubmit = async e => { e.preventDefault(); const f = e.currentTarget; if (f.password.value !== f.confirm_password.value) { fail(new Error("Passwords do not match.")); return; } try { await api("/api/admin/admins", { method: "POST", body: Object.fromEntries(new FormData(f)) }); modal.close(); toast("Administrator created"); route(); } catch (x) { fail(x); } }; }, 0);
  c.querySelectorAll(".admin-active-toggle").forEach(b => b.onclick = async () => { const active = b.dataset.active === "true"; const action = active ? "disable" : "enable"; if (active && !confirm("Disable this administrator? Any active login will be revoked.")) return; try { await api(`/api/admin/users/${b.dataset.id}/${action}`, { method: "POST", body: { remarks: `Administrator ${action}d from Admins section` } }); toast(`Administrator ${action}d`); route(); } catch (x) { fail(x); } });
  c.querySelectorAll(".admin-reset").forEach(b => b.onclick = () => { openModal(`<h2>Reset password — ${esc(b.dataset.name)}</h2><form id="admin-reset-form" class="form-grid">${passwordField("new_password", "New password", "admin-reset-password")}${passwordField("confirm_password", "Confirm password", "admin-reset-confirm")}<button class="btn btn-primary">Reset password</button></form><p class="muted">All active sessions for this administrator will be revoked.</p>`); wirePasswordToggles(modalContent); setTimeout(() => { const f = document.getElementById("admin-reset-form"); if (f) f.onsubmit = async e => { e.preventDefault(); if (f.new_password.value !== f.confirm_password.value) { fail(new Error("Passwords do not match.")); return; } try { await api(`/api/admin/admins/${b.dataset.id}/reset-password`, { method: "POST", body: { new_password: f.new_password.value, confirm_password: f.confirm_password.value } }); modal.close(); toast("Administrator password reset"); } catch (x) { fail(x); } }; }, 0); });
}

async function userDecision(id, action) { try { await api(`/api/admin/users/${id}/${action}`, { method: "POST", body: { remarks: "Admin portal decision" } }); toast(`User ${action}d`); route(); } catch (x) { fail(x); } }
async function showUser(id) { try { const d = await api(`/api/admin/users/${id}`); openModal(`<h2>${esc(d.user.full_name)}</h2>${identityHtml(d.user.full_name, d.user.email, d.user.enrollment_number)}<div class="grid grid-3"><div class="stat"><small>Enrollment</small><strong style="font-size:18px">${esc(d.user.enrollment_number)}</strong></div><div class="stat"><small>Language solutions</small><strong>${d.user.submission_count}</strong></div><div class="stat"><small>Flags</small><strong>${d.user.flag_count}</strong></div></div><h3>Submitted solutions</h3>${d.submissions.map(s => `<div class="test-card"><a href="#/submission/${s.id}">${esc(s.problem_title)} — ${esc(languageLabel(s.language))}</a> · ${badge(s.verdict || s.status)} · final ${s.final_score}</div>`).join("") || "<p>No submissions.</p>"}<h3>Security flags</h3>${d.flags.slice(0, 30).map(f => `<div class="test-card"><strong>${esc(f.event_type)}</strong> · ${fmt(f.created_at)} · ${esc(f.client_ip)}<br><small>${esc(f.details)}</small></div>`).join("") || "<p>No flags.</p>"}<p class="muted">Use Admin → Results to generate a selected-question PDF or email this student.</p>`); } catch (x) { fail(x); } }
async function adminRegistration(c) {
  const s = await api("/api/admin/registration-settings");
  c.innerHTML = `<article class="card"><div class="section-head"><div><h2>Student registration email domain</h2><p class="muted">Choose which email domain students may use during OTP registration. The default is All domains.</p></div><span class="badge">Current: ${esc(s.effective_domain)}</span></div><form id="registration-domain-form" class="form-grid"><div class="field"><label>Allowed registration domain</label><select name="mode"><option value="ALL" ${s.mode === "ALL" ? "selected" : ""}>All domains (default)</option><option value="GMAIL" ${s.mode === "GMAIL" ? "selected" : ""}>gmail.com only</option><option value="IITRPR" ${s.mode === "IITRPR" ? "selected" : ""}>iitrpr.ac.in only</option><option value="CUSTOM" ${s.mode === "CUSTOM" ? "selected" : ""}>Custom domain</option></select></div><div class="field" id="custom-domain-field"><label>Custom domain</label><input name="custom_domain" value="${esc(s.custom_domain || "")}" placeholder="example.edu"></div><button class="btn btn-primary">Save registration policy</button></form><div class="alert">This rule is enforced by the backend before the registration OTP is sent and checked again when the OTP is verified.</div></article>`;
  const f = document.getElementById("registration-domain-form"), custom = document.getElementById("custom-domain-field");
  const sync = () => custom.classList.toggle("hidden", f.mode.value !== "CUSTOM"); sync(); f.mode.onchange = sync;
  f.onsubmit = async e => { e.preventDefault(); try { await api("/api/admin/registration-settings", { method: "PATCH", body: { mode: f.mode.value, custom_domain: f.custom_domain.value } }); toast("Registration domain policy updated"); route(); } catch (x) { fail(x); } };
}

async function adminRecovery(c) { const items = await api("/api/admin/recovery?status=PENDING"); c.innerHTML = `<article class="card"><h2>Pending account recovery requests</h2>${items.map(x => `<div class="test-card"><div class="section-head"><div><strong>${esc(x.request_type)}</strong> · ${esc(x.full_name || x.enrollment_number)}<br><small>${esc(x.email)} · ${fmt(x.created_at)}</small></div><div class="actions"><button class="btn btn-success approve-rec" data-id="${x.id}">Approve</button><button class="btn btn-danger reject-rec" data-id="${x.id}">Reject</button></div></div><p>${esc(x.reason)}</p></div>`).join("") || `<p class="muted">No pending requests.</p>`}</article>`; c.querySelectorAll(".approve-rec").forEach(b => b.onclick = async () => { try { const r = await api(`/api/admin/recovery/${b.dataset.id}/approve`, { method: "POST", body: { remarks: "Approved", token_valid_minutes: 60 } }); openModal(`<h2>One-time recovery token</h2><div class="code-box">Request ID: ${esc(r.request_id)}\nToken: ${esc(r.reset_token)}</div><p>Give these details to the student. The token is shown only once.</p>`); route(); } catch (x) { fail(x); } }); c.querySelectorAll(".reject-rec").forEach(b => b.onclick = async () => { try { await api(`/api/admin/recovery/${b.dataset.id}/reject`, { method: "POST", body: { remarks: "Rejected", token_valid_minutes: 60 } }); toast("Request rejected"); route(); } catch (x) { fail(x); } }); }
async function adminProblems(c) {
  const items = await api("/api/admin/problems");
  const card = p => `<div class="test-card"><div class="section-head"><div><div class="actions problem-badges"><span class="badge">${p.is_practice ? "Practice" : "Assessment"}</span>${p.active ? `<span class="badge verdict-AC">Online</span>` : `<span class="badge verdict-WA">Hidden</span>`}${p.accepting_submissions ? `<span class="badge verdict-AC">Accepting solutions</span>` : `<span class="badge verdict-WA">Submissions closed</span>`}</div><strong>${esc(p.title)}</strong><br><small>${p.test_count} tests · ${p.max_score} marks</small></div><div class="actions"><a class="btn btn-primary" href="#/admin/problem-test/${p.id}">Test / Preview</a><button class="btn edit-problem" data-id="${p.id}">Edit problem</button><button class="btn tests" data-id="${p.id}" data-title="${esc(p.title)}">Manage tests</button><button class="btn copy-problem" data-id="${p.id}" data-title="${esc(p.title)}">Make copy</button><button class="btn toggle-accept ${p.accepting_submissions ? "btn-danger" : "btn-success"}" data-id="${p.id}" data-accepting="${p.accepting_submissions}">${p.accepting_submissions ? "Close submissions" : "Start accepting solutions"}</button><button class="btn toggle" data-id="${p.id}" data-active="${p.active}">${p.active ? "Deactivate" : "Activate"}</button></div></div></div>`;
  const assessment = items.filter(p => !p.is_practice), practice = items.filter(p => p.is_practice);
  c.innerHTML = `<article class="card"><div class="section-head"><div><h2>Problems and tests</h2><p class="muted">Visibility and submission acceptance are independent. A problem can stay online for reading/manual testing while official Run Test Cases / Submit are closed.</p></div><div class="actions"><button id="new-problem" class="btn btn-primary">New problem</button><button id="new-practice-problem" class="btn">New practice problem</button></div></div><h3>Assessment problems</h3>${assessment.map(card).join("") || `<p class="muted">No assessment problems.</p>`}<hr><h3>Practice problems</h3><p class="muted">Practice problems appear in the separate student Practice section and are hidden while Exam Mode is active.</p>${practice.map(card).join("") || `<p class="muted">No practice problems.</p>`}</article>`;
  document.getElementById("new-problem").onclick = () => problemForm(false);
  document.getElementById("new-practice-problem").onclick = () => problemForm(true);
  c.querySelectorAll(".toggle").forEach(b => b.onclick = async () => { try { await api(`/api/admin/problems/${b.dataset.id}`, { method: "PATCH", body: { active: b.dataset.active !== "true" } }); toast(b.dataset.active === "true" ? "Problem deactivated" : "Problem activated"); route(); } catch (x) { fail(x); } });
  c.querySelectorAll(".toggle-accept").forEach(b => b.onclick = async () => { const opening = b.dataset.accepting !== "true"; const action = opening ? "start accepting solutions for" : "close submissions for"; if (!confirm(`${action[0].toUpperCase() + action.slice(1)} this problem?`)) return; try { await api(`/api/admin/problems/${b.dataset.id}`, { method: "PATCH", body: { accepting_submissions: opening } }); toast(opening ? "Now accepting solutions" : "Submissions closed; problem remains online if active"); route(); } catch (x) { fail(x); } });
  c.querySelectorAll(".copy-problem").forEach(b => b.onclick = async () => { if (!confirm(`Make a full copy of “${b.dataset.title}”, including all public/private test cases? The copy will start hidden with submissions closed.`)) return; try { const r = await api(`/api/admin/problems/${b.dataset.id}/copy`, { method: "POST" }); toast(`Problem copied with ${r.copied_tests} test case${r.copied_tests === 1 ? "" : "s"}`); route(); } catch (x) { fail(x); } });
  c.querySelectorAll(".tests").forEach(b => b.onclick = () => manageTests(b.dataset.id, b.dataset.title));
  c.querySelectorAll(".edit-problem").forEach(b => b.onclick = () => editProblem(Number(b.dataset.id)));
}


async function renderAdminProblemTest(id) {
  if (!state.user || state.user.role !== "admin") { location.hash = "#/login"; return; }
  const [p, check] = await Promise.all([api(`/api/problems/${id}`), api(`/api/admin/problems/${id}/student-access-check`)]);
  const key = (language) => `judge-admin-test:${state.user.id}:${id}:${language}`;
  const initialLang = "c";
  const initial = localStorage.getItem(key(initialLang)) || templates[initialLang];
  const gate = !check.active
    ? `<div class="alert alert-danger"><strong>Student access check: HIDDEN.</strong> The question is absent from the student list and direct student access is blocked. Admin manual/configured testing remains available.</div>`
    : check.accepting_submissions
      ? `<div class="alert"><strong>Student access check: ONLINE + ACCEPTING SOLUTIONS.</strong> Students can open the question, Run Manual, Run Test Cases and Submit.</div>`
      : `<div class="alert"><strong>Student access check: ONLINE + SUBMISSIONS CLOSED.</strong> Students can open/read the question and Run Manual, but Run Test Cases and Submit are rejected until you start accepting solutions.</div>`;
  app.innerHTML = `<section class="section-head"><div><h1>Admin test — ${esc(p.title)}</h1><p class="muted">Test the exact statement, limits and configured test cases before exam activation.</p></div><div class="actions"><a class="btn" href="#/admin/problems">Back to problems</a><button id="admin-edit-problem" class="btn" type="button">Edit problem</button><button id="admin-toggle-accept" class="btn ${p.accepting_submissions ? "btn-danger" : "btn-success"}" type="button">${p.accepting_submissions ? "Close submissions" : "Start accepting solutions"}</button><button id="admin-toggle-problem" class="btn ${p.active ? "btn-danger" : "btn-primary"}" type="button">${p.active ? "Deactivate question" : "Activate question"}</button></div></section>${gate}<div class="split"><section><article class="card"><div class="section-head"><h2>Student preview</h2><div class="actions problem-badges"><span class="badge">${p.is_practice ? "PRACTICE" : "ASSESSMENT"}</span><span class="badge ${p.active ? "verdict-AC" : "verdict-WA"}">${p.active ? "ONLINE" : "DEACTIVATED"}</span><span class="badge ${p.accepting_submissions ? "verdict-AC" : "verdict-WA"}">${p.accepting_submissions ? "ACCEPTING SOLUTIONS" : "SUBMISSIONS CLOSED"}</span></div></div><h3>Problem statement</h3><p>${esc(p.statement).replaceAll("\n", "<br>")}</p><h3>Input format</h3><p>${esc(p.input_format).replaceAll("\n", "<br>") || "—"}</p><h3>Output format</h3><p>${esc(p.output_format).replaceAll("\n", "<br>") || "—"}</p><h3>Constraints</h3><p>${esc(p.constraints).replaceAll("\n", "<br>") || "—"}</p><p class="muted">${p.time_limit_ms} ms · ${p.memory_limit_mb} MB</p></article><article class="card"><h2>Public examples shown to students</h2>${p.public_tests.map(t => `<div class="test-card"><strong>Test ${t.position}</strong><div class="grid grid-2"><div><p class="muted">Input</p><pre class="code-box">${esc(t.input_data)}</pre></div><div><p class="muted">Expected output</p><pre class="code-box">${esc(t.expected_output)}</pre></div></div></div>`).join("") || `<p class="muted">No public examples.</p>`}</article></section><section class="sticky"><article id="admin-testing-panel" class="card coding-panel"><div class="section-head"><div><h2>Admin judge test</h2><p class="muted">Same advanced editor and secure judge path used to validate the student experience.</p></div><button id="admin-test-fullscreen" class="btn btn-small" type="button">Full-screen coding</button></div><div class="inline-form"><div class="field"><label>Language</label><select id="admin-test-language"><option value="c">C</option><option value="cpp">C++</option><option value="python">Python</option><option value="java">Java</option></select></div><div class="field"><label>Upload source</label><input id="admin-test-file" type="file" accept=".py,.c,.cpp,.cc,.cxx,.java"></div></div><div class="ide-toolbar"><span class="muted">Syntax highlighting · autocomplete (built-ins + your functions) · auto-indent · auto brackets · folding · Ctrl+Space suggestions</span></div><div id="admin-monaco-editor" class="monaco-editor-host"></div><textarea id="admin-test-editor" class="code-editor hidden" spellcheck="false">${esc(initial)}</textarea><div class="ide-statusbar"><span id="admin-editor-status">Loading editor…</span><span>UTF-8</span></div><div class="manual-test-panel"><div class="section-head"><div><strong>Manual test</strong><p class="muted">Run the code with administrator-provided stdin without touching configured test cases or creating a submission record.</p></div></div><div class="manual-test-grid"><div class="field"><label>Custom input (stdin)</label><textarea id="admin-manual-input" class="manual-stdin" spellcheck="false" placeholder="Type manual input…"></textarea></div><div><label>Program output</label><div id="admin-manual-result" class="manual-output muted">Run manually to see output here.</div></div></div></div><div class="actions ide-actions"><button id="admin-test-manual" class="btn">Run Manual</button><button id="admin-test-submit" class="btn btn-primary">Run Test Cases</button><button id="admin-test-reset" class="btn">Reset template</button><a id="admin-test-reference" class="btn" href="#/reference/c">C syntax</a><a class="btn" href="#/verdicts">Verdict meanings</a></div><div id="admin-test-result"></div></article></section></div>`;
  const lang = document.getElementById("admin-test-language"), panel = document.getElementById("admin-testing-panel"), fs = document.getElementById("admin-test-fullscreen"), ref = document.getElementById("admin-test-reference");
  lang.value = initialLang; let currentLang = initialLang, saveTimer = null;
  const codeEditor = await createJudgeEditor({ textareaId: "admin-test-editor", hostId: "admin-monaco-editor", statusId: "admin-editor-status", language: currentLang, value: initial, onChange: value => { clearTimeout(saveTimer); saveTimer = setTimeout(() => localStorage.setItem(key(currentLang), value), 300); } });
  state.monitorCleanup.push(() => { try { codeEditor.dispose(); } catch (_e) { } });
  const syncRef = () => { ref.href = `#/reference/${currentLang}`; ref.textContent = `${languageLabel(currentLang)} syntax`; }; syncRef();
  const switchLanguage = (next, value = null) => { localStorage.setItem(key(currentLang), codeEditor.getValue()); currentLang = next; lang.value = next; codeEditor.setLanguage(next); codeEditor.setValue(value ?? localStorage.getItem(key(next)) ?? templates[next]); syncRef(); codeEditor.focus(); };
  lang.onchange = () => switchLanguage(lang.value);
  document.getElementById("admin-test-reset").onclick = () => { if (confirm("Replace current admin test code with the language template?")) codeEditor.setValue(templates[currentLang]); };
  document.getElementById("admin-test-file").onchange = async e => { const file = e.target.files[0]; if (!file) return; const ext = file.name.split(".").pop().toLowerCase(); const map = { py: "python", c: "c", cpp: "cpp", cc: "cpp", cxx: "cpp", java: "java" }; if (!map[ext]) { fail(new Error("Unsupported file type.")); return; } switchLanguage(map[ext], await file.text()); };
  fs.onclick = async () => { try { if (document.fullscreenElement) await document.exitFullscreen(); else { await panel.requestFullscreen(); codeEditor.focus(); } setTimeout(() => codeEditor.layout(), 60); } catch (e) { fail(e); } };
  const onFs = () => { fs.textContent = document.fullscreenElement ? "Exit full screen" : "Full-screen coding"; panel.classList.toggle("is-fullscreen", Boolean(document.fullscreenElement)); setTimeout(() => codeEditor.layout(), 60); }; document.addEventListener("fullscreenchange", onFs); state.monitorCleanup.push(() => document.removeEventListener("fullscreenchange", onFs));
  document.getElementById("admin-edit-problem").onclick = () => editProblem(id, () => renderAdminProblemTest(id));
  document.getElementById("admin-toggle-accept").onclick = async () => { const next = !p.accepting_submissions; const verb = next ? "start accepting solutions for" : "close submissions for"; if (!confirm(`${verb[0].toUpperCase() + verb.slice(1)} this question?`)) return; try { await api(`/api/admin/problems/${id}`, { method: "PATCH", body: { accepting_submissions: next } }); toast(next ? "Now accepting student solutions" : "Student submissions closed; admin testing remains available"); route(); } catch (e) { fail(e); } };
  document.getElementById("admin-toggle-problem").onclick = async () => { const next = !p.active; const verb = next ? "activate" : "deactivate"; if (!confirm(`${verb[0].toUpperCase() + verb.slice(1)} this question?`)) return; try { await api(`/api/admin/problems/${id}`, { method: "PATCH", body: { active: next } }); toast(`Question ${next ? "activated" : "deactivated"}`); route(); } catch (e) { fail(e); } };
  document.getElementById("admin-test-manual").onclick = async () => { const b = document.getElementById("admin-test-manual"), target = document.getElementById("admin-manual-result"); busy(b, true, "Running…"); target.classList.remove("muted"); target.innerHTML = `<div class="muted">Compiling and running with manual input…</div>`; try { const fd = new FormData(); fd.set("problem_id", String(id)); fd.set("language", currentLang); fd.set("code", codeEditor.getValue()); fd.set("stdin_data", document.getElementById("admin-manual-input").value); const r = await api("/api/submissions/manual-run", { method: "POST", body: fd }); target.innerHTML = renderManualRunResult(r); } catch (e) { target.innerHTML = `<div class="alert alert-danger">${esc(e.message)}</div>`; fail(e); } finally { busy(b, false); codeEditor.focus(); } };
  document.getElementById("admin-test-submit").onclick = async () => { const b = document.getElementById("admin-test-submit"); busy(b, true, "Running…"); try { const fd = new FormData(); fd.set("problem_id", String(id)); fd.set("language", currentLang); fd.set("code", codeEditor.getValue()); const r = await api("/api/submissions", { method: "POST", body: fd }); toast(`Admin ${languageLabel(currentLang)} test queued`); pollAdminProblemTest(r.submission_id, document.getElementById("admin-test-result")); } catch (e) { fail(e); } finally { busy(b, false); } };
}

function pollAdminProblemTest(id, target) {
  const run = async () => { try { const s = await api(`/api/submissions/${id}`); target.innerHTML = renderAdminProblemTestResult(s); if (!FINAL.has(s.status)) { state.polling = setTimeout(run, 1000); } else { toast(`Admin test complete: ${s.verdict}`); } } catch (e) { target.innerHTML = `<div class="alert alert-danger">${esc(e.message)}</div>`; } }; run();
}

function renderAdminProblemTestResult(s) {
  const rows = (s.results || []).map(r => `<div class="test-card"><div class="section-head"><strong>Test ${r.position} · ${r.visibility.toUpperCase()} · ${badge(r.verdict)}</strong><span>${r.points_awarded} marks · ${r.runtime_ms} ms · ${r.memory_kb} KB</span></div><div class="grid grid-2"><div><small>Expected output</small><pre class="code-box">${esc(r.expected_output)}</pre></div><div><small>Actual output</small><pre class="code-box">${esc(r.actual_output)}</pre></div></div>${r.stderr ? `<small>stderr</small><pre class="code-box">${esc(r.stderr)}</pre>` : ""}</div>`).join("");
  return `<div class="test-card"><div class="section-head"><h3>Judge result ${badge(s.verdict || s.status)}</h3><strong>${s.final_score}/${s.total_points ?? "—"}</strong></div>${s.compile_message ? `<h4>Compiler / judge message</h4><pre class="code-box">${esc(s.compile_message)}</pre>` : ""}${FINAL.has(s.status) ? `<h4>All configured test cases</h4>${rows || `<p class="muted">No test results were produced.</p>`}` : `<p class="muted">Judging in progress…</p>`}</div>`;
}

function problemForm(practiceDefault = false) { openModal(`<h2>Create ${practiceDefault ? "practice " : ""}problem</h2><form id="problem-form" class="form-grid"><div class="field"><label>Title</label><input name="title" required></div><div class="field"><label>Statement</label><textarea name="statement" required></textarea></div><div class="field"><label>Input format</label><textarea name="input_format"></textarea></div><div class="field"><label>Output format</label><textarea name="output_format"></textarea></div><div class="field"><label>Constraints</label><textarea name="constraints"></textarea></div><div class="grid grid-3"><div class="field"><label>Time ms</label><input name="time_limit_ms" type="number" value="2000"></div><div class="field"><label>Memory MB</label><input name="memory_limit_mb" type="number" value="256"></div><div class="field"><label>Submission limit</label><input name="submission_limit" type="number"></div></div><div class="field"><label><input name="is_practice" type="checkbox" ${practiceDefault ? "checked" : ""}> Practice problem</label></div><div class="field"><label><input name="accepting_submissions" type="checkbox" checked> Start accepting solutions immediately</label></div><div class="field"><label><input name="active" type="checkbox" checked> Online / visible to students</label></div><button class="btn btn-primary">Create problem</button></form>`); setTimeout(() => { document.getElementById("problem-form").onsubmit = async e => { e.preventDefault(); const f = e.currentTarget; const data = Object.fromEntries(new FormData(f)); data.time_limit_ms = Number(data.time_limit_ms); data.memory_limit_mb = Number(data.memory_limit_mb); data.submission_limit = data.submission_limit ? Number(data.submission_limit) : null; data.is_practice = f.is_practice.checked; data.accepting_submissions = f.accepting_submissions.checked; data.active = f.active.checked; try { await api("/api/admin/problems", { method: "POST", body: data }); modal.close(); toast("Problem created"); route(); } catch (x) { fail(x); } }; }, 0); }

async function editProblem(id, onSaved = null) {
  try {
    const p = await api(`/api/problems/${id}`);
    openModal(`<h2>Edit problem</h2><p class="muted">Edit the question itself here. Public/private cases are managed separately. Visibility and submission acceptance can also be controlled from the Problems page.</p><form id="edit-problem-form" class="form-grid"><div class="field"><label>Title</label><input name="title" value="${esc(p.title)}" required></div><div class="field"><label>Statement</label><textarea name="statement" required>${esc(p.statement)}</textarea></div><div class="field"><label>Input format</label><textarea name="input_format">${esc(p.input_format)}</textarea></div><div class="field"><label>Output format</label><textarea name="output_format">${esc(p.output_format)}</textarea></div><div class="field"><label>Constraints</label><textarea name="constraints">${esc(p.constraints)}</textarea></div><div class="grid grid-3"><div class="field"><label>Time limit (ms)</label><input name="time_limit_ms" type="number" min="100" max="30000" value="${p.time_limit_ms}" required></div><div class="field"><label>Memory limit (MB)</label><input name="memory_limit_mb" type="number" min="32" max="2048" value="${p.memory_limit_mb}" required></div><div class="field"><label>Submission limit</label><input name="submission_limit" type="number" min="1" value="${p.submission_limit ?? ""}" placeholder="Unlimited"></div></div><div class="field"><label><input name="is_practice" type="checkbox" ${p.is_practice ? "checked" : ""}> Practice problem</label></div><div class="field"><label><input name="accepting_submissions" type="checkbox" ${p.accepting_submissions ? "checked" : ""}> Accept student solutions</label></div><div class="field"><label><input name="exam_mode_enabled" type="checkbox" ${p.exam_mode_enabled ? "checked" : ""}> Available when Exam Mode is enabled</label></div><button class="btn btn-primary">Save problem changes</button></form>`);
    setTimeout(() => { const f = document.getElementById("edit-problem-form"); if (f) f.onsubmit = async e => { e.preventDefault(); const body = { title: f.title.value, statement: f.statement.value, input_format: f.input_format.value, output_format: f.output_format.value, constraints: f.constraints.value, time_limit_ms: Number(f.time_limit_ms.value), memory_limit_mb: Number(f.memory_limit_mb.value), submission_limit: f.submission_limit.value ? Number(f.submission_limit.value) : null, is_practice: f.is_practice.checked, accepting_submissions: f.accepting_submissions.checked, exam_mode_enabled: f.exam_mode_enabled.checked }; try { await api(`/api/admin/problems/${id}`, { method: "PATCH", body }); modal.close(); toast("Problem updated"); if (onSaved) await onSaved(); else route(); } catch (x) { fail(x); } }; }, 0);
  } catch (x) { fail(x); }
}


async function manageTests(id, title) { try { const tests = await api(`/api/admin/problems/${id}/tests`); openModal(`<h2>${esc(title)} — Test cases</h2>${tests.map(t => `<div class="test-card"><strong>Position ${t.position} · ${t.is_public ? "Public" : "Private"} · ${t.points} marks</strong><div class="grid grid-2"><pre class="code-box">${esc(t.input_data)}</pre><pre class="code-box">${esc(t.expected_output)}</pre></div><button class="btn btn-danger btn-small delete-test" data-id="${t.id}">Delete</button></div>`).join("")}<h3>Add test</h3><form id="test-form" class="form-grid"><div class="grid grid-3"><div class="field"><label>Position</label><input name="position" type="number" min="1" required></div><div class="field"><label>Points</label><input name="points" type="number" step="0.01" value="1"></div><div class="field"><label><input name="is_public" type="checkbox"> Public</label></div></div><div class="field"><label>Input</label><textarea name="input_data"></textarea></div><div class="field"><label>Expected output</label><textarea name="expected_output"></textarea></div><button class="btn btn-primary">Add test</button></form>`); setTimeout(() => { modalContent.querySelectorAll(".delete-test").forEach(b => b.onclick = async () => { try { await api(`/api/admin/tests/${b.dataset.id}`, { method: "DELETE" }); modal.close(); manageTests(id, title); } catch (x) { fail(x); } }); document.getElementById("test-form").onsubmit = async e => { e.preventDefault(); const f = e.currentTarget; try { await api(`/api/admin/problems/${id}/tests`, { method: "POST", body: { position: Number(f.position.value), points: Number(f.points.value), is_public: f.is_public.checked, input_data: f.input_data.value, expected_output: f.expected_output.value } }); modal.close(); manageTests(id, title); } catch (x) { fail(x); } }; }, 0); } catch (x) { fail(x); } }
async function adminSubmissions(c) {
  const items = await api("/api/admin/submissions/summary");
  c.innerHTML = `<article class="card"><div class="section-head"><div><h2>Submissions by student</h2><p class="muted">Open a student to inspect the current solution for each problem/language, source code, scores and full test-result/manual-grading analysis.</p></div></div><div class="table-wrap"><table><thead><tr><th>Student</th><th>Enrollment</th><th>Language solutions</th><th>Problems attempted</th><th>Best automatic score</th><th>Latest submission</th><th></th></tr></thead><tbody>${items.map(row => `<tr><td><a href="#/admin/submissions/user/${row.user_id}">${identityHtml(row.student, row.email)}</a></td><td>${esc(row.enrollment_number)}</td><td><strong>${row.submission_count}</strong></td><td>${row.problems_attempted}</td><td>${row.best_automatic_score}</td><td>${fmt(row.latest_at)}</td><td><a class="btn btn-small" href="#/admin/submissions/user/${row.user_id}">Analyze submissions</a></td></tr>`).join("") || `<tr><td colspan="7">No submissions yet.</td></tr>`}</tbody></table></div></article>`;
}

async function renderAdminUserSubmissions(userId) {
  if (!state.user || state.user.role !== "admin") { location.hash = "#/login"; return; }
  const data = await api(`/api/admin/submissions/user/${userId}`);
  const u = data.user;
  app.innerHTML = `<section class="section-head"><div><h1>${esc(u.full_name)} — Submission analysis</h1><p class="muted">${esc(u.enrollment_number)} · ${esc(u.email)} · ${data.submissions.length} submission${data.submissions.length === 1 ? "" : "s"}</p></div><div class="actions"><a class="btn" href="#/admin/submissions">Back to students</a><a class="btn" href="#/admin/flags">Violation history</a></div></section><section class="grid">${data.submissions.map(s => `<article class="card"><div class="section-head"><div><h2>${esc(s.problem_title)} — ${esc(languageLabel(s.language))}</h2><p class="muted">${fmt(s.created_at)} · ${badge(s.verdict || s.status)}</p></div><div class="actions"><strong>Final ${s.final_score}</strong>${s.official ? `<span class="badge verdict-AC">Official</span>` : ""}</div></div><div class="grid grid-3"><div class="stat"><small>Automatic</small><strong>${s.automatic_score}</strong></div><div class="stat"><small>Manual</small><strong>${s.manual_score ?? "—"}</strong></div><div class="stat"><small>Status</small><strong style="font-size:18px">${esc(s.verdict || s.status)}</strong></div></div>${s.compile_message ? `<div class="alert"><strong>Compiler/Judge message</strong><pre class="code-box">${esc(s.compile_message)}</pre></div>` : ""}<details class="test-card"><summary><strong>Read submitted source code</strong></summary><pre class="code-box">${esc(s.source_code)}</pre></details><div class="actions"><a class="btn btn-primary" href="#/submission/${s.id}">Full submission analysis</a></div></article>`).join("") || `<article class="card"><p>No submissions for this student.</p></article>`}</section>`;
}

async function adminFlags(c) {
  const summary = await api("/api/admin/flags/summary");
  c.innerHTML = `<article class="card"><div class="section-head"><div><h2>Violation counts by student</h2><p class="muted">Counts are retained permanently by exam session and student. Detailed event records are kept for 5 hours only. Highest violation count is shown first.</p></div></div><div class="table-wrap"><table><thead><tr><th>Student</th><th>Enrollment</th><th>Total violations</th><th>Sessions</th><th>Latest activity</th><th></th></tr></thead><tbody>${summary.map(row => `<tr><td><button class="link-button flag-user" data-id="${row.user_id}">${identityHtml(row.student, row.email)}</button></td><td>${esc(row.enrollment_number)}</td><td><strong class="danger-text">${row.violation_count}</strong></td><td>${row.session_count}</td><td>${fmt(row.latest_at)}</td><td><button class="btn btn-small flag-user" data-id="${row.user_id}">View activity</button></td></tr>`).join("") || `<tr><td colspan="6">No violations recorded.</td></tr>`}</tbody></table></div></article>`;
  c.querySelectorAll(".flag-user").forEach(button => button.onclick = async () => {
    try {
      const payload = await api(`/api/admin/flags?user_id=${button.dataset.id}`);
      const who = summary.find(x => String(x.user_id) === String(button.dataset.id));
      const sessionRows = payload.sessions.map(x => { const types = Object.entries(x.event_counts || {}).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${esc(k.replaceAll("_", " "))}: ${v}`).join(" · ") || "—"; return `<tr><td>${fmt(x.session_started_at)}</td><td>${fmt(x.session_ended_at)}</td><td>${durationText(x.duration_seconds)}</td><td>${esc(x.status)}${x.end_reason ? `<br><small class="muted">${esc(x.end_reason.replaceAll("_", " "))}</small>` : ""}</td><td><strong class="danger-text">${x.violation_count}</strong></td><td>${types}</td><td><code>${esc(x.generation_id)}</code></td></tr>`; }).join("") || `<tr><td colspan="7">No session summaries.</td></tr>`;
      const cards = payload.details.map(f => { const problemText = f.problem_id ? ` · Problem ${f.problem_id}` : ""; return `<div class="test-card"><div class="section-head"><strong class="danger-text">${esc(f.event_type)}</strong><span>${fmt(f.created_at)}</span></div><p>${esc(f.details || "No additional details")}</p><small class="muted">IP ${esc(f.client_ip)}${problemText}</small></div>`; }).join("") || `<p class="muted">No detailed events remain in the 5-hour retention window. Permanent counts above are still preserved.</p>`;
      openModal(`<h2>${esc(who?.student || "Student")} — ${who?.violation_count || 0} total violations</h2><p class="muted">Permanent counts are grouped by exam session. Detailed events below are latest first and automatically removed after ${payload.retention_hours} hours.</p><h3>Session-wise counts</h3><div class="table-wrap"><table><thead><tr><th>Started</th><th>Ended</th><th>Duration</th><th>Status</th><th>Violations</th><th>By type</th><th>Exam generation</th></tr></thead><tbody>${sessionRows}</tbody></table></div><h3>Recent detailed activity</h3>${cards}`);
    } catch (x) { fail(x); }
  });
}

async function adminIpAccess(c) {
  const data = await api("/api/admin/ip-access");
  c.innerHTML = `<div class="grid grid-2"><article class="card"><h2>IP access mode</h2><p>Current administrator IP: <code>${esc(data.current_ip)}</code></p><form id="ip-mode-form" class="form-grid"><div class="field"><label>Mode</label><select name="mode"><option value="ALL" ${data.mode === "ALL" ? "selected" : ""}>ALL — allow every IP</option><option value="APPROVED" ${data.mode === "APPROVED" ? "selected" : ""}>APPROVED — allow only listed IPs</option><option value="REJECTED" ${data.mode === "REJECTED" ? "selected" : ""}>REJECTED — block listed IPs</option></select></div><button class="btn btn-primary">Save mode</button></form><div class="alert"><strong>ALL:</strong> list is ignored.<br><strong>APPROVED:</strong> only listed IPs/networks can open and log in.<br><strong>REJECTED:</strong> listed IPs/networks are denied.</div></article><article class="card"><h2>Add IP or network</h2>${data.mode === "ALL" ? `<div class="alert">Switch to APPROVED or REJECTED mode before adding entries.</div>` : ""}<form id="ip-entry-form" class="form-grid"><div class="field"><label>IP / CIDR</label><input name="ip_value" placeholder="172.26.1.42 or 172.26.1.0/24" required></div><div class="field"><label>Note</label><input name="note" placeholder="Lab PC 12"></div><button class="btn btn-primary" ${data.mode === "ALL" ? "disabled" : ""}>Add to ${esc(data.mode)} list</button></form></article></div><article class="card"><h2>Configured IP list</h2><div class="table-wrap"><table><thead><tr><th>List</th><th>IP / network</th><th>Note</th><th>Added</th><th></th></tr></thead><tbody>${data.entries.map(x => `<tr><td>${badge(x.list_mode)}</td><td><code>${esc(x.ip_value)}</code></td><td>${esc(x.note)}</td><td>${fmt(x.created_at)}</td><td><button class="btn btn-small btn-danger delete-ip" data-id="${x.id}">Delete</button></td></tr>`).join("") || `<tr><td colspan="5">No IPs configured.</td></tr>`}</tbody></table></div></article>`;
  document.getElementById("ip-mode-form").onsubmit = async e => { e.preventDefault(); try { await api("/api/admin/ip-access", { method: "PATCH", body: { mode: e.currentTarget.mode.value } }); toast("IP access mode updated"); route(); } catch (x) { fail(x); } };
  document.getElementById("ip-entry-form").onsubmit = async e => { e.preventDefault(); const f = e.currentTarget; try { await api("/api/admin/ip-access/entries", { method: "POST", body: { ip_value: f.ip_value.value, note: f.note.value } }); toast("IP entry added"); route(); } catch (x) { fail(x); } };
  c.querySelectorAll(".delete-ip").forEach(button => button.onclick = async () => { if (!confirm("Delete this IP entry?")) return; try { await api(`/api/admin/ip-access/entries/${button.dataset.id}`, { method: "DELETE" }); toast("IP entry deleted"); route(); } catch (x) { fail(x); } });
}

function scoreGraph(stats) {
  if (!stats.length) return `<p class="muted">No question statistics.</p>`;
  return `<div class="graph-scroll"><canvas id="scoreChart"></canvas></div>`;
}
function attemptGraph(stats) {
  if (!stats.length) return "";
  return `<div class="attempt-bars"><canvas id="attemptChart"></canvas></div>`;
}

async function adminResults(c) {
  const options = await api("/api/reports/admin/options");
  c.innerHTML = `<article class="card"><div class="section-head"><div><h2>Results</h2><p class="muted">Select one or more questions, generate overall and student-wise results, review question statistics and graphs, email results, or export CSV, PDF and Excel.</p></div></div><div class="question-selector">${options.problems.map(p => `<label class="question-check"><input type="checkbox" class="result-problem" value="${p.id}"> <strong>Q${p.id}</strong> ${esc(p.title)} <small>${p.maximum} marks</small></label>`).join("") || `<p>No questions available.</p>`}</div><div class="actions"><button id="select-all-results" class="btn">Select all</button><button id="generate-results" class="btn btn-primary">Generate selected results</button></div></article><section id="result-output"></section>`;
  document.getElementById("select-all-results").onclick = () => document.querySelectorAll(".result-problem").forEach(x => x.checked = true);
  const selected = () => [...document.querySelectorAll(".result-problem:checked")].map(x => Number(x.value));
  document.getElementById("generate-results").onclick = async () => {
    const ids = selected(); if (!ids.length) { fail(new Error("Select at least one question.")); return; } const b = document.getElementById("generate-results"); busy(b, true, "Generating…");
    try {
      const qs = ids.join(","), data = await api(`/api/reports/admin/selected?problem_ids=${encodeURIComponent(qs)}`), out = document.getElementById("result-output");
      const qheads = data.problems.map(p => `<th>Q${p.id}<br><small>${esc(p.title)}</small></th>`).join("");
      const rows = data.students.map(x => `<tr><td>${identityHtml(x.student.full_name, x.student.email, x.student.enrollment_number)}</td>${x.rows.map(r => `<td><strong>${Number(r.final_score).toFixed(2)}</strong>/${Number(r.maximum).toFixed(2)}<br><small>${esc(r.language === "cpp" ? "C++" : r.language === "python" ? "Python" : r.language === "c" ? "C" : "—")} · ${esc(r.verdict)}</small></td>`).join("")}<td><strong>${Number(x.summary.total).toFixed(2)}/${Number(x.summary.maximum).toFixed(2)}</strong><br>${Number(x.summary.percentage).toFixed(2)}%</td><td><div class="actions"><button class="btn btn-small student-result" data-id="${x.student.id}">Student result</button><button class="btn btn-small email-student-result" data-id="${x.student.id}">Email</button></div></td></tr>`).join("") || `<tr><td colspan="${data.problems.length + 3}">No students.</td></tr>`;
      const stats = data.statistics.map(x => `<tr><td>Q${x.problem_id}<br><small>${esc(x.problem)}</small></td><td>${x.students}</td><td>${x.attempted}</td><td>${x.accepted}</td><td>${Number(x.min).toFixed(2)}</td><td>${Number(x.max).toFixed(2)}</td><td>${Number(x.avg).toFixed(2)}</td><td>${Number(x.median).toFixed(2)}</td><td>${Number(x.stddev).toFixed(2)}</td><td>${Number(x.avg_percentage).toFixed(2)}%</td></tr>`).join("");
      out.innerHTML = `<article class="card"><div class="section-head"><div><h2>Overall result — selected questions</h2><p class="muted">The result policy chooses one language solution per question; there are no student submission versions.</p></div><div class="actions"><a class="btn" href="/api/reports/admin/selected.csv?problem_ids=${encodeURIComponent(qs)}">CSV</a><a class="btn" href="/api/reports/admin/selected.pdf?problem_ids=${encodeURIComponent(qs)}">PDF</a><a class="btn" href="/api/reports/admin/selected.xlsx?problem_ids=${encodeURIComponent(qs)}">Excel sheet</a><button id="email-all-results" class="btn btn-primary">Email all students</button></div></div><div class="table-wrap"><table><thead><tr><th>Student</th>${qheads}<th>Total</th><th>Actions</th></tr></thead><tbody>${rows}</tbody></table></div></article><div class="grid grid-2"><article class="card"><h2>Score graph</h2>${scoreGraph(data.statistics)}</article><article class="card"><h2>Attempt / accepted graph</h2>${attemptGraph(data.statistics)}</article></div><article class="card"><h2>Question-wise statistics</h2><div class="table-wrap"><table><thead><tr><th>Question</th><th>Students</th><th>Attempted</th><th>AC</th><th>Min</th><th>Max</th><th>Average</th><th>Median</th><th>Std dev</th><th>Avg %</th></tr></thead><tbody>${stats}</tbody></table></div></article>`;
      
      // Render Chart.js charts if there is statistics data
      if (data.statistics.length && window.Chart) {
        const labels = data.statistics.map(x => `Q${x.problem_id}`);
        const scoreCtx = document.getElementById("scoreChart");
        if (scoreCtx) {
          new Chart(scoreCtx, {
            type: 'bar',
            data: {
              labels,
              datasets: [
                { label: 'Min', data: data.statistics.map(x => x.min), backgroundColor: '#fca5a5' },
                { label: 'Avg', data: data.statistics.map(x => x.avg), backgroundColor: '#93c5fd' },
                { label: 'Max', data: data.statistics.map(x => x.max), backgroundColor: '#86efac' }
              ]
            },
            options: { indexAxis: 'y', responsive: true }
          });
        }
        const attemptCtx = document.getElementById("attemptChart");
        if (attemptCtx) {
          new Chart(attemptCtx, {
            type: 'bar',
            data: {
              labels,
              datasets: [
                { label: 'Attempted', data: data.statistics.map(x => x.attempted), backgroundColor: '#fcd34d' },
                { label: 'Accepted (AC)', data: data.statistics.map(x => x.accepted), backgroundColor: '#34d399' }
              ]
            },
            options: { indexAxis: 'y', responsive: true }
          });
        }
      }

      out.querySelectorAll(".student-result").forEach(btn => btn.onclick = async () => { try { const x = await api(`/api/reports/admin/student/${btn.dataset.id}/selected?problem_ids=${encodeURIComponent(qs)}`); openModal(`<h2>${esc(x.student.full_name)}</h2>${identityHtml(x.student.full_name, x.student.email, x.student.enrollment_number)}<div class="grid grid-3"><div class="stat"><small>Total</small><strong>${Number(x.summary.total).toFixed(2)}/${Number(x.summary.maximum).toFixed(2)}</strong></div><div class="stat"><small>Percentage</small><strong>${Number(x.summary.percentage).toFixed(2)}%</strong></div><div class="stat"><small>Result</small><strong>${x.summary.passed ? "PASS" : "FAIL"}</strong></div></div><div class="table-wrap"><table><thead><tr><th>Question</th><th>Language</th><th>Verdict</th><th>Final</th><th>Maximum</th></tr></thead><tbody>${x.rows.map(r => `<tr><td>${esc(r.problem)}</td><td>${esc(r.language === "cpp" ? "C++" : r.language === "python" ? "Python" : r.language === "c" ? "C" : "—")}</td><td>${badge(r.verdict)}</td><td>${Number(r.final_score).toFixed(2)}</td><td>${Number(r.maximum).toFixed(2)}</td></tr>`).join("")}</tbody></table></div><div class="actions"><a class="btn btn-primary" href="/api/reports/admin/student/${x.student.id}.pdf?problem_ids=${encodeURIComponent(qs)}">Download student PDF</a></div>`); } catch (e) { fail(e); } });
      out.querySelectorAll(".email-student-result").forEach(btn => btn.onclick = async () => { const target = btn; busy(target, true, "Sending…"); try { const r = await api("/api/reports/admin/email/student", { method: "POST", body: { user_id: Number(btn.dataset.id), problem_ids: ids } }); toast(`Result emailed to ${r.email}`); } catch (e) { fail(e); } finally { busy(target, false); } });
      document.getElementById("email-all-results").onclick = async () => { if (!confirm(`Email the selected-question result to all ${data.students.length} students?`)) return; const eb = document.getElementById("email-all-results"); busy(eb, true, "Emailing…"); try { const r = await api("/api/reports/admin/email/all", { method: "POST", body: { problem_ids: ids } }); toast(`Result email complete: ${r.sent} sent, ${r.failed} failed`, r.failed > 0); } catch (e) { fail(e); } finally { busy(eb, false); } };
    } catch (e) { fail(e); } finally { busy(b, false); }
  };
}


window.addEventListener("hashchange", route);
window.addEventListener("load", async () => { initTheme(); try { await loadMe(); renderNav(); route(); state.sessionCheck = setInterval(async () => { if (!state.user) return; try { const me = await api("/api/auth/me"); const changed = state.user.exam_code_required !== me.exam_code_required; state.user = me; if (changed) route(); } catch (e) { if (e.status === 401) { state.user = null; renderNav(); location.hash = "#/login"; toast(e.message, true); } } }, 8000); } catch (e) { fail(e); route(); } });
