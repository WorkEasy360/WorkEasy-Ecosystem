// A stand-in for the production edge (deploy/edge/Caddyfile) for the Store check
// only (ADR-065): every portal host on one port, the Host header kept, `/api/*` to
// the backend and everything else to the frontend - and, as the Caddyfile's Store
// site does, the Store host's `/sitemap.xml` to the API's sitemap. Plain http; no
// dependency.
//
//   node scripts/store_domain_check/edge.js <listen> <backend port> <frontend port>

const http = require("node:http");

const [listen, api, web] = process.argv.slice(2).map(Number);

http
  .createServer((req, res) => {
    const host = String(req.headers.host || "").split(":")[0];
    let path = req.url;
    if (host === "store.workeasy360.com" && path === "/sitemap.xml") path = "/api/v1/public/store/sitemap.xml";
    const toApi = path.startsWith("/api/") || path.startsWith("/healthz");
    const upstream = http.request(
      { host: "127.0.0.1", port: toApi ? api : web, method: req.method, path, headers: req.headers },
      (answer) => {
        res.writeHead(answer.statusCode ?? 502, answer.headers);
        answer.pipe(res);
      },
    );
    upstream.on("error", (err) => {
      if (res.headersSent) return res.destroy(err);
      res.writeHead(502, { "content-type": "text/plain" });
      res.end(String(err));
    });
    req.on("error", () => upstream.destroy());
    res.on("error", () => upstream.destroy());
    req.pipe(upstream);
  })
  .listen(listen, "127.0.0.1", () => console.log(`edge listening on 127.0.0.1:${listen}`));
