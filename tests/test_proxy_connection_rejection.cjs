"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const adminPath = path.join(__dirname, "..", "admin.html");
const html = fs.readFileSync(adminPath, "utf8");
const startMarker = "async function testProxyConnection(data) {";
const endMarker = "\nfunction setupProxyPillNavigation()";
const start = html.indexOf(startMarker);
const end = html.indexOf(endMarker, start);

assert.notEqual(start, -1, "proxy connection helper should exist");
assert.notEqual(end, -1, "proxy connection helper should end before navigation setup");

const helperSource = html.slice(start, end).trim();

function callProxyCheck(fetchImpl) {
    const sandbox = { fetch: fetchImpl, JSON };
    vm.runInNewContext(
        helperSource + "\nthis.__testProxyConnection = testProxyConnection;",
        sandbox,
        { timeout: 1000 }
    );
    return sandbox.__testProxyConnection({
        proxy_type: "HTTP",
        proxy_host: "proxy.example",
        proxy_port: "8080",
    });
}

async function main() {
    let requestUrl = "";
    const networkFailure = await callProxyCheck(async (url) => {
        requestUrl = url;
        throw new TypeError("Failed to fetch");
    });

    assert.equal(requestUrl, "/api/proxy/test");
    assert.equal(networkFailure.reachable, false);
    assert.equal(networkFailure.state, "error");
    assert.match(networkFailure.message, /Не удалось проверить соединение с прокси/);

    const serverFailure = await callProxyCheck(async () => ({
        ok: false,
        status: 502,
        json: async () => ({ detail: "Upstream proxy failed" }),
    }));
    assert.equal(serverFailure.reachable, false);
    assert.equal(serverFailure.message, "Upstream proxy failed");

    const success = await callProxyCheck(async () => ({
        ok: true,
        status: 200,
        json: async () => ({ ok: true, reachable: true, state: "reachable" }),
    }));
    assert.equal(success.reachable, true);
    assert.equal(success.state, "reachable");

    console.log("Proxy connection UI regression tests passed (3 cases).");
}

main().catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
