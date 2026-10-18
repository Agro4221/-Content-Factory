"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const html = fs.readFileSync(path.join(__dirname, "..", "admin.html"), "utf8");
const script = fs.readFileSync(path.join(__dirname, "..", "admin.js"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "..", "admin.css"), "utf8");

function extractFunction(name) {
    const escaped = name.replace(/[.*+?^$()|[\]\\]/g, "\\$&");
    const pattern = new RegExp("(?:async\\s+)?function " + escaped + "\\s*\\([^)]*\\)\\s*\\{[\\s\\S]*?\\n\\}", "m");
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
    assert.ok(css.includes(".route:not(.active) { display: none !important; }"), "inactive pages must be hidden");
    assert.ok(css.includes(".route.active { display: block; }"), "active page must be visible");
}

// Exercise the real route function with lightweight DOM elements. This catches
// runtime errors after the active page has been switched, not just click wiring.
{
    function makeElement(dataset) {
        const classes = new Set();
        return {
            dataset,
            classList: {
                toggle(name, enabled) {
                    if (enabled) classes.add(name);
                    else classes.delete(name);
                },
                contains(name) { return classes.has(name); },
            },
        };
    }

    const routeNames = [
        "posts/overview", "posts/dashboard", "posts/ai",
        "posts/parsing", "posts/proxy", "posts/logs", "posts/faq",
    ];
    const navItems = routeNames.map(route => makeElement({ route }));
    const sections = routeNames.map(route => makeElement({ route }));
    const groups = [makeElement({ module: "posts" })];
    const contentEl = { scrollTop: 123 };
    const sandbox = {
        state: {
            currentRoute: "posts/overview",
            expandedGroups: new Set(["posts"]),
            mobileDrawerOpen: false,
            splitMenuOpen: false,
        },
        DEFAULT_ROUTE: "posts/overview",
        NAV_CONFIG: {
            posts: {
                label: "Публикация постов",
                moduleColor: "posts",
                routes: Object.fromEntries(routeNames.map(route => [route, { title: route, crumb: route }])),
            },
        },
        document: {
            querySelectorAll(selector) {
                if (selector === ".nav-item") return navItems;
                if (selector === ".nav-group") return groups;
                if (selector === ".route") return sections;
                return [];
            },
            querySelector(selector) { return selector === ".content" ? contentEl : null; },
            getElementById() { return null; },
        },
        applyExpandedGroups() {},
        renderBreadcrumbs() {},
        syncWorkflowStep() {},
        renderModuleDashboard() {},
        renderModuleHealthForRoute() {},
        updateBotButton() {},
        saveState() { sandbox.saved = true; },
    };

    evaluateFunctions(["getRouteMeta", "closeSplitMenu", "navigate"], sandbox);
    assert.doesNotThrow(() => sandbox.navigate("posts/ai"));
    assert.equal(sandbox.state.currentRoute, "posts/ai");
    assert.equal(sections.filter(section => section.classList.contains("active")).length, 1);
    assert.equal(sections[2].classList.contains("active"), true);
    assert.equal(navItems[2].classList.contains("active"), true);
    assert.equal(sandbox.saved, true, "route should finish and persist state");
    assert.equal(contentEl.scrollTop, 0, "navigation should reset scroll position");
}

async function testLocalAiModelUi() {
    // A custom remote host must never trigger a hidden server-side URL fetch.
    {
        const elements = {
            local_ai_provider: { value: "ollama" },
            local_model: { value: "qwen2.5:7b" },
            localModelOptions: { textContent: "", options: [], appendChild(option) { this.options.push(option); } },
            local_host: { value: "192.168.1.20" },
            local_port: { value: "11434" },
            refreshLocalModelsBtn: { disabled: false },
            localModelStatus: { textContent: "", dataset: {} },
        };
        let requests = 0;
        const sandbox = {
            document: {
                getElementById(id) { return elements[id] || null; },
                createElement() { return { value: "", textContent: "" }; },
            },
            fetch: async () => { requests += 1; throw new Error("unexpected request"); },
            _localAiModelRequest: 0,
        };
        evaluateFunctions(["localAiDefaultPort", "setLocalAiModelStatus", "refreshLocalAiModels"], sandbox);
        await sandbox.refreshLocalAiModels();
        assert.equal(requests, 0, "custom remote host must not be queried by the model-discovery API");
        assert.match(elements.localModelStatus.textContent, /удалённого хоста/i);
    }

    // The standard local provider populates model suggestions and preserves the saved model.
    {
        const elements = {
            local_ai_provider: { value: "ollama" },
            local_model: { value: "qwen2.5:7b" },
            localModelOptions: { textContent: "", options: [], appendChild(option) { this.options.push(option); } },
            local_host: { value: "127.0.0.1" },
            local_port: { value: "11434" },
            refreshLocalModelsBtn: { disabled: false },
            localModelStatus: { textContent: "", dataset: {} },
        };
        let requestUrl = "";
        const sandbox = {
            document: {
                getElementById(id) { return elements[id] || null; },
                createElement() { return { value: "", textContent: "" }; },
            },
            fetch: async url => {
                requestUrl = url;
                return {
                    ok: true,
                    json: async () => ({ models: ["qwen2.5:7b", "llama3.2:3b"] }),
                };
            },
            _localAiModelRequest: 0,
        };
        evaluateFunctions(["localAiDefaultPort", "setLocalAiModelStatus", "refreshLocalAiModels"], sandbox);
        await sandbox.refreshLocalAiModels();
        assert.equal(requestUrl, "/api/ai/models?provider=ollama");
        assert.deepEqual(elements.localModelOptions.options.map(option => option.value), ["qwen2.5:7b", "llama3.2:3b"]);
        assert.equal(elements.local_model.value, "qwen2.5:7b");
        assert.equal(elements.localModelStatus.dataset.state, "success");
        assert.equal(elements.refreshLocalModelsBtn.disabled, false);
    }

    // Switching providers changes a standard default port but preserves a custom endpoint.
    {
        const host = { value: "127.0.0.1" };
        const port = { value: "1234" };
        const sandbox = {
            document: { getElementById(id) { return id === "local_host" ? host : (id === "local_port" ? port : null); } },
        };
        evaluateFunctions(["localAiDefaultPort", "setLocalAiProviderFields"], sandbox);
        sandbox.setLocalAiProviderFields("ollama", "lmstudio");
        assert.equal(port.value, "11434");

        host.value = "192.168.1.20";
        port.value = "5050";
        sandbox.setLocalAiProviderFields("lmstudio", "ollama");
        assert.equal(host.value, "192.168.1.20");
        assert.equal(port.value, "5050");
    }
}

testLocalAiModelUi()
    .then(() => console.log("Admin startup/navigation and local AI UI regressions passed (8 groups)."))
    .catch(error => {
        console.error(error);
        process.exitCode = 1;
    });
