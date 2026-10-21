"use strict";

// Проверка разбора и сбора маршрутов «тема → канал» в админ-панели
// Content Factory. Панель самодостаточна, поэтому логика извлекается
// из admin.html.

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const html = fs.readFileSync(path.join(__dirname, "..", "admin.html"), "utf8");
// Основная логика панели — в самом крупном блоке <script>.
const scriptBlocks = [...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)].map(m => m[1]);
assert.ok(scriptBlocks.length > 0, "inline script should exist in admin.html");
const script = scriptBlocks.reduce((a, b) => (b.length > a.length ? b : a));

function extractFunction(name) {
    const escaped = name.replace(/[.*+?^$()|[\]\\]/g, "\\$&");
    const pattern = new RegExp("(?:async\\s+)?function\\s+" + escaped + "\\s*\\([^)]*\\)\\s*\\{[\\s\\S]*?\\n\\}");
    const match = script.match(pattern);
    assert.ok(match, "function " + name + " should exist");
    return match[0];
}

function makeRow(name, chat) {
    return {
        querySelector(selector) {
            if (selector === ".topic-name") return { value: name };
            if (selector === ".topic-chat") return { value: chat };
            return null;
        },
    };
}

// Разбор сохранённого JSON с маршрутами.
{
    const sandbox = { JSON };
    vm.createContext(sandbox);
    vm.runInContext(
        extractFunction("parseTopics") + "\nthis.parseTopics = parseTopics;",
        sandbox,
        { timeout: 1000 }
    );
    const parse = sandbox.parseTopics;

    assert.deepEqual(
        JSON.parse(JSON.stringify(parse('{"news":"@target"}'))),
        { news: "@target" },
        "valid topic mapping should be parsed"
    );
    for (const malformed of ["null", "[]", "42", "true", '"text"', "not-json", ""]) {
        assert.deepEqual(
            JSON.parse(JSON.stringify(parse(malformed))),
            {},
            `malformed topic settings should safely become an empty object: ${malformed}`
        );
    }
}

// Сбор маршрутов из строк формы: неполные и пустые строки игнорируются,
// имя темы приводится к нижнему регистру.
{
    const rows = [
        makeRow("Tech", "@tech_channel"),
        makeRow("news", "@news_channel"),
        makeRow("", "@orphan_channel"),
        makeRow("nochat", ""),
        makeRow("  ", "@blank_channel"),
    ];
    const sandbox = {
        JSON,
        document: {
            querySelectorAll(selector) {
                return selector === "#topics-list .route-row" ? rows : [];
            },
        },
    };
    vm.createContext(sandbox);
    vm.runInContext(
        extractFunction("collectTopics") + "\nthis.collectTopics = collectTopics;",
        sandbox,
        { timeout: 1000 }
    );
    const collected = JSON.parse(JSON.stringify(sandbox.collectTopics()));
    assert.deepEqual(collected, { tech: "@tech_channel", news: "@news_channel" });
}

// Панель не должна вшивать значения маршрутов в разметку.
{
    assert.ok(!/topic_channels"\s*value=/.test(html), "no topic mapping value may be embedded in markup");
}

console.log("Topic-channel UI regression tests passed (10 cases).");
