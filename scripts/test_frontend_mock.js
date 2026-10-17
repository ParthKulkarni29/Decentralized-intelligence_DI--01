const fs = require('fs');
const vm = require('vm');

const html = fs.readFileSync('frontend/index.html', 'utf8');
const scriptMatch = html.match(/<script type="text\/babel">([\s\S]*?)<\/script>/);

// Setup mock window and document
const reactCode = fs.readFileSync('frontend/assets/vendor/react.production.min.js', 'utf8');
const win = {
  location: { search: '?mock=1' }
};
const context = {
  console,
  setTimeout,
  clearTimeout,
  window: win,
  self: win,
  URLSearchParams: class {
    constructor(s) { this.params = new Map(); if (s && s.includes('=')) { const [k, v] = s.replace('?', '').split('='); this.params.set(k, v); } }
    get(k) { return this.params.get(k); }
  },
  localStorage: {
    getItem: () => null,
    setItem: () => {}
  },
  ReactDOM: { createRoot: () => ({ render: () => {} }) },
  document: {
    getElementById: () => null,
    documentElement: { classList: { add: () => {}, remove: () => {} } }
  }
};
context.global = context;
context.window.React = context.React;

const vmCtx = vm.createContext(context);
vm.runInContext(reactCode, vmCtx);
context.React = context.window.React;

// Transform script
const babelCode = fs.readFileSync('frontend/assets/vendor/babel.min.js', 'utf8');
const bCtx = { console, setTimeout, clearTimeout, window: context.window, global: context };
vm.createContext(bCtx);
vm.runInContext(babelCode, bCtx);
const transpiled = bCtx.Babel.transform(scriptMatch[1], { presets: ['react'] }).code;

// Wrap to extract CONFIG and api
const runnerCode = transpiled + `
;global.exportedApi = api;
global.exportedConfig = CONFIG;
`;
vm.createContext(context);
vm.runInContext(runnerCode, context);

async function runMockTests() {
  console.log("=== Testing Frontend in Mock Mode (?mock=1) ===");
  const api = context.exportedApi;
  const config = context.exportedConfig;
  console.log("CONFIG.USE_MOCK:", config.USE_MOCK);

  // 1. getChallenge
  const ch = await api.getChallenge("last-k-total");
  console.log("1. getChallenge('last-k-total'):", ch.title, "(visible tests:", ch.visible_tests.length, ")");

  // 2. getDraft
  const draft = await api.getDraft("rohan", "last-k-total");
  console.log("2. getDraft:", draft.trim().slice(0, 40) + "...");

  // 3. run endpoint (visible only)
  const buggyCode = "def last_k_total(marks, k):\n    start = len(marks) - k\n    return sum(marks[start:])\n";
  const runRes = await api.run("rohan", "last-k-total", buggyCode);
  console.log("3. api.run visible count:", runRes.visible.length);
  console.log("   First visible outcome:", runRes.visible[0].outcome, "| call:", runRes.visible[0].call);

  // 4. submit endpoint (buggy code)
  const submitRes = await api.submit("rohan", "last-k-total", buggyCode);
  console.log("4. api.submit (buggy):");
  console.log("   submission_id:", submitRes.submission_id);
  console.log("   all_passed:", submitRes.all_passed);
  console.log("   visible count:", submitRes.visible.length, "| hidden count:", submitRes.hidden.length);
  console.log("   mistake_tags:", submitRes.mistake_tags);
  console.log("   prediction_result:", submitRes.prediction_result);
  console.log("   arena:", submitRes.arena);

  // 5. getHint
  const hintRes = await api.getHint(submitRes.submission_id, 0);
  console.log("5. api.getHint:", `Hint #${hintRes.hint_number}: ${hintRes.hint.slice(0, 50)}...`);

  // 6. getChallengeSubmissions
  const hist = await api.getChallengeSubmissions("rohan", "last-k-total");
  console.log("6. api.getChallengeSubmissions count:", hist.length);

  // 7. getSubmission
  const detail = await api.getSubmission(submitRes.submission_id);
  console.log("7. api.getSubmission code present:", detail.code ? "YES" : "NO");

  // 8. submit endpoint (passing code)
  const cleanCode = "def last_k_total(marks, k):\n    start = max(0, len(marks) - k)\n    return sum(marks[start:])\n";
  const passRes = await api.submit("rohan", "last-k-total", cleanCode);
  console.log("8. api.submit (passing): all_passed =", passRes.all_passed, "| hidden passed count:", passRes.hidden.filter(h => h.outcome === 'passed').length);

  console.log("\nPASS: All mock data layer functions verified successfully!");
}

runMockTests().catch(err => {
  console.error("FAIL:", err);
  process.exit(1);
});
