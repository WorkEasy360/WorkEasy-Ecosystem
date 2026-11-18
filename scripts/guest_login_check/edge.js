// A stand-in for the production edge (deploy/edge/Caddyfile) for this check only:
// every portal host on one port, `/api/*` to the backend and everything else to the
// frontend, with the Host header kept - which is what the edge does and what the
// backend's per-host rules, the frontend's per-host CSP and the host-only cookies
// depend on. Plain http; no dependency.
//
//   node scripts/guest_login_check/edge.js <listen> <backend port> <frontend port>

const http = require("node:http");

const [listen, api, web] = process.argv.slice(2).map(Number);

http
  .createServer((req, res) => {
    const toApi = req.url.startsWith("/api/") || req.url.startsWith("/healthz");
    const upstream = http.request(
      { host: "127.0.0.1", port: toApi ? api : web, method: req.method, path: req.url, headers: req.headers },
      (answer) => {
        res.writeHead(answer.statusCode ?? 502, answer.headers);
        answer.pipe(res);
      },
    );
    upstream.on("error", (err) => {
      // An upstream that stops mid-answer can only be cut off, not answered again.
      if (res.headersSent) return res.destroy(err);
      res.writeHead(502, { "content-type": "text/plain" });
      res.end(String(err));
    });
    req.on("error", () => upstream.destroy());
    res.on("error", () => upstream.destroy());
    req.pipe(upstream);
  })
  .listen(listen, "127.0.0.1", () => console.log(`edge listening on 127.0.0.1:${listen}`));
