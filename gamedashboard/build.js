// Rebuilds gamedashboard/index.html as a single, fully self-contained,
// offline HTML file: inlines React + ReactDOM (UMD production builds)
// and the app source (src/app.jsx, pre-compiled from JSX with Babel).
//
// Usage:
//   cd gamedashboard
//   npm install --no-save react@18 react-dom@18 @babel/core@7 @babel/preset-react@7
//   node build.js
//
// Output: gamedashboard/index.html (no external requests at runtime).

const fs = require("fs");
const path = require("path");
const babel = require("@babel/core");

const dir = __dirname;
const style = fs.readFileSync(path.join(dir, "src/style.css"), "utf8");
const jsx = fs.readFileSync(path.join(dir, "src/app.jsx"), "utf8");
const reactDir = path.dirname(require.resolve("react/package.json"));
const reactDomDir = path.dirname(require.resolve("react-dom/package.json"));
const react = fs.readFileSync(
  path.join(reactDir, "umd/react.production.min.js"),
  "utf8"
);
const reactDom = fs.readFileSync(
  path.join(reactDomDir, "umd/react-dom.production.min.js"),
  "utf8"
);

const { code: app } = babel.transform(jsx, {
  presets: ["@babel/preset-react"],
  filename: "app.jsx",
});

const html = `<title>썸머탈출 페스티벌</title>
<style>
${style}</style>

<div id="root"></div>

<script>
/* React 18 (production, bundled offline — no network required) */
${react}
</script>
<script>
/* ReactDOM 18 (production, bundled offline — no network required) */
${reactDom}
</script>
<script>
${app}
</script>
`;

fs.writeFileSync(path.join(dir, "index.html"), html);
console.log(`Wrote index.html (${html.length} bytes)`);
