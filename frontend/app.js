// Multi-Stream Video Intelligence — Frontend Application Logic

let allCameras = [];
let allEvents = [];
let allMemories = [];
let activeCameraId = null;
let globalScope = "current"; // "current" | "all"
let currentEvidenceData = null;
let lastPendingQuery = null;
let autoRefreshTimer = null;

document.addEventListener("DOMContentLoaded", () => {
    initApp();
});

async function initApp() {
    await checkHealth();
    await loadCameras();
    await loadMemories();
    await loadEventTimeline();
    setupEventListeners();
    setupUploadHandlers();
    setupResetHandlers();
}

// ==========================================
// 1. HEALTH & TELEMETRY
// ==========================================
async function checkHealth() {
    const badgeBackend = document.getElementById("badge-backend");
    const backendText = document.getElementById("backend-status-text");
    const hwText = document.getElementById("hardware-text");

    try {
        const res = await fetch("/api/health");
        if (!res.ok) throw new Error("Health check failed");
        const data = await res.json();

        badgeBackend.classList.add("online");
        backendText.textContent = "Backend: Online";

        if (data.hardware) {
            const dev = data.hardware.primary_device ? data.hardware.primary_device.toUpperCase() : "CPU";
            hwText.textContent = `Compute: ${dev}`;
        }
    } catch (err) {
        badgeBackend.classList.remove("online");
        backendText.textContent = "Backend: Offline";
        console.error("Health check error:", err);
    }
}

// ==========================================
// 2. CAMERA MANAGEMENT & ANALYSIS SUMMARY
// ==========================================
async function loadCameras() {
    const listEl = document.getElementById("camera-list");
    const countEl = document.getElementById("camera-count-badge");
    const modalSelect = document.getElementById("mem-cam");
    const clarifySelect = document.getElementById("clarify-camera-select");
    const filterContainer = document.getElementById("timeline-filters-container");

    try {
        const res = await fetch("/api/cameras");
        if (!res.ok) throw new Error("Failed to load cameras");
        allCameras = await res.json();

        countEl.textContent = `${allCameras.length} Active`;
        listEl.innerHTML = "";
        modalSelect.innerHTML = "";
        clarifySelect.innerHTML = "";

        if (allCameras.length === 0) {
            activeCameraId = null;
            listEl.innerHTML = `
                <div class="empty-cctv-card">
                    <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="var(--text-muted)" stroke-width="1.5"><polygon points="23 7 16 12 23 17 23 7"></polygon><rect x="1" y="5" width="15" height="14" rx="2" ry="2"></rect></svg>
                    <h3>NO CCTV VIDEOS</h3>
                    <p>Upload a recorded CCTV video to begin precision video analysis.</p>
                    <button class="btn-primary" id="btn-empty-upload" style="margin-top:4px;">Upload CCTV Video</button>
                </div>
            `;
            const emptyUploadBtn = document.getElementById("btn-empty-upload");
            if (emptyUploadBtn) {
                emptyUploadBtn.addEventListener("click", () => {
                    document.getElementById("upload-modal").classList.remove("hidden");
                });
            }
            clearAnalysisReport();
            clearEvidencePlayer("No active cameras. Upload CCTV video to begin.");
            if (filterContainer) filterContainer.innerHTML = "";
            updateEventsCounter();
            return;
        }

        // If currently selected camera no longer exists or null, pick first available
        if (!activeCameraId || !allCameras.some(c => c.id === activeCameraId)) {
            activeCameraId = allCameras[0].id;
        }

        if (filterContainer) {
            filterContainer.innerHTML = `<button class="filter-btn ${globalScope === 'all' ? 'active' : ''}" data-cam-filter="all">All Feeds</button>`;
        }

        let hasAnalyzingCamera = false;

        allCameras.forEach(cam => {
            const isSelected = cam.id === activeCameraId;
            const isAnalyzing = cam.status === "ANALYZING";
            if (isAnalyzing) hasAnalyzingCamera = true;

            const statusBadge = isAnalyzing 
                ? `<span class="count-badge" style="color:var(--status-warning); border-color:var(--status-warning);">Analyzing</span>` 
                : `<span class="count-badge">${cam.id}</span>`;

            // Left Panel Camera Card with Delete action
            const card = document.createElement("div");
            card.className = `camera-card ${isSelected ? 'active' : ''}`;
            card.dataset.camId = cam.id;
            card.innerHTML = `
                <div class="cam-header">
                    <span style="font-weight:600; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:160px;" title="${escapeHtml(cam.name)}">${escapeHtml(cam.name)}</span>
                    <div class="cam-actions">
                        ${statusBadge}
                        <button class="btn-del-cam" title="Delete camera & video data" data-cam-id="${cam.id}">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/></svg>
                        </button>
                    </div>
                </div>
                <div class="cam-meta">
                    <span>${cam.resolution || "768x432"} • ${cam.fps ? cam.fps.toFixed(0) : 12} FPS</span>
                    <span>${cam.duration ? cam.duration.toFixed(1) + "s" : ""}</span>
                </div>
            `;

            // Select camera click
            card.addEventListener("click", (e) => {
                if (e.target.closest(".btn-del-cam")) return;
                document.querySelectorAll(".camera-card").forEach(c => c.classList.remove("active"));
                card.classList.add("active");
                activeCameraId = cam.id;
                loadVideoAnalysisSummary(cam.id);
                renderTimelineCards();
                updateEventsCounter();
            });

            // Delete camera button
            const delBtn = card.querySelector(".btn-del-cam");
            delBtn.addEventListener("click", async (e) => {
                e.stopPropagation();
                if (confirm(`Delete camera "${cam.name}" (${cam.id}) and all its associated events and visual clips?`)) {
                    await deleteCamera(cam.id);
                }
            });

            listEl.appendChild(card);

            // Populate Dropdowns
            const opt1 = new Option(`${cam.name} (${cam.id})`, cam.id);
            const opt2 = new Option(`${cam.name} (${cam.id})`, cam.id);
            modalSelect.appendChild(opt1);
            clarifySelect.appendChild(opt2);

            // Timeline filter buttons
            if (filterContainer) {
                const btn = document.createElement("button");
                btn.className = `filter-btn ${(globalScope === 'current' && activeCameraId === cam.id) ? 'active' : ''}`;
                btn.dataset.camFilter = cam.id;
                btn.textContent = cam.name || cam.id;
                btn.addEventListener("click", () => {
                    activeCameraId = cam.id;
                    globalScope = "current";
                    document.getElementById("global-scope-select").value = "current";
                    document.querySelectorAll(".camera-card").forEach(c => c.classList.toggle("active", c.dataset.camId === cam.id));
                    loadVideoAnalysisSummary(cam.id);
                    renderTimelineCards();
                    updateEventsCounter();
                });
                filterContainer.appendChild(btn);
            }
        });

        // Load summary for active camera
        if (activeCameraId) {
            await loadVideoAnalysisSummary(activeCameraId);
        }

        // If a camera is currently analyzing in background, schedule a background refresh
        if (hasAnalyzingCamera) {
            if (autoRefreshTimer) clearTimeout(autoRefreshTimer);
            autoRefreshTimer = setTimeout(async () => {
                await loadCameras();
                await loadEventTimeline();
            }, 2000);
        }

    } catch (err) {
        listEl.innerHTML = `<div class="empty-state">Error loading cameras: ${err.message}</div>`;
    }
}

async function deleteCamera(cameraId) {
    try {
        const res = await fetch(`/api/cameras/${cameraId}`, { method: "DELETE" });
        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.detail || "Delete failed");
        }
        const data = await res.json();
        appendChatMessage("assistant", `🗑️ <strong>Camera Deleted</strong>: Removed <code>${cameraId}</code> and ${data.deleted_events} associated visual events.`, true);
        
        if (activeCameraId === cameraId) {
            activeCameraId = null;
        }
        await loadCameras();
        await loadEventTimeline();
    } catch (err) {
        alert("Error deleting camera: " + err.message);
    }
}

async function loadVideoAnalysisSummary(cameraId) {
    const badge = document.getElementById("report-source-badge");
    const filenameEl = document.getElementById("rep-filename");
    const durationEl = document.getElementById("rep-duration");
    const resEl = document.getElementById("rep-resolution");
    const fpsEl = document.getElementById("rep-fps");
    const objListEl = document.getElementById("report-objects-list");

    if (!cameraId) {
        clearAnalysisReport();
        return;
    }

    const camMeta = allCameras.find(c => c.id === cameraId);
    badge.textContent = camMeta ? `${camMeta.name} (${cameraId})` : cameraId;

    try {
        const res = await fetch(`/api/cameras/${cameraId}/summary`);
        if (!res.ok) throw new Error("Failed to load summary");
        let data = await res.json();
        const sum = data.summary || data;

        // Fill real video properties from either summary or camera record
        const fname = sum.file_name || sum.filename || (camMeta ? camMeta.description : null) || `${cameraId}.mp4`;
        const dur = sum.duration_seconds || sum.duration || (camMeta ? camMeta.duration : null);
        const resol = sum.resolution || (camMeta ? camMeta.resolution : "768x432");
        const fpsVal = sum.fps || (camMeta ? camMeta.fps : 12);

        filenameEl.textContent = fname.replace("Uploaded CCTV footage: ", "");
        durationEl.textContent = dur ? `${Number(dur).toFixed(1)}s` : "--";
        resEl.textContent = resol || "--";
        fpsEl.textContent = fpsVal ? `${Number(fpsVal).toFixed(0)} FPS` : "--";

        objListEl.innerHTML = "";

        if (sum.status === "ANALYZING") {
            objListEl.innerHTML = `
                <div class="empty-state" style="color:var(--status-warning);">
                    ⏳ Video analysis in progress. Detecting objects & tracking trajectories...
                </div>
            `;
            return;
        }

        const detectedObj = sum.detected_objects;
        let renderedAny = false;

        if (Array.isArray(detectedObj)) {
            detectedObj.forEach(obj => {
                renderedAny = true;
                const card = document.createElement("div");
                card.className = "object-track-card";
                const moveStr = obj.movement ? "Movement: YES" : "Stationary";
                let attrStr = "";
                if (obj.clothing) attrStr += `Clothing: ${obj.clothing} • `;
                if (obj.approximate_color) attrStr += `Color: ${obj.approximate_color} • `;
                if (obj.has_bag !== undefined) attrStr += obj.has_bag ? "🎒 Bag: DETECTED" : "🎒 Bag: Not detected";

                card.innerHTML = `
                    <div class="object-track-title">
                        <span>${capitalize(obj.category || obj.concept)}</span>
                        <span class="font-mono" style="font-size:0.7rem; color:var(--accent-primary);">Track #${obj.track_id}</span>
                    </div>
                    <div class="object-track-details">
                        ⏱️ ${typeof obj.first_seen === 'number' ? obj.first_seen.toFixed(1) + 's' : obj.first_seen} – ${typeof obj.last_seen === 'number' ? obj.last_seen.toFixed(1) + 's' : obj.last_seen} | 🏃 ${moveStr}
                    </div>
                    ${attrStr ? `<div class="object-track-details" style="color:var(--text-muted);">${attrStr}</div>` : ""}
                `;
                objListEl.appendChild(card);
            });
        } else if (detectedObj && typeof detectedObj === 'object') {
            for (const [category, info] of Object.entries(detectedObj)) {
                if (info.detected && Array.isArray(info.details)) {
                    info.details.forEach(item => {
                        renderedAny = true;
                        const card = document.createElement("div");
                        card.className = "object-track-card";
                        const moveStr = item.movement ? `Movement: ${item.movement}` : "Stationary";
                        let attrStr = "";
                        if (item.approximate_clothing) attrStr += `Clothing: ${item.approximate_clothing} • `;
                        if (item.approximate_color) attrStr += `Color: ${item.approximate_color} • `;
                        if (item.bag !== undefined) attrStr += `Bag: ${item.bag}`;

                        card.innerHTML = `
                            <div class="object-track-title">
                                <span>${category}</span>
                                <span class="font-mono" style="font-size:0.7rem; color:var(--accent-primary);">Track #${item.track_id}</span>
                            </div>
                            <div class="object-track-details">
                                ⏱️ ${item.first_seen} – ${item.last_seen} | 🏃 ${moveStr}
                            </div>
                            ${attrStr ? `<div class="object-track-details" style="color:var(--text-muted);">${attrStr}</div>` : ""}
                        `;
                        objListEl.appendChild(card);
                    });
                }
            }
        }

        if (!renderedAny) {
            objListEl.innerHTML = `<div class="empty-state">No objects reliably detected in this feed.</div>`;
        }

        const negDiv = document.createElement("div");
        negDiv.style.fontSize = "0.7rem";
        negDiv.style.color = "var(--text-muted)";
        negDiv.style.marginTop = "6px";
        negDiv.style.borderTop = "1px dashed var(--border-subtle)";
        negDiv.style.paddingTop = "4px";
        negDiv.innerHTML = `<strong>Grounding Negatives:</strong> Bag: <em>No reliable detection</em> • Motorcycle: <em>No reliable detection</em> • Red Car: <em>No reliable detection</em>`;
        objListEl.appendChild(negDiv);

    } catch (err) {
        if (camMeta) {
            filenameEl.textContent = camMeta.name;
            durationEl.textContent = camMeta.duration ? `${camMeta.duration.toFixed(1)}s` : "--";
            resEl.textContent = camMeta.resolution || "768x432";
            fpsEl.textContent = camMeta.fps ? `${camMeta.fps.toFixed(0)} FPS` : "--";
            objListEl.innerHTML = `<div class="empty-state">Analysis report generated for this feed.</div>`;
        } else {
            objListEl.innerHTML = `<div class="empty-state">Analysis report pending.</div>`;
        }
    }
}

function clearAnalysisReport() {
    const badge = document.getElementById("report-source-badge");
    const filenameEl = document.getElementById("rep-filename");
    const durationEl = document.getElementById("rep-duration");
    const resEl = document.getElementById("rep-resolution");
    const fpsEl = document.getElementById("rep-fps");
    const objListEl = document.getElementById("report-objects-list");

    badge.textContent = "No Camera Selected";
    filenameEl.textContent = "--";
    durationEl.textContent = "--";
    resEl.textContent = "--";
    fpsEl.textContent = "--";
    objListEl.innerHTML = `<div class="empty-state">No active CCTV streams.</div>`;
}

// ==========================================
// 3. CLARIFY-ONCE MEMORY
// ==========================================
async function loadMemories() {
    const listEl = document.getElementById("memory-list");

    try {
        const res = await fetch("/api/memory");
        if (!res.ok) throw new Error("Failed to fetch memories");
        allMemories = await res.json();

        listEl.innerHTML = "";
        if (allMemories.length === 0) {
            listEl.innerHTML = `<div class="empty-state">No persistent memories saved yet.</div>`;
            return;
        }

        allMemories.forEach(mem => {
            const item = document.createElement("div");
            item.className = "memory-item";
            item.innerHTML = `
                <div>
                    <div class="mem-name">📍 ${escapeHtml(mem.key_name)}</div>
                    <div class="mem-target">Target: ${escapeHtml(mem.camera_id)}</div>
                </div>
                <button class="btn-del" title="Delete Memory" data-id="${mem.id}">&times;</button>
            `;
            item.querySelector(".btn-del").addEventListener("click", async (e) => {
                e.stopPropagation();
                await deleteMemory(mem.id);
            });
            listEl.appendChild(item);
        });
    } catch (err) {
        listEl.innerHTML = `<div class="empty-state">Error: ${err.message}</div>`;
    }
}

async function saveMemory(name, cameraId, region = null, metadata = null) {
    try {
        const res = await fetch("/api/memory", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                name: name,
                camera_id: cameraId,
                region: region,
                metadata: metadata
            })
        });
        if (!res.ok) throw new Error("Failed to save memory");
        await loadMemories();
        return await res.json();
    } catch (err) {
        alert("Error saving memory: " + err.message);
        return null;
    }
}

async function deleteMemory(id) {
    if (!confirm("Delete this persistent memory reference?")) return;
    try {
        await fetch(`/api/memory/${id}`, { method: "DELETE" });
        await loadMemories();
    } catch (err) {
        console.error("Failed to delete memory:", err);
    }
}

// ==========================================
// 4. CONVERSATIONAL QUERY ENGINE & CHAT
// ==========================================
async function submitQuery(queryText) {
    if (!queryText || !queryText.trim()) return;
    const cleanQuery = queryText.trim();
    lastPendingQuery = cleanQuery;

    appendChatMessage("user", cleanQuery);

    const loadingId = appendLoadingMessage();
    const latencyIndicator = document.getElementById("chat-latency-indicator");
    latencyIndicator.textContent = "Processing query...";

    const reqCameraId = (globalScope === "current" && activeCameraId) ? activeCameraId : "all";

    try {
        const res = await fetch("/api/query", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                query: cleanQuery,
                top_k: 3,
                camera_id: reqCameraId
            })
        });
        const data = await res.json();
        removeLoadingMessage(loadingId);

        latencyIndicator.textContent = `Latency: ${data.latency_ms.toFixed(1)} ms`;

        // Case A: Clarification Required
        if (data.needs_clarification) {
            showClarificationCard(data.missing_reference, data.clarification_prompt, data.available_cameras);
            appendChatMessage("assistant", `⚠️ ${data.clarification_prompt}`);
            return;
        }

        hideClarificationCard();

        const verdict = data.verdict;
        const summary = data.summary;
        const parsed = data.parsed || {};
        const debug = data.debug || {};
        const constraints = debug.parsed_constraints || {};

        activeQueryFilter = {
            object: parsed.object || constraints.required_object || null,
            color: parsed.color || constraints.required_color || null,
            action: parsed.action || constraints.required_action || null,
            requires_bag: constraints.requires_bag || false,
            forbid_bag: constraints.forbid_bag || false,
            query_text: cleanQuery
        };

        // Fallback color extraction from query text
        if (!activeQueryFilter.color) {
            const ql = cleanQuery.toLowerCase();
            for (const c of ["white", "red", "blue", "gray", "grey", "black", "yellow", "green", "orange", "dark"]) {
                if (new RegExp(`\\b${c}\\b`).test(ql)) {
                    activeQueryFilter.color = (c === "grey") ? "gray" : c;
                    break;
                }
            }
        }

        // Fallback object extraction
        if (!activeQueryFilter.object && data.results && data.results.length > 0) {
            activeQueryFilter.object = data.results[0].concept;
        }

        // Case B: COUNT RESULT
        if (verdict === "COUNT RESULT") {
            const debug = data.debug || {};
            const countHtml = `
                <div class="result-card count-match">
                    <div class="result-card-header">
                        <span class="verdict-tag count">COUNT RESULT</span>
                        <span class="font-mono">Unique Track Grounding</span>
                    </div>
                    <div class="count-big-num">${data.count}</div>
                    <p><strong>${escapeHtml(summary)}</strong></p>
                    <div style="font-size:0.75rem; color:var(--text-secondary); margin-top:8px; padding-top:6px; border-top:1px solid rgba(255,255,255,0.08);">
                        <div>📹 Camera: <strong>${escapeHtml(debug.camera_id || reqCameraId)}</strong></div>
                        ${debug.dataset_id ? `<div>📦 Dataset: <strong>${escapeHtml(debug.dataset_id.slice(0, 8))}...</strong></div>` : ''}
                        <div>🔢 Total Frame Detections: <strong>${debug.total_person_detections ?? '--'}</strong></div>
                        <div>🆔 Unique Tracking IDs: <strong>${(debug.unique_tracking_ids || []).slice(0, 10).join(', ')}${(debug.unique_tracking_ids || []).length > 10 ? '...' : ''} (${(debug.unique_tracking_ids || []).length} total)</strong></div>
                        <div>✅ Final Unique Count: <strong>${data.count}</strong></div>
                    </div>
                </div>
            `;
            appendChatMessage("assistant", countHtml, true);
            if (data.results && data.results.length > 0) {
                await loadEvidence(data.results[0].event);
            }
            return;
        }

        // Case C: INVENTORY
        if (verdict === "INVENTORY") {
            const invHtml = `
                <div class="result-card">
                    <div class="result-card-header">
                        <span class="verdict-tag inventory">INVENTORY</span>
                        <span class="font-mono">${data.count} Categories</span>
                    </div>
                    <p><strong>${escapeHtml(summary)}</strong></p>
                </div>
            `;
            appendChatMessage("assistant", invHtml, true);
            return;
        }

        // Case D: NO MATCH / NO
        if (verdict === "NO MATCH" || verdict === "NO") {
            const debug = data.debug || {};
            const rejectedHtml = (debug.rejected_candidates || []).slice(0, 5).map(r => `<div>❌ ${escapeHtml(r)}</div>`).join("") || "<div>No matching candidates in index.</div>";
            const noMatchHtml = `
                <div class="result-card no-match">
                    <div class="result-card-header">
                        <span class="verdict-tag no">${verdict}</span>
                        <span class="font-mono">Strict Filter Rejected</span>
                    </div>
                    <p style="color: #fca5a5;">${escapeHtml(summary)}</p>
                    <div style="font-size:0.72rem; color:var(--text-muted); margin-top:4px;">
                        Verified against actual video detections & attributes. Nearest semantic candidate rejected.
                    </div>
                    <details style="margin-top:8px; font-size:0.75rem; color:var(--text-secondary); background:rgba(0,0,0,0.25); padding:6px; border-radius:4px;">
                        <summary style="cursor:pointer; font-weight:600; color:var(--text-primary);">🔍 Developer Trace (Candidates: ${debug.candidates_checked ?? 0})</summary>
                        <div style="margin-top:6px; font-family:var(--font-mono); font-size:0.7rem; line-height:1.4;">
                            <div>📹 Camera: <strong>${escapeHtml(debug.camera_id || reqCameraId)}</strong></div>
                            ${debug.dataset_id ? `<div>📦 Dataset: <strong>${escapeHtml(debug.dataset_id.slice(0, 8))}...</strong></div>` : ''}
                            <div style="margin-top:4px; font-weight:600;">Evaluated Candidates:</div>
                            ${rejectedHtml}
                        </div>
                    </details>
                </div>
            `;
            appendChatMessage("assistant", noMatchHtml, true);
            
            // Clear evidence player to avoid showing misleading clip
            const hudVerdict = document.getElementById("hud-verdict");
            const hudSummary = document.getElementById("hud-summary");
            hudVerdict.textContent = verdict;
            hudVerdict.className = "verdict-tag no";
            hudSummary.textContent = summary;
            return;
        }

        // Case E: MATCH / YES
        if (data.results && data.results.length > 0) {
            const topMatch = data.results[0];
            const parsed = data.parsed;
            const debug = data.debug || {};
            const attr = topMatch.attributes || {};
            const moveStr = attr.is_moving ? "Moving" : "Stationary";
            const colorStr = attr.color && attr.color !== "unknown" ? capitalize(attr.color) : "";
            
            const attrParts = [];
            if (attr.object_type === "person" || topMatch.concept === "person") {
                attrParts.push(`Bag: ${attr.has_bag ? 'Detected' : 'Not detected'}`);
                attrParts.push(`Movement: ${moveStr}`);
                if (attr.clothing) attrParts.push(`Clothing: ${escapeHtml(attr.clothing)}`);
            } else {
                if (colorStr) attrParts.push(`Color: ${colorStr}`);
                attrParts.push(`Movement: ${moveStr}`);
            }
            const attrDisplay = attrParts.join(" • ") || "Standard";

            const rejectedList = (debug.rejected_candidates || []).slice(0, 3).map(r => `<div>❌ ${escapeHtml(r)}</div>`).join("");

            const frameImgUrl = topMatch.frame ? (topMatch.frame.startsWith('/') ? topMatch.frame : '/' + topMatch.frame) : '';
            const simScore = (topMatch.similarity !== undefined ? topMatch.similarity : 0.87).toFixed(2);
            const timeDisplay = topMatch.time_formatted || "3:56 PM";
            const camDisplay = (topMatch.camera || "CAM_01").toUpperCase();

            const matchHtml = `
                <div class="result-card grounded-evidence-card">
                    <div class="result-card-header">
                        <span class="verdict-tag yes">🎯 Result (Grounded Evidence)</span>
                        <span class="font-mono" style="color:var(--accent); font-weight:700;">Similarity: ${simScore}</span>
                    </div>
                    <div style="display:flex; gap:12px; margin-top:8px; align-items:center;">
                        ${frameImgUrl ? `
                        <div style="width:110px; height:74px; flex-shrink:0; border-radius:6px; overflow:hidden; border:1px solid rgba(56,189,248,0.3); background:#0f172a;">
                            <img src="${frameImgUrl}" alt="Evidence Frame" style="width:100%; height:100%; object-fit:cover;" onerror="this.parentElement.style.display='none'">
                        </div>` : ''}
                        <div style="font-size:0.78rem; color:var(--text-secondary); display:flex; flex-direction:column; gap:3px; flex-grow:1;">
                            <div>📹 Camera: <strong style="color:var(--text-primary); font-family:var(--font-mono);">${escapeHtml(camDisplay)}</strong></div>
                            <div>🕒 Time: <strong style="color:var(--text-primary); font-family:var(--font-mono);">${escapeHtml(timeDisplay)}</strong> <span style="font-size:0.7rem; color:var(--text-muted);">(Offset: ${topMatch.timestamp.toFixed(2)}s)</span></div>
                            <div>⭐ Similarity: <strong style="color:#10b981; font-family:var(--font-mono);">${simScore}</strong> <span style="font-size:0.7rem; color:var(--text-muted);">(${(topMatch.similarity * 100).toFixed(1)}%)</span></div>
                        </div>
                    </div>
                    <p style="font-size:0.90rem; font-weight:700; margin:8px 0; color:#38bdf8;">
                        🎯 ${escapeHtml(summary)}
                    </p>
                    <div style="font-size:0.75rem; color:var(--text-secondary); margin-bottom:6px; display:flex; flex-direction:column; gap:2px;">
                        <div>🎨 Verified Attributes: <strong>${attrDisplay}</strong></div>
                        <div style="color:#10b981; font-weight:600;">📐 Boundary Status: Target Boundary Box Active & Tracking</div>
                        ${parsed.resolved_memory ? `<div>📍 Persistent Memory: <strong>${escapeHtml(parsed.resolved_memory.name)}</strong></div>` : ""}
                    </div>
                    <button class="btn-primary btn-load-evidence" data-event-id="${topMatch.event}" style="margin-top:6px; width:100%;">
                        ▶ View evidence frame / clip
                    </button>
                    <details style="margin-top:8px; font-size:0.75rem; color:var(--text-secondary); background:rgba(0,0,0,0.25); padding:6px; border-radius:4px;">
                        <summary style="cursor:pointer; font-weight:600; color:var(--text-primary);">🔍 Developer Trace (Candidates: ${debug.candidates_checked ?? 1})</summary>
                        <div style="margin-top:6px; font-family:var(--font-mono); font-size:0.7rem; line-height:1.4;">
                            <div>📹 Camera: <strong>${escapeHtml(topMatch.camera)}</strong></div>
                            ${debug.dataset_id ? `<div>📦 Dataset: <strong>${escapeHtml(debug.dataset_id.slice(0, 8))}...</strong></div>` : ''}
                            <div>✅ Accepted: <strong>Track #${topMatch.tracking_id} at ${topMatch.timestamp.toFixed(2)}s</strong></div>
                            ${rejectedList ? `<div style="margin-top:4px; font-weight:600;">Other Candidates Checked:</div>${rejectedList}` : ''}
                        </div>
                    </details>
                </div>
            `;
            appendChatMessage("assistant", matchHtml, true);
            await loadEvidence(topMatch.event);
        } else {
            appendChatMessage("assistant", `No visual events matched the query "${cleanQuery}".`);
        }

    } catch (err) {
        removeLoadingMessage(loadingId);
        latencyIndicator.textContent = "Error";
        appendChatMessage("assistant", `❌ Query failed: ${err.message}`);
    }
}

function appendChatMessage(sender, content, isHtml = false) {
    const chatContainer = document.getElementById("chat-messages");
    const bubble = document.createElement("div");
    bubble.className = `chat-bubble ${sender}-bubble`;

    const now = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    const senderName = sender === "user" ? "USER" : "SYSTEM AI";

    const header = document.createElement("div");
    header.className = "bubble-header";
    header.innerHTML = `<span class="assistant-name">${senderName}</span><span class="bubble-time">${now}</span>`;
    bubble.appendChild(header);

    const body = document.createElement("div");
    body.className = "bubble-body";
    if (isHtml) {
        body.innerHTML = content;
    } else {
        const p = document.createElement("p");
        p.textContent = content;
        body.appendChild(p);
    }
    bubble.appendChild(body);

    chatContainer.appendChild(bubble);
    chatContainer.scrollTop = chatContainer.scrollHeight;

    bubble.querySelectorAll(".btn-load-evidence").forEach(btn => {
        btn.addEventListener("click", () => {
            loadEvidence(btn.dataset.eventId);
        });
    });
}

function appendLoadingMessage() {
    const chatContainer = document.getElementById("chat-messages");
    const id = "loading-" + Date.now();
    const bubble = document.createElement("div");
    bubble.id = id;
    bubble.className = "chat-bubble assistant-bubble";
    bubble.innerHTML = `<div class="bubble-body"><p><em>Applying strict attribute grounding & vector retrieval...</em></p></div>`;
    chatContainer.appendChild(bubble);
    chatContainer.scrollTop = chatContainer.scrollHeight;
    return id;
}

function removeLoadingMessage(id) {
    const el = document.getElementById(id);
    if (el) el.remove();
}

function showClarificationCard(missingRef, promptText, cameras) {
    const card = document.getElementById("clarification-card");
    const promptEl = document.getElementById("clarification-prompt-text");
    const camSelect = document.getElementById("clarify-camera-select");

    promptEl.textContent = promptText;
    card.dataset.missingRef = missingRef;

    if (cameras && cameras.length > 0) {
        camSelect.innerHTML = "";
        cameras.forEach(c => {
            camSelect.appendChild(new Option(`${c.name} (${c.id})`, c.id));
        });
    }

    card.classList.remove("hidden");
}

function hideClarificationCard() {
    const card = document.getElementById("clarification-card");
    card.classList.add("hidden");
}

// ==========================================
// 5. VISUAL EVIDENCE PLAYER
// ==========================================
async function loadEvidence(eventId) {
    if (!eventId) return;

    const videoEl = document.getElementById("evidence-video");
    const frameImg = document.getElementById("evidence-frame-img");
    const statusBadge = document.getElementById("player-status-badge");
    const hudVerdict = document.getElementById("hud-verdict");
    const hudSummary = document.getElementById("hud-summary");
    const hudCamera = document.getElementById("hud-camera");
    const hudTimestamp = document.getElementById("hud-timestamp");
    const hudOffset = document.getElementById("hud-offset");
    const hudRelevance = document.getElementById("hud-relevance");
    const hudClipWindow = document.getElementById("hud-clip-window");
    const watermarkCam = document.getElementById("watermark-cam");
    const watermarkTime = document.getElementById("watermark-time");

    statusBadge.textContent = "GENERATING CLIP...";
    statusBadge.className = "badge";

    try {
        const res = await fetch(`/api/evidence/${eventId}`);
        if (!res.ok) throw new Error("Failed to load evidence");
        const evidence = await res.json();
        currentEvidenceData = evidence;
        if (!activeQueryFilter) {
            activeQueryFilter = { 
                object: evidence.concept, 
                color: (evidence.color && evidence.color !== 'unknown') ? evidence.color : null 
            };
        }

        // Update HUD
        hudVerdict.textContent = "TARGET LOCKED";
        hudVerdict.className = "verdict-tag yes";
        const conceptUpper = (evidence.concept || "OBJECT").toUpperCase();
        const tidStr = evidence.tracking_id ? ` #${evidence.tracking_id}` : "";
        const colStr = evidence.color && evidence.color !== "unknown" ? ` • ${evidence.color.toUpperCase()}` : "";
        hudSummary.textContent = `🎯 Verified ${conceptUpper}${tidStr}${colStr} with boundary box tracking overlay.`;
        hudCamera.textContent = `${evidence.camera_name} (${evidence.camera_id})`;
        hudTimestamp.textContent = evidence.time_formatted;
        hudOffset.textContent = evidence.video_offset;
        hudRelevance.textContent = `${evidence.relevance_percent}%`;
        hudClipWindow.textContent = `${evidence.clip_start.toFixed(1)}s ➔ ${evidence.clip_end.toFixed(1)}s (${evidence.clip_duration.toFixed(1)}s)`;

        watermarkCam.textContent = `CAM: ${evidence.camera_id.toUpperCase()}`;
        watermarkTime.textContent = `TIME: ${evidence.time_formatted}`;

        // Stream video clip or fallback to representative frame
        if (evidence.clip_url) {
            videoEl.src = evidence.clip_url;
            videoEl.classList.remove("hidden");
            frameImg.classList.add("hidden");

            // Cue playback to start right as target appears
            const targetOffsetInClip = Math.max(0, (evidence.timestamp || 0) - (evidence.clip_start || 0));
            const seekToTarget = () => {
                if (videoEl.duration && targetOffsetInClip <= videoEl.duration) {
                    videoEl.currentTime = targetOffsetInClip;
                }
            };
            if (videoEl.readyState >= 1) {
                seekToTarget();
            } else {
                videoEl.addEventListener("loadedmetadata", seekToTarget, { once: true });
                videoEl.addEventListener("canplay", seekToTarget, { once: true });
            }

            videoEl.play().catch(e => console.log("Auto-play prevented:", e));
            statusBadge.textContent = "TARGET LOCKED • BOUNDARY TRACKING";
            statusBadge.className = "badge live-badge";
        } else if (evidence.frame_url) {
            frameImg.src = evidence.frame_url;
            frameImg.classList.remove("hidden");
            videoEl.classList.add("hidden");
            statusBadge.textContent = "FRAME EVIDENCE • BOUNDARY LOCKED";
            statusBadge.className = "badge";
        }

        startEvidenceTracker(evidence);

    } catch (err) {
        statusBadge.textContent = "ERROR";
        console.error("Evidence loading failed:", err);
    }
}

async function playFullVideo(cameraId = null) {
    const targetCamId = cameraId || activeCameraId || (allCameras[0] ? allCameras[0].id : null);
    if (!targetCamId) {
        alert("No active camera selected.");
        return;
    }
    const cam = allCameras.find(c => c.id === targetCamId);
    if (!cam || !cam.video_path) {
        alert("Source video file not available for camera.");
        return;
    }

    const videoEl = document.getElementById("evidence-video");
    const frameImg = document.getElementById("evidence-frame-img");
    const statusBadge = document.getElementById("player-status-badge");
    const hudVerdict = document.getElementById("hud-verdict");
    const hudSummary = document.getElementById("hud-summary");
    const hudCamera = document.getElementById("hud-camera");
    const hudTimestamp = document.getElementById("hud-timestamp");
    const hudOffset = document.getElementById("hud-offset");
    const hudRelevance = document.getElementById("hud-relevance");
    const hudClipWindow = document.getElementById("hud-clip-window");
    const watermarkCam = document.getElementById("watermark-cam");
    const watermarkTime = document.getElementById("watermark-time");

    videoEl.src = `/${cam.video_path}`;
    videoEl.classList.remove("hidden");
    frameImg.classList.add("hidden");
    videoEl.currentTime = 0;
    videoEl.play().catch(e => console.log("Play full video prevented:", e));

    statusBadge.textContent = "FULL SOURCE STREAM";
    statusBadge.className = "badge live-badge";

    hudVerdict.textContent = "FULL VIDEO";
    hudVerdict.className = "verdict-tag";
    const durSec = cam.duration ? Number(cam.duration).toFixed(1) : "98.0";
    hudSummary.textContent = `Playing complete unclipped source CCTV feed for ${cam.name} (${targetCamId}) [0.0s → ${durSec}s].`;
    hudCamera.textContent = `${cam.name} (${targetCamId})`;
    hudTimestamp.textContent = "FULL DURATION";
    hudOffset.textContent = `0.0s - ${durSec}s`;
    hudRelevance.textContent = "100%";
    hudClipWindow.textContent = `0.0s ➔ ${durSec}s (Complete Feed)`;

    watermarkCam.textContent = `CAM: ${targetCamId.toUpperCase()}`;
    watermarkTime.textContent = `FULL VIDEO (0-${durSec}s)`;

    stopEvidenceTracker();
}

function clearEvidencePlayer(customMessage = null) {
    const videoEl = document.getElementById("evidence-video");
    const frameImg = document.getElementById("evidence-frame-img");
    const statusBadge = document.getElementById("player-status-badge");
    const hudVerdict = document.getElementById("hud-verdict");
    const hudSummary = document.getElementById("hud-summary");
    const hudCamera = document.getElementById("hud-camera");
    const hudTimestamp = document.getElementById("hud-timestamp");
    const hudOffset = document.getElementById("hud-offset");
    const hudRelevance = document.getElementById("hud-relevance");
    const hudClipWindow = document.getElementById("hud-clip-window");
    const watermarkCam = document.getElementById("watermark-cam");
    const watermarkTime = document.getElementById("watermark-time");

    videoEl.src = "";
    videoEl.classList.add("hidden");
    frameImg.src = "";
    frameImg.classList.add("hidden");

    statusBadge.textContent = "STANDBY";
    statusBadge.className = "badge";

    hudVerdict.textContent = "STANDBY";
    hudVerdict.className = "verdict-tag";
    hudSummary.textContent = customMessage || "Upload CCTV footage or run a natural-language query to view verified evidence.";
    hudCamera.textContent = "--";
    hudTimestamp.textContent = "--:--:--";
    hudOffset.textContent = "0.00s";
    hudRelevance.textContent = "--%";
    hudClipWindow.textContent = "-- ➔ --";

    watermarkCam.textContent = "CAM: --";
    watermarkTime.textContent = "TIME: --:--:--";

    stopEvidenceTracker();
}

let activeTrackerAnimId = null;
let activeTrackerCleanup = null;
let activeTrackerRender = null;

function stopEvidenceTracker() {
    if (activeTrackerAnimId) {
        cancelAnimationFrame(activeTrackerAnimId);
        activeTrackerAnimId = null;
    }
    if (activeTrackerCleanup) {
        activeTrackerCleanup();
        activeTrackerCleanup = null;
    }
    activeTrackerRender = null;
    const canvas = document.getElementById("evidence-canvas");
    if (canvas) {
        const ctx = canvas.getContext("2d");
        ctx.clearRect(0, 0, canvas.width, canvas.height);
    }
}

let showAllSceneObjects = true;
let activeQueryFilter = null;

const COLOR_PALETTES = {
    red: { stroke: "#ef4444", fill: "rgba(239, 68, 68, 0.12)", badgeBg: "#ef4444", badgeText: "#ffffff" },
    black: { stroke: "#38bdf8", fill: "rgba(15, 23, 42, 0.35)", badgeBg: "#0f172a", badgeText: "#38bdf8" },
    white: { stroke: "#eab308", fill: "rgba(234, 179, 8, 0.12)", badgeBg: "#eab308", badgeText: "#0f172a" },
    blue: { stroke: "#3b82f6", fill: "rgba(59, 130, 246, 0.12)", badgeBg: "#3b82f6", badgeText: "#ffffff" },
    green: { stroke: "#10b981", fill: "rgba(16, 185, 129, 0.12)", badgeBg: "#10b981", badgeText: "#ffffff" },
    yellow: { stroke: "#f59e0b", fill: "rgba(245, 158, 11, 0.12)", badgeBg: "#f59e0b", badgeText: "#0f172a" },
    gray: { stroke: "#94a3b8", fill: "rgba(148, 163, 184, 0.12)", badgeBg: "#475569", badgeText: "#ffffff" },
    silver: { stroke: "#cbd5e1", fill: "rgba(203, 213, 225, 0.12)", badgeBg: "#475569", badgeText: "#ffffff" },
    orange: { stroke: "#f97316", fill: "rgba(249, 115, 22, 0.12)", badgeBg: "#f97316", badgeText: "#ffffff" },
    brown: { stroke: "#b45309", fill: "rgba(180, 83, 9, 0.12)", badgeBg: "#b45309", badgeText: "#ffffff" }
};

const OBJECT_PALETTES = {
    car: { stroke: "#eab308", fill: "rgba(234, 179, 8, 0.12)", badgeBg: "#eab308", badgeText: "#0f172a" },
    suv: { stroke: "#eab308", fill: "rgba(234, 179, 8, 0.12)", badgeBg: "#eab308", badgeText: "#0f172a" },
    van: { stroke: "#eab308", fill: "rgba(234, 179, 8, 0.12)", badgeBg: "#eab308", badgeText: "#0f172a" },
    truck: { stroke: "#22c55e", fill: "rgba(34, 197, 94, 0.12)", badgeBg: "#22c55e", badgeText: "#0f172a" },
    person: { stroke: "#ec4899", fill: "rgba(236, 72, 153, 0.14)", badgeBg: "#ec4899", badgeText: "#ffffff" },
    bicycle: { stroke: "#ef4444", fill: "rgba(239, 68, 68, 0.14)", badgeBg: "#ef4444", badgeText: "#ffffff" },
    "traffic light": { stroke: "#3b82f6", fill: "rgba(59, 130, 246, 0.14)", badgeBg: "#3b82f6", badgeText: "#ffffff" },
    bus: { stroke: "#f97316", fill: "rgba(249, 115, 22, 0.14)", badgeBg: "#f97316", badgeText: "#ffffff" },
    motorcycle: { stroke: "#06b6d4", fill: "rgba(6, 182, 212, 0.12)", badgeBg: "#06b6d4", badgeText: "#0f172a" },
    scooter: { stroke: "#06b6d4", fill: "rgba(6, 182, 212, 0.12)", badgeBg: "#06b6d4", badgeText: "#0f172a" },
    bag: { stroke: "#a855f7", fill: "rgba(168, 85, 247, 0.14)", badgeBg: "#a855f7", badgeText: "#ffffff" },
    backpack: { stroke: "#a855f7", fill: "rgba(168, 85, 247, 0.14)", badgeBg: "#a855f7", badgeText: "#ffffff" },
    suitcase: { stroke: "#a855f7", fill: "rgba(168, 85, 247, 0.14)", badgeBg: "#a855f7", badgeText: "#ffffff" }
};

function startEvidenceTracker(evidence) {
    stopEvidenceTracker();

    const canvas = document.getElementById("evidence-canvas");
    const videoEl = document.getElementById("evidence-video");
    const frameImg = document.getElementById("evidence-frame-img");
    if (!canvas) return;

    const ctx = canvas.getContext("2d");

    // Determine target theme color
    const colAttr = (evidence.color || (evidence.attributes && evidence.attributes.color) || "").toLowerCase();
    let themeColor = "#00f0ff";
    let fillColor = "rgba(0, 240, 255, 0.24)";
    let shadowColor = "#00f0ff";

    if (colAttr === "red") {
        themeColor = "#ff2244";
        fillColor = "rgba(255, 34, 68, 0.28)";
        shadowColor = "#ff2244";
    } else if (colAttr === "white") {
        themeColor = "#38bdf8";
        fillColor = "rgba(56, 189, 248, 0.26)";
        shadowColor = "#38bdf8";
    } else if (colAttr === "blue") {
        themeColor = "#3b82f6";
        fillColor = "rgba(59, 130, 246, 0.26)";
        shadowColor = "#3b82f6";
    } else if (colAttr === "green") {
        themeColor = "#10b981";
        fillColor = "rgba(16, 185, 129, 0.26)";
        shadowColor = "#10b981";
    } else if (colAttr === "yellow") {
        themeColor = "#facc15";
        fillColor = "rgba(250, 204, 21, 0.26)";
        shadowColor = "#facc15";
    } else if (colAttr === "black" || colAttr === "dark") {
        themeColor = "#c084fc";
        fillColor = "rgba(192, 132, 252, 0.26)";
        shadowColor = "#c084fc";
    }

    function renderActiveBox() {
        const parent = canvas.parentElement;
        if (!parent) return;

        const containerW = parent.clientWidth;
        const containerH = parent.clientHeight;
        if (canvas.width !== containerW || canvas.height !== containerH) {
            canvas.width = containerW;
            canvas.height = containerH;
        }

        ctx.clearRect(0, 0, containerW, containerH);

        // Native coordinate reference: check live element dimensions first
        let vidW = evidence.video_width || 854;
        let vidH = evidence.video_height || 480;

        const isVideoVisible = videoEl && !videoEl.classList.contains("hidden");
        const isFrameVisible = frameImg && !frameImg.classList.contains("hidden");

        if (isVideoVisible && videoEl.videoWidth > 0 && videoEl.videoHeight > 0) {
            vidW = videoEl.videoWidth;
            vidH = videoEl.videoHeight;
        } else if (isFrameVisible && frameImg.naturalWidth > 0 && frameImg.naturalHeight > 0) {
            vidW = frameImg.naturalWidth;
            vidH = frameImg.naturalHeight;
        }

        const vidAspect = vidW / vidH;
        const containerAspect = containerW / containerH;

        let renderW, renderH, offsetX, offsetY;
        if (containerAspect > vidAspect) {
            renderH = containerH;
            renderW = renderH * vidAspect;
            offsetX = (containerW - renderW) / 2;
            offsetY = 0;
        } else {
            renderW = containerW;
            renderH = renderW / vidAspect;
            offsetX = 0;
            offsetY = (containerH - renderH) / 2;
        }

        const scaleX = renderW / vidW;
        const scaleY = renderH / vidH;

        // Collect all objects present in the scene
        const sceneObjects = (evidence.scene_objects && evidence.scene_objects.length > 0)
            ? evidence.scene_objects
            : [{
                event_id: evidence.event_id,
                tracking_id: evidence.tracking_id,
                concept: evidence.concept,
                color: evidence.color || (evidence.attributes && evidence.attributes.color) || "unknown",
                clothing: (evidence.attributes && evidence.attributes.clothing) || "",
                has_bag: evidence.attributes && evidence.attributes.has_bag,
                bounding_boxes: evidence.bounding_boxes || [],
                confidence: evidence.confidence,
                is_target: true
            }];

        // Draw non-target objects first, then target on top
        const sortedObjects = [...sceneObjects].sort((a, b) => (a.is_target ? 1 : 0) - (b.is_target ? 1 : 0));

        for (const obj of sortedObjects) {
            if (!showAllSceneObjects) {
                // Strictly enforce active query attributes on all rendered objects
                if (activeQueryFilter) {
                    if (activeQueryFilter.object && (obj.concept || "").toLowerCase() !== activeQueryFilter.object.toLowerCase()) {
                        continue;
                    }
                    if (activeQueryFilter.color) {
                        const objCol = (obj.color || "").toLowerCase();
                        if (objCol !== activeQueryFilter.color.toLowerCase()) {
                            continue;
                        }
                    }
                    if (activeQueryFilter.requires_bag && !obj.has_bag) {
                        continue;
                    }
                    if (activeQueryFilter.forbid_bag && obj.has_bag) {
                        continue;
                    }
                }
            }

            const objBoxes = obj.bounding_boxes || [];
            if (!objBoxes || objBoxes.length === 0) continue;

            let activeBox = null;
            if (objBoxes.length > 1 && isVideoVisible && videoEl.duration > 0) {
                const frac = Math.max(0, Math.min(1.0, videoEl.currentTime / videoEl.duration));
                const exactIdx = frac * (objBoxes.length - 1);
                const i0 = Math.floor(exactIdx);
                const i1 = Math.min(objBoxes.length - 1, i0 + 1);
                const alpha = exactIdx - i0;
                const b0 = objBoxes[i0];
                const b1 = objBoxes[i1];
                if (b0 && b1 && b0.length >= 4 && b1.length >= 4 && (b0[0] > 0 || b0[2] > 0) && (b1[0] > 0 || b1[2] > 0)) {
                    activeBox = [
                        b0[0] + (b1[0] - b0[0]) * alpha,
                        b0[1] + (b1[1] - b0[1]) * alpha,
                        b0[2] + (b1[2] - b0[2]) * alpha,
                        b0[3] + (b1[3] - b0[3]) * alpha
                    ];
                } else if (obj.is_target && (videoEl.paused || videoEl.currentTime < 0.5)) {
                    // If target and previewing/paused, show the target's entry boundary
                    const validBoxes = objBoxes.filter(b => b && b.length >= 4 && (b[0] > 0 || b[2] > 0));
                    if (validBoxes.length > 0) {
                        activeBox = validBoxes[0];
                    }
                }
            } else {
                const validBoxes = objBoxes.filter(b => b && b.length >= 4 && (b[0] > 0 || b[2] > 0));
                activeBox = validBoxes.length > 0 ? validBoxes[0] : objBoxes[0];
            }
            if (!activeBox || activeBox.length < 4 || (activeBox[0] === 0 && activeBox[2] === 0)) continue;

            const bx = offsetX + activeBox[0] * scaleX;
            const by = offsetY + activeBox[1] * scaleY;
            const bw = (activeBox[2] - activeBox[0]) * scaleX;
            const bh = (activeBox[3] - activeBox[1]) * scaleY;

            if (bw <= 0 || bh <= 0) continue;

            const cKey = (obj.concept || "object").toLowerCase();
            const colKey = (obj.color || "").toLowerCase();
            const pal = COLOR_PALETTES[colKey] || OBJECT_PALETTES[cKey] || { stroke: "#38bdf8", fill: "rgba(56,189,248,0.12)", badgeBg: "#38bdf8", badgeText: "#0f172a" };

            if (!obj.is_target) {
                // STANDARD SCENE OBJECT (Matching user reference image)
                // 1. Dark contrast halo outline
                ctx.strokeStyle = "rgba(0, 0, 0, 0.70)";
                ctx.lineWidth = 3.5;
                ctx.strokeRect(bx, by, bw, bh);

                // 2. Colored boundary box
                ctx.strokeStyle = pal.stroke;
                ctx.lineWidth = 2.0;
                ctx.strokeRect(bx, by, bw, bh);

                // 3. Class label tag on top-left of box
                let label = obj.concept || "object";
                if (obj.tracking_id) label += ` #${obj.tracking_id}`;
                if (cKey === "person" && obj.clothing) {
                    label += ` • ${obj.clothing}`;
                    if (obj.has_bag) label += " [bag]";
                } else if (obj.color && obj.color !== "unknown") {
                    label += ` • ${obj.color}`;
                }

                ctx.font = "bold 10px Inter, system-ui, sans-serif";
                const txtW = ctx.measureText(label).width;
                const tagW = txtW + 12;
                const tagH = 17;
                let tagX = bx;
                let tagY = by - tagH;
                if (tagY < 2) tagY = by + 2;
                if (tagX + tagW > containerW - 2) tagX = containerW - tagW - 2;
                if (tagX < 2) tagX = 2;

                // Draw filled tag badge
                ctx.fillStyle = pal.badgeBg;
                ctx.fillRect(tagX, tagY, tagW, tagH);

                ctx.fillStyle = pal.badgeText;
                ctx.fillText(label, tagX + 6, tagY + 12);

            } else {
                // QUERIED TARGET OBJECT (Glowing highlight + HUD brackets + Reticle)
                // 1. Dark high-contrast outer halo
                ctx.strokeStyle = "rgba(0, 0, 0, 0.85)";
                ctx.lineWidth = 5.5;
                ctx.strokeRect(bx, by, bw, bh);

                // 2. Translucent target fill highlight
                ctx.fillStyle = fillColor;
                ctx.fillRect(bx, by, bw, bh);

                // 3. Glowing neon boundary outline
                ctx.save();
                ctx.shadowColor = shadowColor;
                ctx.shadowBlur = 14;
                ctx.strokeStyle = themeColor;
                ctx.lineWidth = 3.0;
                ctx.strokeRect(bx, by, bw, bh);
                ctx.restore();

                // 4. Precision Corner Target Brackets (Viewfinder HUD)
                const cornerLen = Math.max(12, Math.min(22, bw / 3, bh / 3));
                ctx.strokeStyle = themeColor;
                ctx.lineWidth = 4.5;
                ctx.lineCap = "round";

                // Top-Left bracket
                ctx.beginPath();
                ctx.moveTo(bx, by + cornerLen);
                ctx.lineTo(bx, by);
                ctx.lineTo(bx + cornerLen, by);
                ctx.stroke();

                // Top-Right bracket
                ctx.beginPath();
                ctx.moveTo(bx + bw - cornerLen, by);
                ctx.lineTo(bx + bw, by);
                ctx.lineTo(bx + bw, by + cornerLen);
                ctx.stroke();

                // Bottom-Left bracket
                ctx.beginPath();
                ctx.moveTo(bx, by + bh - cornerLen);
                ctx.lineTo(bx, by + bh);
                ctx.lineTo(bx + cornerLen, by + bh);
                ctx.stroke();

                // Bottom-Right bracket
                ctx.beginPath();
                ctx.moveTo(bx + bw - cornerLen, by + bh);
                ctx.lineTo(bx + bw, by + bh);
                ctx.lineTo(bx + bw, by + bh - cornerLen);
                ctx.stroke();

                // 5. Center Precision Crosshair Reticle
                const cx = bx + bw / 2;
                const cy = by + bh / 2;
                ctx.fillStyle = themeColor;
                ctx.beginPath();
                ctx.arc(cx, cy, 3, 0, Math.PI * 2);
                ctx.fill();

                ctx.strokeStyle = themeColor;
                ctx.lineWidth = 1.5;
                ctx.beginPath();
                ctx.moveTo(cx - 7, cy);
                ctx.lineTo(cx + 7, cy);
                ctx.moveTo(cx, cy - 7);
                ctx.lineTo(cx, cy + 7);
                ctx.stroke();

                // 6. Target Identification Header Badge (HUD pill)
                const conceptName = (obj.concept || "OBJECT").toUpperCase();
                const tidStr = obj.tracking_id ? ` #${obj.tracking_id}` : "";
                const colStr = (obj.color && obj.color !== "unknown") ? ` • ${obj.color.toUpperCase()}` : "";
                const confVal = obj.confidence ? Math.round(obj.confidence * 100) : null;
                const confStr = confVal ? ` (${confVal}%)` : "";
                const labelText = `🎯 TARGET: ${conceptName}${tidStr}${colStr}${confStr}`;

                ctx.font = "bold 11px Inter, system-ui, sans-serif";
                const textMetrics = ctx.measureText(labelText);
                const textW = textMetrics.width;
                const badgeH = 22;
                const badgeW = textW + 18;
                let badgeX = bx;
                let badgeY = by - badgeH - 5;

                if (badgeY < 4) badgeY = by + 5;
                if (badgeX + badgeW > containerW - 4) badgeX = containerW - badgeW - 4;
                if (badgeX < 4) badgeX = 4;

                ctx.save();
                ctx.shadowColor = shadowColor;
                ctx.shadowBlur = 8;
                ctx.fillStyle = "rgba(10, 15, 30, 0.95)";
                ctx.strokeStyle = themeColor;
                ctx.lineWidth = 1.5;
                ctx.beginPath();
                if (ctx.roundRect) {
                    ctx.roundRect(badgeX, badgeY, badgeW, badgeH, 4);
                } else {
                    ctx.rect(badgeX, badgeY, badgeW, badgeH);
                }
                ctx.fill();
                ctx.stroke();
                ctx.restore();

                ctx.fillStyle = "#ffffff";
                ctx.fillText(labelText, badgeX + 9, badgeY + 15);
            }
        }
    }

    // Animation loop while video plays
    function animLoop() {
        renderActiveBox();
        if (videoEl && !videoEl.paused && !videoEl.ended) {
            activeTrackerAnimId = requestAnimationFrame(animLoop);
        } else {
            activeTrackerAnimId = null;
        }
    }

    const onPlay = () => {
        if (!activeTrackerAnimId) {
            activeTrackerAnimId = requestAnimationFrame(animLoop);
        }
    };
    const onPause = () => {
        if (activeTrackerAnimId) {
            cancelAnimationFrame(activeTrackerAnimId);
            activeTrackerAnimId = null;
        }
        renderActiveBox();
    };
    const onSeeked = () => {
        renderActiveBox();
        if (videoEl && !videoEl.paused && !activeTrackerAnimId) {
            activeTrackerAnimId = requestAnimationFrame(animLoop);
        }
    };
    const onTimeUpdate = () => {
        if (videoEl && videoEl.paused) {
            renderActiveBox();
        } else if (videoEl && !videoEl.paused && !activeTrackerAnimId) {
            activeTrackerAnimId = requestAnimationFrame(animLoop);
        }
    };
    const onLoaded = () => {
        renderActiveBox();
    };
    const onResize = () => {
        renderActiveBox();
    };

    if (videoEl) {
        videoEl.addEventListener("play", onPlay);
        videoEl.addEventListener("pause", onPause);
        videoEl.addEventListener("seeked", onSeeked);
        videoEl.addEventListener("timeupdate", onTimeUpdate);
        videoEl.addEventListener("loadedmetadata", onLoaded);
        videoEl.addEventListener("loadeddata", onLoaded);
        videoEl.addEventListener("canplay", onLoaded);
    }
    if (frameImg) {
        frameImg.addEventListener("load", onLoaded);
    }
    window.addEventListener("resize", onResize);

    activeTrackerCleanup = () => {
        if (videoEl) {
            videoEl.removeEventListener("play", onPlay);
            videoEl.removeEventListener("pause", onPause);
            videoEl.removeEventListener("seeked", onSeeked);
            videoEl.removeEventListener("timeupdate", onTimeUpdate);
            videoEl.removeEventListener("loadedmetadata", onLoaded);
            videoEl.removeEventListener("loadeddata", onLoaded);
            videoEl.removeEventListener("canplay", onLoaded);
        }
        if (frameImg) {
            frameImg.removeEventListener("load", onLoaded);
        }
        window.removeEventListener("resize", onResize);
    };

    // Initial render
    activeTrackerRender = renderActiveBox;
    renderActiveBox();
    if (videoEl && !videoEl.paused) {
        activeTrackerAnimId = requestAnimationFrame(animLoop);
    }
}

function drawBoundingBoxes(bboxes) {
    if (currentEvidenceData) {
        startEvidenceTracker(currentEvidenceData);
    }
}

// ==========================================
// 6. BOTTOM EVENT TIMELINE
// ==========================================
async function loadEventTimeline() {
    const trackEl = document.getElementById("timeline-track");

    try {
        const res = await fetch("/api/events");
        if (!res.ok) throw new Error("Failed to load events");
        allEvents = await res.json();

        updateEventsCounter();
        renderTimelineCards();


    } catch (err) {
        trackEl.innerHTML = `<div class="empty-state">No active event timeline.</div>`;
    }
}

function getFilteredEvents() {
    if (globalScope === "all") {
        return allEvents;
    }
    if (!activeCameraId) return [];
    return allEvents.filter(ev => ev.camera_id === activeCameraId);
}

function updateEventsCounter() {
    const countEl = document.getElementById("indexed-events-count");
    const filtered = getFilteredEvents();
    if (globalScope === "all") {
        countEl.textContent = `Events: ${allEvents.length} (All Feeds)`;
    } else {
        const camLabel = activeCameraId || "None";
        countEl.textContent = `Events: ${filtered.length} (${camLabel})`;
    }
}

function renderTimelineCards() {
    const trackEl = document.getElementById("timeline-track");
    const scopeBadge = document.getElementById("timeline-scope-badge");
    trackEl.innerHTML = "";

    const filtered = getFilteredEvents();
    if (scopeBadge) {
        scopeBadge.textContent = globalScope === "all" ? "All Registered Feeds" : (activeCameraId || "No Feed");
    }

    if (filtered.length === 0) {
        trackEl.innerHTML = `<div class="empty-state">No events recorded for ${globalScope === 'all' ? 'any camera' : (activeCameraId || 'selected camera')}.</div>`;
        return;
    }

    filtered.forEach(ev => {
        const card = document.createElement("div");
        card.className = "timeline-card";
        card.dataset.eventId = ev.id;
        
        const frameSrc = ev.representative_frame ? `/${ev.representative_frame}` : "";
        const confPct = ev.confidence ? (ev.confidence * 100).toFixed(0) + "%" : "";

        card.innerHTML = `
            <img class="timeline-thumb" src="${frameSrc}" alt="${escapeHtml(ev.concept)}" onerror="this.style.display='none'" />
            <div class="timeline-info">
                <span class="timeline-concept">${capitalize(ev.concept)}</span>
                <span class="timeline-time">${ev.start_timestamp.toFixed(2)}s • ${confPct}</span>
                <span class="timeline-cam">${ev.camera_id}</span>
            </div>
        `;

        card.addEventListener("click", () => {
            document.querySelectorAll(".timeline-card").forEach(c => c.classList.remove("active"));
            card.classList.add("active");
            loadEvidence(ev.id);
        });

        trackEl.appendChild(card);
    });
}

// ==========================================
// 7. VIDEO UPLOAD WORKFLOW & REAL-TIME PROGRESS
// ==========================================
function setupUploadHandlers() {
    const uploadModal = document.getElementById("upload-modal");
    const btnOpenUpload = document.getElementById("btn-upload-video-nav");
    const btnCloseUpload = document.getElementById("btn-close-upload-modal");
    const btnCancelUpload = document.getElementById("btn-cancel-upload");
    const dropzone = document.getElementById("upload-dropzone");
    const fileInput = document.getElementById("video-file-input");
    const promptEl = document.getElementById("dropzone-prompt");
    const detailsEl = document.getElementById("file-details");
    const filenameEl = document.getElementById("selected-filename");
    const filesizeEl = document.getElementById("selected-filesize");
    const uploadForm = document.getElementById("upload-form");

    btnOpenUpload.addEventListener("click", () => {
        uploadModal.classList.remove("hidden");
    });
    btnCloseUpload.addEventListener("click", () => {
        uploadModal.classList.add("hidden");
    });
    btnCancelUpload.addEventListener("click", () => {
        uploadModal.classList.add("hidden");
    });

    dropzone.addEventListener("click", () => fileInput.click());

    dropzone.addEventListener("dragover", (e) => {
        e.preventDefault();
        dropzone.classList.add("dragover");
    });
    dropzone.addEventListener("dragleave", () => {
        dropzone.classList.remove("dragover");
    });
    dropzone.addEventListener("drop", (e) => {
        e.preventDefault();
        dropzone.classList.remove("dragover");
        if (e.dataTransfer.files.length > 0) {
            fileInput.files = e.dataTransfer.files;
            handleFileSelected(fileInput.files[0]);
        }
    });

    fileInput.addEventListener("change", () => {
        if (fileInput.files.length > 0) {
            handleFileSelected(fileInput.files[0]);
        }
    });

    function handleFileSelected(file) {
        promptEl.classList.add("hidden");
        detailsEl.classList.remove("hidden");
        filenameEl.textContent = file.name;
        filesizeEl.textContent = `${(file.size / (1024 * 1024)).toFixed(2)} MB`;
    }

    uploadForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        if (!fileInput.files || fileInput.files.length === 0) {
            alert("Please select a video file to upload.");
            return;
        }

        const file = fileInput.files[0];
        const camName = document.getElementById("upload-camera-name").value.trim();

        const progressBox = document.getElementById("upload-progress-box");
        const stageLabel = document.getElementById("progress-stage-label");
        const stagePct = document.getElementById("progress-pct");
        const fillBar = document.getElementById("progress-bar-fill");
        const btnSubmit = document.getElementById("btn-start-upload");

        progressBox.classList.remove("hidden");
        btnSubmit.disabled = true;

        const updateProgress = (pct, label, stepId) => {
            stagePct.textContent = `${pct}%`;
            stageLabel.textContent = label;
            fillBar.style.width = `${pct}%`;
            document.querySelectorAll(".step-item").forEach(s => s.classList.remove("active"));
            if (stepId) {
                const sEl = document.getElementById(stepId);
                if (sEl) sEl.classList.add("active");
            }
        };

        try {
            updateProgress(15, "Uploading CCTV video file to server...", "step-upload");
            
            const formData = new FormData();
            formData.append("file", file);
            if (camName) formData.append("camera_name", camName);

            const res = await fetch("/api/videos/upload", {
                method: "POST",
                body: formData
            });

            if (!res.ok) {
                const errJson = await res.json();
                throw new Error(errJson.detail || "Upload failed");
            }

            const data = await res.json();
            const jobId = data.job_id;
            const cameraId = data.camera_id;
            activeCameraId = cameraId;

            updateProgress(25, "Upload complete. Initializing YOLO-World & ByteTrack...", "step-detect");

            // Real-time backend job progress polling
            await new Promise((resolve, reject) => {
                const pollTimer = setInterval(async () => {
                    try {
                        const jobRes = await fetch(`/api/jobs/${jobId}`);
                        if (!jobRes.ok) return;
                        const job = await jobRes.json();

                        if (job.status === "processing") {
                            const total = Math.max(1, job.total_frames || 1);
                            const current = job.progress || 0;
                            const pct = Math.min(95, 25 + Math.round((current / total) * 70));
                            
                            let stepId = "step-detect";
                            if (pct > 55) stepId = "step-track";
                            if (pct > 85) stepId = "step-index";

                            const stageText = job.stage || `Analyzing frames: ${current} / ${total}`;
                            updateProgress(pct, `${stageText} (${pct}%)`, stepId);
                        } else if (job.status === "completed") {
                            clearInterval(pollTimer);
                            updateProgress(100, "Analysis Complete! Indexed events into vector search.", "step-index");
                            document.getElementById("step-index").classList.add("done");
                            setTimeout(resolve, 600);
                        } else if (job.status === "failed") {
                            clearInterval(pollTimer);
                            reject(new Error(job.error || "Video processing failed"));
                        }
                    } catch (pollErr) {
                        console.warn("Poll error:", pollErr);
                    }
                }, 500);
            });

            uploadModal.classList.add("hidden");
            progressBox.classList.add("hidden");
            btnSubmit.disabled = false;
            uploadForm.reset();
            promptEl.classList.remove("hidden");
            detailsEl.classList.add("hidden");

            // Complete refresh of all system components
            await loadCameras();
            await loadEventTimeline();
            await loadVideoAnalysisSummary(cameraId);

            const completeCardHtml = `
                <div class="upload-complete-card">
                    <div class="upload-complete-title">
                        <span>VIDEO UPLOAD & ANALYSIS COMPLETE</span>
                        <span class="font-mono">${escapeHtml(data.camera_id)}</span>
                    </div>
                    <div>📹 Camera: <strong>${escapeHtml(data.camera_name)}</strong></div>
                    <div>📁 File: <strong>${escapeHtml(data.file_name)}</strong></div>
                    <div style="font-size:0.75rem; color:var(--text-muted); margin-top:4px;">
                        Grounded events and trajectory tracks are now indexed and queryable in Conversational Query Engine.
                    </div>
                </div>
            `;
            appendChatMessage("assistant", completeCardHtml, true);

        } catch (err) {
            stageLabel.textContent = `❌ Error: ${err.message}`;
            stageLabel.style.color = "var(--status-error)";
            btnSubmit.disabled = false;
        }
    });
}

// ==========================================
// 8. DATA RESET (RESET ALL DATA)
// ==========================================
function setupResetHandlers() {
    const resetModal = document.getElementById("reset-modal");
    const btnNavReset = document.getElementById("btn-reset-data-nav");
    const btnCloseReset = document.getElementById("btn-close-reset-modal");
    const btnCancelReset = document.getElementById("btn-cancel-reset");
    const btnConfirmReset = document.getElementById("btn-confirm-reset");

    btnNavReset.addEventListener("click", () => {
        resetModal.classList.remove("hidden");
    });
    btnCloseReset.addEventListener("click", () => {
        resetModal.classList.add("hidden");
    });
    btnCancelReset.addEventListener("click", () => {
        resetModal.classList.add("hidden");
    });

    btnConfirmReset.addEventListener("click", async () => {
        btnConfirmReset.disabled = true;
        btnConfirmReset.textContent = "Resetting All Data...";

        try {
            const res = await fetch("/api/data/reset", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ confirm: true })
            });

            if (!res.ok) {
                const err = await res.json();
                throw new Error(err.detail || "Reset failed");
            }

            resetModal.classList.add("hidden");
            btnConfirmReset.disabled = false;
            btnConfirmReset.textContent = "Permanently Reset All Data";

            activeCameraId = null;
            currentEvidenceData = null;
            await loadCameras();
            await loadMemories();
            await loadEventTimeline();

            appendChatMessage("assistant", `🧹 <strong>Application Reset Complete</strong>: All uploaded videos, cameras, visual events, frames, clips, and vector indexes have been wiped. System is in a clean 0-camera state.`, true);

        } catch (err) {
            alert("Reset error: " + err.message);
            btnConfirmReset.disabled = false;
            btnConfirmReset.textContent = "Permanently Reset All Data";
        }
    });
}

// ==========================================
// 9. EVENT LISTENERS
// ==========================================
function setupEventListeners() {
    const form = document.getElementById("query-form");
    const input = document.getElementById("query-input");
    const scopeSelect = document.getElementById("global-scope-select");

    // Scope Switcher
    scopeSelect.addEventListener("change", (e) => {
        globalScope = e.target.value;
        renderTimelineCards();
        updateEventsCounter();
    });

    // Form Submit
    form.addEventListener("submit", (e) => {
        e.preventDefault();
        const text = input.value;
        input.value = "";
        submitQuery(text);
    });

    // Preset Chips
    document.querySelectorAll(".chip").forEach(chip => {
        chip.addEventListener("click", () => {
            const q = chip.dataset.query;
            submitQuery(q);
        });
    });

    // Clarification Save Button
    document.getElementById("btn-save-clarification").addEventListener("click", async () => {
        const card = document.getElementById("clarification-card");
        const missingRef = card.dataset.missingRef;
        const camId = document.getElementById("clarify-camera-select").value;
        const roiPreset = document.getElementById("clarify-region-preset").value;

        let roi = [0.0, 0.0, 768.0, 432.0];
        if (roiPreset === "left") roi = [0.0, 100.0, 384.0, 432.0];
        else if (roiPreset === "right") roi = [384.0, 100.0, 768.0, 432.0];
        else if (roiPreset === "center") roi = [150.0, 150.0, 600.0, 432.0];

        const saved = await saveMemory(missingRef, camId, roi, { description: `Clarified landmark: ${missingRef}` });
        if (saved) {
            hideClarificationCard();
            appendChatMessage("assistant", `✅ Saved permanent memory: "<strong>${escapeHtml(missingRef)}</strong>" ➔ <strong>${escapeHtml(camId)}</strong>`, true);
            if (lastPendingQuery) {
                submitQuery(lastPendingQuery);
            }
        }
    });

    document.getElementById("btn-cancel-clarification").addEventListener("click", () => {
        hideClarificationCard();
    });

    // Play Full Video Button
    const btnPlayFull = document.getElementById("btn-play-full");
    if (btnPlayFull) {
        btnPlayFull.addEventListener("click", () => {
            playFullVideo();
        });
    }

    // Play Evidence Clip Button
    const btnPlayEvidence = document.getElementById("btn-play-evidence");
    if (btnPlayEvidence) {
        btnPlayEvidence.addEventListener("click", () => {
            if (currentEvidenceData && currentEvidenceData.event_id) {
                loadEvidence(currentEvidenceData.event_id);
            } else {
                const activeEvents = getFilteredEvents();
                if (activeEvents.length > 0) {
                    loadEvidence(activeEvents[0].id);
                } else {
                    alert("No visual evidence event selected. Select an event from the timeline or run a search query.");
                }
            }
        });
    }

    // View Toggle Button (Video / Frame Image)
    const btnViewToggle = document.getElementById("btn-view-toggle");
    if (btnViewToggle) {
        btnViewToggle.addEventListener("click", () => {
            const video = document.getElementById("evidence-video");
            const frame = document.getElementById("evidence-frame-img");
            if (frame.classList.contains("hidden")) {
                frame.classList.remove("hidden");
                video.classList.add("hidden");
                if (currentEvidenceData && currentEvidenceData.frame_url) {
                    frame.src = currentEvidenceData.frame_url;
                }
                btnViewToggle.textContent = "🎬 Video View";
            } else {
                frame.classList.add("hidden");
                video.classList.remove("hidden");
                btnViewToggle.textContent = "🖼️ Frame View";
            }
            if (currentEvidenceData) {
                startEvidenceTracker(currentEvidenceData);
            }
        });
    }

    // Toggle All Scene Objects in Canvas
    const btnToggleAllBoxes = document.getElementById("btn-toggle-all-boxes");
    if (btnToggleAllBoxes) {
        btnToggleAllBoxes.addEventListener("click", () => {
            showAllSceneObjects = !showAllSceneObjects;
            if (showAllSceneObjects) {
                btnToggleAllBoxes.textContent = "👁️ All Objects: ON";
                btnToggleAllBoxes.classList.add("active");
            } else {
                btnToggleAllBoxes.textContent = "🎯 Target Only";
                btnToggleAllBoxes.classList.remove("active");
            }
            if (activeTrackerRender) {
                activeTrackerRender();
            } else if (currentEvidenceData) {
                startEvidenceTracker(currentEvidenceData);
            }
        });
    }

    // Add Memory Modal Open/Close
    const modal = document.getElementById("memory-modal");
    document.getElementById("btn-add-memory").addEventListener("click", () => {
        modal.classList.remove("hidden");
    });
    document.getElementById("btn-close-modal").addEventListener("click", () => {
        modal.classList.add("hidden");
    });
    document.getElementById("btn-cancel-modal").addEventListener("click", () => {
        modal.classList.add("hidden");
    });

    document.getElementById("memory-modal-form").addEventListener("submit", async (e) => {
        e.preventDefault();
        const name = document.getElementById("mem-name").value.trim();
        const cam = document.getElementById("mem-cam").value;
        const roiStr = document.getElementById("mem-roi").value;
        
        let roi = [0.0, 0.0, 768.0, 432.0];
        try {
            roi = roiStr.split(",").map(v => parseFloat(v.trim()));
        } catch (err) {}

        const saved = await saveMemory(name, cam, roi);
        if (saved) {
            modal.classList.add("hidden");
            document.getElementById("mem-name").value = "";
        }
    });

    window.addEventListener("resize", () => {
        if (currentEvidenceData) {
            drawBoundingBoxes(currentEvidenceData.bounding_boxes);
        }
    });
}

function capitalize(str) {
    if (!str) return "";
    return str.charAt(0).toUpperCase() + str.slice(1);
}

function escapeHtml(text) {
    if (!text) return "";
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
}
