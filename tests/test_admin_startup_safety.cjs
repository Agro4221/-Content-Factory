"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const html = fs.readFileSync(path.join(__dirname, "..", "admin.html"), "utf8");
const scriptMatch = html.match(/<script\b[^>]*>([\s\S]*?)<\/script\s*>/i);
assert.ok(scriptMatch, "admin panel should have its controller script");
const script = scriptMatch[1];

function extractFunction(name) {
    const escaped = name.replace(/[.*+?^$()|[\]\\]/g, "\\$&");
    const pattern = new RegExp("function " + escaped + "\\s*\\([^)]*\\)\\s*\\{[\\s\\S]*?\\n\\}", "m");
    const match = script.match(pattern);
    assert.ok(match, "function " + name + " should exist");
    return match[0];
}

function evaluateFunctions(names, sandbox) {
    const source = names.map(extractFunction).join("\n\n");
    const exports = names.map(name => "this." + name + " = " + name + ";").join("\n");
    vm.runInNewContext(source + "\n" + exports, sandbox, { timeout: 1000 });
}

function inertDocument() {
    return {
        getElementById() { return null; },
        querySelectorAll() { return []; },
        querySelector() { return null; },
        addEventListener() {},
    };
}

// The previous UI referenced an undeclared splitMenu variable here, throwing
// during DOMContentLoaded and aborting all subsequent page initialization.
{
    const sandbox = { document: inertDocument() };
    evaluateFunctions(["setupBotButton"], sandbox);
    assert.doesNotThrow(() => sandbox.setupBotButton());
}

// This UI no longer contains .workflow-step elements. Initialization must be
// a no-op instead of referencing the deleted global order.
{
    const sandbox = {
        document: inertDocument(),
        state: { currentRoute: "posts/overview" },
    };
    evaluateFunctions(["setupWorkflow", "syncWorkflowStep"], sandbox);
    assert.doesNotThrow(() => sandbox.setupWorkflow());
    assert.doesNotThrow(() => sandbox.syncWorkflowStep());
}

// Test the actual sidebar event delegation: clicking an item must request its
// route, rather than leaving the UI on the initial dashboard.
{
    const handlers = {};
    const item = { dataset: { route: "posts/ai" } };
    const nav = {
        addEventListener(type, callback) { handlers[type] = callback; },
        contains(node) { return node === item; },
    };
    const eventTarget = {
        closest(selector) { return selector === ".nav-item" ? item : null; },
    };
    const sandbox = {
        document: {
            querySelector() { return null; },
            getElementById(id) { return id === "sidebarNav" ? nav : null; },
        },
        state: { expandedGroups: new Set(["posts"]), currentRoute: "posts/overview", mobileDrawerOpen: false },
        navigate(route) { sandbox.requestedRoute = route; },
        applyExpandedGroups() {},
        saveState() {},
    };
    evaluateFunctions(["setupSidebar"], sandbox);
    sandbox.setupSidebar();
    assert.equal(typeof handlers.click, "function", "sidebar click handler must be registered");
    handlers.click({
        target: eventTarget,
        preventDefault() {},
    });
    assert.equal(sandbox.requestedRoute, "posts/ai", "clicking AI settings must navigate to posts/ai");
}

// Every sidebar link has a matching page section, and the app no longer adds a
// second inline click handler that could start/stop the parser twice.
{
    const sidebarHtml = html.split('<div class="content">')[0];
    const navRoutes = [...sidebarHtml.matchAll(/class="nav-item"[^>]*data-route="([^"]+)"/g)].map(m => m[1]);
    const sectionRoutes = [...html.matchAll(/<section\b[^>]*class="[^"]*\broute\b[^"]*"[^>]*data-route="([^"]+)"/g)].map(m => m[1]);
    assert.ok(navRoutes.includes("posts/overview"), "overview should be reachable from the sidebar");
    assert.ok(navRoutes.includes("posts/proxy"), "proxy settings should be reachable from the sidebar");
    for (const route of navRoutes) {
        assert.ok(sectionRoutes.includes(route), "sidebar route " + route + " must have a matching section");
    }
    const toggleButton = html.match(/<button\b[^>]*id="botToggleBtn"[^>]*>/);
    assert.ok(toggleButton, "top-bar start/stop button should exist");
    assert.ok(!/\bonclick\s*=/.test(toggleButton[0]), "start/stop action must have only one event handler");
    assert.ok(html.includes(".route:not(.active) { display: none !important; }"), "inactive pages must be hidden");
    assert.ok(html.includes(".route.active { display: block; }"), "active page must be visible");
}

console.log("Admin startup/navigation regression tests passed (4 groups).");
