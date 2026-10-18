"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const script = fs.readFileSync(path.join(__dirname, "..", "admin.js"), "utf8");
const startMarker = "function parseTopicChannels(raw) {";
const endMarker = "\nfunction applyTheme(theme, persist = false) {";
const start = script.indexOf(startMarker);
const end = script.indexOf(endMarker, start);

assert.notEqual(start, -1, "topic settings parser should exist");
assert.notEqual(end, -1, "topic settings parser should end before theme setup");

const sandbox = {};
vm.runInNewContext(
    script.slice(start, end) + "\nthis.parseTopicChannels = parseTopicChannels;",
    sandbox,
    { timeout: 1000 }
);

assert.deepEqual(JSON.parse(JSON.stringify(sandbox.parseTopicChannels('{"news":"@target"}'))), {
    news: "@target",
});
for (const malformed of ["null", "[]", "42", "true", '"text"', "not-json", ""]) {
    assert.deepEqual(
        JSON.parse(JSON.stringify(sandbox.parseTopicChannels(malformed))),
        {},
        `malformed topic settings should safely become an empty object: ${malformed}`
    );
}

console.log("Topic-channel UI parser regression tests passed (8 cases).");
