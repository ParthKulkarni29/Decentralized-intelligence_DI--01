const fs = require('fs');
const vm = require('vm');

const html = fs.readFileSync('frontend/index.html', 'utf8');
const scriptMatch = html.match(/<script type="text\/babel">([\s\S]*?)<\/script>/);
if (!scriptMatch) {
  console.error('FAIL: Could not find text/babel script tag!');
  process.exit(1);
}
console.log('Script size:', scriptMatch[1].length, 'characters');

const babelCode = fs.readFileSync('frontend/assets/vendor/babel.min.js', 'utf8');
const context = { console, setTimeout, clearTimeout, process };
context.window = context;
context.global = context;
vm.createContext(context);
vm.runInContext(babelCode, context);

if (!context.Babel || typeof context.Babel.transform !== 'function') {
  console.error('FAIL: Babel did not expose Babel.transform');
  process.exit(1);
}

console.log('Babel ready:', typeof context.Babel.transform);
try {
  const result = context.Babel.transform(scriptMatch[1], { presets: ['react'] });
  console.log('PASS: In-browser Babel transpilation succeeded with ZERO errors!');
  console.log('Transpiled output code size:', result.code.length, 'bytes');
  process.exit(0);
} catch (err) {
  console.error('FAIL: Transpilation error:', err);
  process.exit(1);
}
