// Rebuilds gamedashboard/index.html as a single, fully self-contained,
// offline HTML file: inlines React + ReactDOM (UMD production builds),
// the Pretendard webfont (3 weights, base64), and the app source
// (src/app.jsx, pre-compiled from JSX with Babel).
//
// Usage:
//   cd gamedashboard
//   npm install --no-save react@18 react-dom@18 @babel/core@7 @babel/preset-react@7 pretendard@1
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

const pretendardDir = path.join(
  path.dirname(require.resolve("pretendard/package.json")),
  "dist/web/static/woff2"
);
const fontFace = (weight, file) => {
  const b64 = fs
    .readFileSync(path.join(pretendardDir, file))
    .toString("base64");
  return `@font-face{font-family:'Pretendard';font-weight:${weight};font-display:swap;
  src:url(data:font/woff2;base64,${b64}) format('woff2');}`;
};
const fonts = [
  fontFace(400, "Pretendard-Regular.woff2"),
  fontFace(700, "Pretendard-Bold.woff2"),
  fontFace(900, "Pretendard-Black.woff2"),
].join("\n");

const { code: app } = babel.transform(jsx, {
  presets: ["@babel/preset-react"],
  filename: "app.jsx",
});

const html = `<title>썸머탈출 페스티벌</title>
<style>
${fonts}
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
