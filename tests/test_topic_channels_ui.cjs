"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const html = fs.readFileSync(path.join(__dirname, "..", "admin.html"), "utf8");
const startMarker = "function parseTopicChannels(raw) {";
const endMarker = "\nfunction buildOnboardState(settings, licenseActive) {";
const start = html.indexOf(startMarker);
const end = html.indexOf(endMarker, start);

assert.notEqual(start, -1, "topic settings parser should exist");
assert.notEqual(end, -1, "topic settings parser should end before onboarding state");

const sandbox = {};
vm.runInNewContext(
    html.slice(start, end) + "\nthis.parseTopicChannels = parseTopicChannels;",
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
