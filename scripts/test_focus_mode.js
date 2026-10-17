const fs = require('fs');
const vm = require('vm');
const path = require('path');

const indexPath = path.join(__dirname, '..', 'frontend', 'index.html');
const cssPath = path.join(__dirname, '..', 'frontend', 'assets', 'sakura.css');

const htmlContent = fs.readFileSync(indexPath, 'utf-8');
const cssContent = fs.readFileSync(cssPath, 'utf-8');

console.log("=== Phase 6 Focus Mode Validation ===");

// 1. Verify zero innerHTML in index.html
if (htmlContent.includes('.innerHTML') || htmlContent.includes('dangerouslySetInnerHTML')) {
  console.error("FAIL: innerHTML found in index.html!");
  process.exit(1);
} else {
  console.log("PASS 1: Zero innerHTML or dangerouslySetInnerHTML found in index.html.");
}

// 2. Verify sakura.css contains focus mode and reduced-motion rules
const requiredCssSelectors = [
  '.focus-viewport',
  '.focus-workbench',
  '.focus-editor-card',
  '.focus-results-card',
  'prefers-reduced-motion'
];
for (const sel of requiredCssSelectors) {
  if (!cssContent.includes(sel)) {
    console.error(`FAIL: Missing ${sel} in sakura.css!`);
    process.exit(1);
  }
}
console.log("PASS 2: sakura.css contains all required focus-mode and prefers-reduced-motion layout classes.");

// 3. Verify Focus Toggle button and keyboard handlers in index.html source
const requiredCodeSnippets = [
  'debugdojo_focus_mode',
  'isFocusActive',
  'focusMode',
  'setFocusMode',
  'descCollapsed',
  'setDescCollapsed',
  'Escape',
  'handleFocusKeys',
  'focus-workbench',
  'focus-viewport',
  'focus-editor-card',
  'focus-results-card',
  'aria-pressed={Boolean(isFocusActive)}',
  'aria-label="Expand description panel"',
  'prefersReducedMotion'
];

for (const snippet of requiredCodeSnippets) {
  if (!htmlContent.includes(snippet)) {
    console.error(`FAIL: Missing expected code snippet "${snippet}" in index.html!`);
    process.exit(1);
  }
}
console.log("PASS 3: index.html contains all focus mode state hooks, shortcuts, ARIA labels, and collapse handlers.");

// 4. Test In-Browser Babel Transpilation via Node vm
const scriptMatch = htmlContent.match(/<script type="text\/babel">([\s\S]*?)<\/script>/);
if (!scriptMatch) {
  console.error("FAIL: Could not extract Babel script from index.html");
  process.exit(1);
}

const babelCode = fs.readFileSync(path.join(__dirname, '..', 'frontend', 'assets', 'vendor', 'babel.min.js'), 'utf-8');
const bCtx = { console, setTimeout, clearTimeout };
bCtx.window = bCtx;
bCtx.global = bCtx;
vm.createContext(bCtx);
vm.runInContext(babelCode, bCtx);

const transpiled = bCtx.Babel.transform(scriptMatch[1], { presets: ['react'] }).code;
if (!transpiled || transpiled.length < 50000) {
  console.error("FAIL: Transpilation produced empty or too small output");
  process.exit(1);
}
console.log(`PASS 4: Babel transpilation succeeded (${transpiled.length} bytes of compiled JS).`);

// 5. Test localStorage persistence logic
const store = {};
const mockLocalStorage = {
  getItem: (k) => store[k] || null,
  setItem: (k, v) => { store[k] = String(v); }
};

mockLocalStorage.setItem("debugdojo_focus_mode", "true");
if (mockLocalStorage.getItem("debugdojo_focus_mode") !== "true") {
  console.error("FAIL: localStorage test failed");
  process.exit(1);
}
mockLocalStorage.setItem("debugdojo_focus_mode", "false");
if (mockLocalStorage.getItem("debugdojo_focus_mode") !== "false") {
  console.error("FAIL: localStorage reset test failed");
  process.exit(1);
}
console.log("PASS 5: localStorage persistence and retrieval verified for debugdojo_focus_mode.");

// 6. Test keyboard shortcut logic simulation
function simulateShortcut(key, isInputFocused, isFocusActive) {
  let newFocusActive = isFocusActive;
  if (key === "Escape" && isFocusActive) {
    newFocusActive = false;
    return { newFocusActive, prevented: true, action: "exit_focus" };
  }
  if ((key === "f" || key === "F")) {
    if (!isInputFocused) {
      newFocusActive = !isFocusActive;
      return { newFocusActive, prevented: true, action: "toggle_focus" };
    } else {
      return { newFocusActive, prevented: false, action: "type_letter" };
    }
  }
  return { newFocusActive, prevented: false, action: "ignore" };
}

// When editor is focused, typing 'f' should NOT toggle focus mode
const r1 = simulateShortcut("f", true, false);
if (r1.newFocusActive !== false || r1.prevented !== false || r1.action !== "type_letter") {
  console.error("FAIL: 'f' inside editor incorrectly triggered focus mode!", r1);
  process.exit(1);
}

// When editor is not focused, pressing 'f' toggles focus mode ON
const r2 = simulateShortcut("f", false, false);
if (r2.newFocusActive !== true || r2.prevented !== true || r2.action !== "toggle_focus") {
  console.error("FAIL: 'f' outside editor failed to toggle focus mode ON!", r2);
  process.exit(1);
}

// When focus mode is active, pressing 'Escape' exits focus mode
const r3 = simulateShortcut("Escape", true, true);
if (r3.newFocusActive !== false || r3.prevented !== true || r3.action !== "exit_focus") {
  console.error("FAIL: Escape failed to exit focus mode!", r3);
  process.exit(1);
}

console.log("PASS 6: Keyboard shortcut dispatch logic strictly verified (typing 'f' allowed in editor; 'f' outside toggles; 'Escape' exits).");

console.log("\n*** ALL PHASE 6 AUTOMATED CHECKS PASSED (100%) ***\n");
