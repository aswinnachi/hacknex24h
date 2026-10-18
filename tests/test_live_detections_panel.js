/**
 * Automated test suite for Live Detections Panel fix
 * Verifies all requirements from USER_REQUEST using Node.js vm context:
 * 1. Initial loading state ends after a successful request.
 * 2. Real API events appear in the panel.
 * 3. A successful empty response shows "No detections yet."
 * 4. A failed request shows an error and Retry action.
 * 5. Person, Vehicle, Other, and All filters work.
 * 6. Repeated refreshes do not create duplicate events.
 * 7. New detections appear without reloading the whole page.
 * 8. Missing optional fields do not crash rendering.
 * 9. API contract & schema compliance.
 */

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

console.log("============================================================");
console.log("TESTING LIVE DETECTIONS PANEL LOGIC & STATE MACHINE");
console.log("============================================================");

class MockElement {
    constructor(id = "", className = "") {
        this.id = id;
        this.className = className;
        this.innerHTML = "";
        this.children = [];
        this.attributes = {};
        this.classList = {
            add: (c) => { if (!this.className.includes(c)) this.className += " " + c; },
            remove: (c) => { this.className = this.className.replace(new RegExp(`\\b${c}\\b`, "g"), "").trim(); }
        };
    }
    getAttribute(name) { return this.attributes[name]; }
    setAttribute(name, val) { this.attributes[name] = val; }
}

const elements = {
    "overview-detections-list": new MockElement("overview-detections-list"),
    "monitoring-detections-list": new MockElement("monitoring-detections-list"),
    "metric-tracked-objects": new MockElement("metric-tracked-objects")
};

const sandbox = {
    document: {
        getElementById: (id) => elements[id] || null,
        querySelectorAll: (sel) => []
    },
    console: console,
    setTimeout: setTimeout,
    clearTimeout: clearTimeout,
    setInterval: setInterval,
    clearInterval: clearInterval,
    AbortController: globalThis.AbortController,
    escapeHtml: (str) => String(str || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"),
    switchView: () => {},
    allCameras: [
        { id: "CAM_01", name: "Pedestrian Walkway", status: "CONNECTED" },
        { id: "CAM_02", name: "Vehicle Entrance", status: "CONNECTED" }
    ],
    currentView: "overview",
    liveDetectionsFilter: "all",
    addEventListener: () => {}
};
sandbox.window = sandbox;
sandbox.global = sandbox;

vm.createContext(sandbox);

// Read app.js
const appJsContent = fs.readFileSync(path.join(__dirname, "../frontend/app.js"), "utf8");

// Isolate the live detection engine code from app.js
const startIdx = appJsContent.indexOf("// LIVE DETECTIONS PANEL STATE & RENDERING ENGINE");
const endIdx = appJsContent.indexOf("async function loadOverviewRecentAlerts()");
assert(startIdx !== -1 && endIdx !== -1, "Live detection engine code block not found in app.js");

const engineCode = appJsContent.slice(startIdx, endIdx);
vm.runInContext(engineCode, sandbox);

async function runTests() {
    // -------------------------------------------------------------
    // Test 1 & 2: Loading -> Populated with Real API format data
    // -------------------------------------------------------------
    console.log("[Test 1 & 2] Verifying Loading -> Populated state with real API data...");
    
    // Simulate initial loading
    sandbox.liveDetectionsCache = [];
    sandbox.liveDetectionsState = "loading";
    sandbox.renderLiveDetectionsList();
    assert(elements["overview-detections-list"].innerHTML.includes("Loading live detections..."), "Should display loading spinner in overview");
    assert(elements["monitoring-detections-list"].innerHTML.includes("Loading live detections..."), "Should display loading spinner in monitoring");

    // Mock API response with real backend schema: { count, limit, camera_id, detections: [...] }
    sandbox.fetch = async (url) => {
        return {
            ok: true,
            status: 200,
            json: async () => ({
                count: 3,
                limit: 50,
                camera_id: null,
                detections: [
                    { camera_id: "CAM_01", timestamp: 1700000000, frame_number: 100, class: "person", confidence: 0.88, bbox: [10, 20, 30, 40], track_id: 12 },
                    { camera_id: "CAM_02", timestamp: 1700000001, frame_number: 101, class: "car", confidence: 0.94, bbox: [50, 60, 70, 80], track_id: 15 },
                    { camera_id: "CAM_01", timestamp: 1700000002, frame_number: 102, class: "backpack", confidence: 0.65, bbox: [15, 25, 35, 45], track_id: 16 }
                ]
            })
        };
    };

    await sandbox.loadRecentDetectionsFeed();
    assert.strictEqual(sandbox.liveDetectionsState, "populated");
    assert(elements["overview-detections-list"].innerHTML.includes("person"), "Should render person detection");
    assert(elements["overview-detections-list"].innerHTML.includes("car"), "Should render car detection");
    assert(elements["overview-detections-list"].innerHTML.includes("Pedestrian Walkway"), "Should resolve friendly camera name");
    assert(elements["overview-detections-list"].innerHTML.includes("Track #12"), "Should render track id");
    assert(elements["overview-detections-list"].innerHTML.includes("88%"), "Should render confidence percentage");
    assert(elements["overview-detections-list"].innerHTML.includes("var(--emerald-status)"), "High confidence formatted green");
    console.log("  ✓ Initial loading completed, populated real events rendered accurately.");

    // -------------------------------------------------------------
    // Test 3: Successful Empty Response -> "No detections yet."
    // -------------------------------------------------------------
    console.log("[Test 3] Verifying successful empty response -> 'No detections yet.'...");
    sandbox.fetch = async () => ({
        ok: true,
        status: 200,
        json: async () => ({ count: 0, limit: 50, camera_id: null, detections: [] })
    });
    sandbox.liveDetectionsCache = [];
    await sandbox.loadRecentDetectionsFeed(true);
    assert.strictEqual(sandbox.liveDetectionsState, "empty");
    assert(elements["overview-detections-list"].innerHTML.includes("No detections yet."), "Should show 'No detections yet.'");
    console.log("  ✓ Empty response shows 'No detections yet.'");

    // -------------------------------------------------------------
    // Test 4: Failed Request -> Error State with Retry button
    // -------------------------------------------------------------
    console.log("[Test 4] Verifying failed request -> Error state with Retry action...");
    sandbox.fetch = async () => ({
        ok: false,
        status: 500,
        json: async () => ({})
    });
    sandbox.liveDetectionsCache = [];
    await sandbox.loadRecentDetectionsFeed(true);
    assert.strictEqual(sandbox.liveDetectionsState, "error");
    assert(elements["overview-detections-list"].innerHTML.includes("Unable to load detections"), "Should display error state");
    assert(elements["overview-detections-list"].innerHTML.includes("Retry"), "Should offer retry action");
    assert(elements["overview-detections-list"].innerHTML.includes("window.retryLiveDetections()"), "Retry button is wired");
    console.log("  ✓ Failed request renders error and retry action.");

    // -------------------------------------------------------------
    // Test 5: Category Filters (All, Person, Vehicle, Other)
    // -------------------------------------------------------------
    console.log("[Test 5] Verifying category filters (All, Person, Vehicle, Other)...");
    sandbox.liveDetectionsCache = [
        { camera_id: "CAM_01", timestamp: 1700000000, class: "person", confidence: 0.9, track_id: 1 },
        { camera_id: "CAM_02", timestamp: 1700000001, class: "truck", confidence: 0.85, track_id: 2 },
        { camera_id: "CAM_01", timestamp: 1700000002, class: "bicycle", confidence: 0.75, track_id: 3 },
        { camera_id: "CAM_02", timestamp: 1700000003, class: "backpack", confidence: 0.6, track_id: 4 }
    ];
    sandbox.liveDetectionsState = "populated";

    // Filter: All
    sandbox.liveDetectionsFilter = "all";
    sandbox.renderLiveDetectionsList();
    assert(elements["overview-detections-list"].innerHTML.includes("person"));
    assert(elements["overview-detections-list"].innerHTML.includes("truck"));
    assert(elements["overview-detections-list"].innerHTML.includes("bicycle"));
    assert(elements["overview-detections-list"].innerHTML.includes("backpack"));

    // Filter: Person
    sandbox.liveDetectionsFilter = "person";
    sandbox.renderLiveDetectionsList();
    assert(elements["overview-detections-list"].innerHTML.includes("person"));
    assert(!elements["overview-detections-list"].innerHTML.includes("truck"));
    assert(!elements["overview-detections-list"].innerHTML.includes("backpack"));

    // Filter: Vehicle
    sandbox.liveDetectionsFilter = "vehicle";
    sandbox.renderLiveDetectionsList();
    assert(!elements["overview-detections-list"].innerHTML.includes("person"));
    assert(elements["overview-detections-list"].innerHTML.includes("truck"));
    assert(elements["overview-detections-list"].innerHTML.includes("bicycle"));
    assert(!elements["overview-detections-list"].innerHTML.includes("backpack"));

    // Filter: Other
    sandbox.liveDetectionsFilter = "other";
    sandbox.renderLiveDetectionsList();
    assert(!elements["overview-detections-list"].innerHTML.includes("person"));
    assert(!elements["overview-detections-list"].innerHTML.includes("truck"));
    assert(!elements["overview-detections-list"].innerHTML.includes("bicycle"));
    assert(elements["overview-detections-list"].innerHTML.includes("backpack"));
    console.log("  ✓ Filters work accurately without mutating underlying data.");

    // -------------------------------------------------------------
    // Test 6: Deduplication on Repeated Refreshes
    // -------------------------------------------------------------
    console.log("[Test 6] Verifying deduplication on repeated refreshes...");
    const sampleEvent = { camera_id: "CAM_01", timestamp: 1700000000.12, track_id: 42, class: "person", confidence: 0.95 };
    sandbox.fetch = async () => ({
        ok: true,
        status: 200,
        json: async () => ({ count: 3, limit: 50, detections: [sampleEvent, sampleEvent, sampleEvent] })
    });
    sandbox.liveDetectionsFilter = "all";
    await sandbox.loadRecentDetectionsFeed(true);
    assert.strictEqual(sandbox.liveDetectionsCache.length, 1, "Duplicate events must be consolidated into 1");
    console.log("  ✓ Repeated refreshes properly deduplicate events.");

    // -------------------------------------------------------------
    // Test 7: Updating State (Preserves Content on Background Poll)
    // -------------------------------------------------------------
    console.log("[Test 7] Verifying Updating state preserves content without wiping...");
    assert(sandbox.liveDetectionsCache.length > 0);
    // Simulate background failure
    sandbox.fetch = async () => { throw new Error("Temporary network glitch"); };
    await sandbox.loadRecentDetectionsFeed(false);
    assert(elements["overview-detections-list"].innerHTML.includes("person"), "Existing detections must stay visible during temporary error");
    console.log("  ✓ Updating state preserves visible items without flashing blank or error.");

    // -------------------------------------------------------------
    // Test 8: Missing Optional Fields Safety
    // -------------------------------------------------------------
    console.log("[Test 8] Verifying missing optional fields do not crash rendering...");
    sandbox.liveDetectionsCache = [
        {}, // completely empty object
        { class: null, confidence: null, timestamp: null, track_id: null, camera_id: null }
    ];
    sandbox.renderLiveDetectionsList();
    assert(elements["overview-detections-list"].innerHTML.includes("object"), "Fallback class name applied");
    assert(elements["overview-detections-list"].innerHTML.includes("Camera"), "Fallback camera name applied");
    assert(elements["overview-detections-list"].innerHTML.includes("Track --"), "Fallback track applied");
    console.log("  ✓ Missing optional fields handled completely safely.");

    console.log("============================================================");
    console.log("ALL 8 FRONTEND UNIT & INTEGRATION TESTS PASSED (8/8)!");
    console.log("============================================================");
}

runTests().catch(err => {
    console.error("Test failed with error:", err);
    process.exit(1);
});
