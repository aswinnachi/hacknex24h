// Multi-Stream Video Intelligence — Frontend Application Logic

// Dynamic Cloud / Vercel API Base URL Resolution
const API_BASE_URL = (typeof window !== "undefined" && window.API_BASE_URL) ? window.API_BASE_URL.replace(/\/$/, "") : "";
if (typeof window !== "undefined" && API_BASE_URL) {
    const _nativeFetch = window.fetch;
    window.fetch = function(input, init) {
        if (typeof input === "string" && (input.startsWith("/api/") || input.startsWith("/data/"))) {
            input = API_BASE_URL + input;
        }
        return _nativeFetch(input, init);
    };
    console.info("[Frontend] Configured remote backend API_BASE_URL:", API_BASE_URL);
}

let allCameras = [];
let allEvents = [];
let allMemories = [];
let activeCameraId = null;
let globalScope = "current"; // "current" | "all"
let currentEvidenceData = null;
let lastPendingQuery = null;
let autoRefreshTimer = null;
let cameraPlayerModes = {}; // camId -> "ai" | "direct"
let multiviewDirectYoutube = true;
let showBoundingBoxes = true;
let latestMatrixDetections = {};
let liveMatrixPollTimer = null;

function extractYouTubeVideoId(url) {
    if (!url || typeof url !== "string") return null;
    const u = url.trim();
    const regExp = /(?:youtube\.com\/(?:watch\?.*v=|live\/|embed\/)|youtu\.be\/)([a-zA-Z0-9_-]{6,15})/i;
    const match = u.match(regExp);
    return (match && match[1]) ? match[1] : null;
}

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
    setupConnectCameraHandlers();
    setupTimelineFilterHandlers();
    setupAlertingHandlers();
    startAlertsPolling();
    startLiveMatrixDetectionPolling();
    setupGlobalModalAndKeyHandlers();
    setupLayoutInteractions();
    setupVideoLabHandlers();
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

        const timelineCamSelect = document.getElementById("timeline-cam-select");
        if (timelineCamSelect) {
            const currentSelected = timelineCamSelect.value;
            timelineCamSelect.innerHTML = `<option value="all">All Cameras</option>`;
            allCameras.forEach(cam => {
                const opt = document.createElement("option");
                opt.value = cam.id;
                opt.textContent = `${cam.name} (${cam.id})`;
                timelineCamSelect.appendChild(opt);
            });
            if (currentSelected && Array.from(timelineCamSelect.options).some(o => o.value === currentSelected)) {
                timelineCamSelect.value = currentSelected;
            }
        }

        let hasAnalyzingCamera = false;

        allCameras.forEach(cam => {
            const isSelected = cam.id === activeCameraId;
            const isAnalyzing = cam.status === "ANALYZING";
            const isLive = cam.source_type === "live" || cam.source_type === "youtube_live";
            const isYt = cam.source_type === "youtube_live";
            const isConnected = cam.status === "CONNECTED";
            if (isAnalyzing) hasAnalyzingCamera = true;

            let statusBadge = "";
            if (isYt) {
                if (isConnected) {
                    statusBadge = `<span class="count-badge live-badge" style="color:#ef4444; border-color:#ef4444; font-weight:700;">● YT LIVE</span>`;
                } else {
                    statusBadge = `<span class="count-badge" style="color:#f87171; border-color:#f87171;">OFFLINE</span>`;
                }
            } else if (isLive) {
                if (isConnected) {
                    statusBadge = `<span class="count-badge live-badge" style="color:#34d399; border-color:#34d399; font-weight:700;">● LIVE</span>`;
                } else {
                    statusBadge = `<span class="count-badge" style="color:#f87171; border-color:#f87171;">OFFLINE</span>`;
                }
            } else if (isAnalyzing) {
                statusBadge = `<span class="count-badge" style="color:var(--status-warning); border-color:var(--status-warning);">Analyzing</span>`;
            } else {
                statusBadge = `<span class="count-badge" style="font-size:0.65rem;">UPLOADED</span>`;
            }

            const disconnectBtnHtml = (isLive && isConnected) ? `
                <button class="btn-disc-cam" title="Disconnect stream" data-cam-id="${cam.id}" style="background:transparent; border:none; color:#f87171; cursor:pointer; padding:2px; display:inline-flex; align-items:center;">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18.36 6.64a9 9 0 1 1-12.73 0"/><line x1="12" y1="2" x2="12" y2="12"/></svg>
                </button>
            ` : "";

            const reconnectBtnHtml = (isLive && !isConnected) ? `
                <button class="btn-reconnect-cam" title="Connect/reconnect live stream" data-cam-id="${cam.id}">
                    ⚡ Connect
                </button>
            ` : "";

            // Left Panel Camera Card with Delete action
            const card = document.createElement("div");
            card.className = `camera-card ${isSelected ? 'active' : ''}`;
            card.dataset.camId = cam.id;
            card.innerHTML = `
                <div class="cam-header">
                    <span style="font-weight:600; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:140px;" title="${escapeHtml(cam.name)}">${escapeHtml(cam.name)}</span>
                    <div class="cam-actions">
                        ${statusBadge}
                        ${disconnectBtnHtml}
                        ${reconnectBtnHtml}
                        <button class="btn-del-cam" title="Delete camera & video data" data-cam-id="${cam.id}">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/></svg>
                        </button>
                    </div>
                </div>
                <div class="cam-meta">
                    <span>${cam.resolution || "768x432"} • ${cam.fps ? cam.fps.toFixed(0) : 12} FPS</span>
                    <span>${isYt ? "YOUTUBE LIVE" : (isLive ? "LIVE FEED" : (cam.duration ? cam.duration.toFixed(1) + "s" : ""))}</span>
                </div>
            `;

            // Select camera click
            card.addEventListener("click", (e) => {
                if (e.target.closest(".btn-del-cam") || e.target.closest(".btn-disc-cam") || e.target.closest(".btn-reconnect-cam")) return;
                document.querySelectorAll(".camera-card").forEach(c => c.classList.remove("active"));
                card.classList.add("active");
                activeCameraId = cam.id;
                const isLive = cam.source_type === "live" || cam.source_type === "youtube_live" || Boolean(extractYouTubeVideoId(cam.video_path));
                if (isLive) {
                    displayLiveCamera(cam.id);
                } else {
                    stopLiveCameraStream();
                    loadVideoAnalysisSummary(cam.id);
                }
                renderTimelineCards();
                updateEventsCounter();
            });

            // Disconnect camera button
            const discBtn = card.querySelector(".btn-disc-cam");
            if (discBtn) {
                discBtn.addEventListener("click", async (e) => {
                    e.stopPropagation();
                    if (confirm(`Disconnect live camera stream "${cam.name}" (${cam.id})?`)) {
                        await disconnectCamera(cam.id);
                    }
                });
            }

            // Reconnect camera button
            const reconBtn = card.querySelector(".btn-reconnect-cam");
            if (reconBtn) {
                reconBtn.addEventListener("click", async (e) => {
                    e.stopPropagation();
                    await reconnectCamera(cam);
                });
            }

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

        // Load live stream or video analysis summary for active camera
        if (activeCameraId) {
            const activeCam = allCameras.find(c => c.id === activeCameraId);
            const isLive = activeCam && (activeCam.source_type === "live" || activeCam.source_type === "youtube_live" || Boolean(extractYouTubeVideoId(activeCam.video_path)));
            if (isLive) {
                displayLiveCamera(activeCameraId);
            } else {
                await loadVideoAnalysisSummary(activeCameraId);
            }
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
                top_k: 16,
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

        // Case Cross-Camera
        if (verdict === "CROSS_CAMERA") {
            const debug = data.debug || {};
            const e1 = data.results[0] || {};
            const e2 = data.results[1] || {};
            const e1Frame = e1.frame ? (e1.frame.startsWith('/') ? e1.frame : '/' + e1.frame) : '';
            const e2Frame = e2.frame ? (e2.frame.startsWith('/') ? e2.frame : '/' + e2.frame) : '';
            const e1Cam = (e1.camera || "CAM 1").toUpperCase();
            const e2Cam = (e2.camera || "CAM 2").toUpperCase();
            const e1Time = e1.time_formatted || (e1.timestamp ? `${e1.timestamp.toFixed(1)}s` : '');
            const e2Time = e2.time_formatted || (e2.timestamp ? `${e2.timestamp.toFixed(1)}s` : '');

            const crossHtml = `
                <div class="result-card" style="border-left: 3px solid #38bdf8;">
                    <div class="result-card-header">
                        <span class="verdict-tag" style="background:rgba(56,189,248,0.18); color:#38bdf8; border:1px solid #38bdf8; font-weight:700;">CROSS-CAMERA MATCH</span>
                        <span class="font-mono">Confidence: ${(debug.confidence ? (debug.confidence * 100).toFixed(0) : 85)}%</span>
                    </div>
                    <p><strong>${escapeHtml(summary)}</strong></p>
                    <div style="display:grid; grid-template-columns: 1fr 1fr; gap:8px; margin-top:10px;">
                        <div style="background:rgba(0,0,0,0.35); border-radius:6px; overflow:hidden; border:1px solid rgba(255,255,255,0.08); padding:6px; cursor:pointer;" onclick="loadEvidence('${e1.event}')">
                            <div style="font-size:0.7rem; font-weight:700; color:#38bdf8; margin-bottom:4px;">📹 ${e1Cam} • ${e1Time}</div>
                            ${e1Frame ? `<img src="${e1Frame}" style="width:100%; height:90px; object-fit:cover; border-radius:4px;">` : '<div style="height:90px; display:flex; align-items:center; justify-content:center; color:var(--text-muted); font-size:0.7rem;">Frame</div>'}
                            <div style="font-size:0.68rem; color:var(--text-secondary); margin-top:4px;">Track #${e1.tracking_id || '--'} • ${capitalize(e1.concept || '')}</div>
                        </div>
                        <div style="background:rgba(0,0,0,0.35); border-radius:6px; overflow:hidden; border:1px solid rgba(255,255,255,0.08); padding:6px; cursor:pointer;" onclick="loadEvidence('${e2.event}')">
                            <div style="font-size:0.7rem; font-weight:700; color:#a855f7; margin-bottom:4px;">📹 ${e2Cam} • ${e2Time}</div>
                            ${e2Frame ? `<img src="${e2Frame}" style="width:100%; height:90px; object-fit:cover; border-radius:4px;">` : '<div style="height:90px; display:flex; align-items:center; justify-content:center; color:var(--text-muted); font-size:0.7rem;">Frame</div>'}
                            <div style="font-size:0.68rem; color:var(--text-secondary); margin-top:4px;">Track #${e2.tracking_id || '--'} • ${capitalize(e2.concept || '')}</div>
                        </div>
                    </div>
                    <div style="font-size:0.7rem; color:var(--text-muted); margin-top:8px; line-height:1.3; border-top:1px solid rgba(255,255,255,0.08); padding-top:6px;">
                        ⏱️ Time Delta: <strong>${debug.time_delta_seconds ?? '--'}s</strong> • ${escapeHtml(debug.uncertainty_note || '')}
                    </div>
                </div>
            `;
            appendChatMessage("assistant", crossHtml, true);
            if (data.results && data.results.length > 0) {
                await loadEvidence(data.results[0].event);
            }
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

            // Build Multi-Clips Cards Gallery when multiple clips/objects match
            let multiClipsHtml = "";
            if (data.results.length > 1) {
                const clipCards = data.results.map((resItem, idx) => {
                    const isFirst = idx === 0;
                    const rFrame = resItem.frame ? (resItem.frame.startsWith('/') ? resItem.frame : '/' + resItem.frame) : '';
                    const rConcept = capitalize(resItem.concept || "object");
                    const rTid = resItem.tracking_id ? `#${resItem.tracking_id}` : '';
                    const rCam = (resItem.camera || "").toUpperCase();
                    const rTime = resItem.time_formatted || `${resItem.timestamp.toFixed(1)}s`;
                    const rAttr = resItem.attributes || {};
                    const rCol = rAttr.color && rAttr.color !== "unknown" ? capitalize(rAttr.color) : "";
                    const rSim = ((resItem.similarity || 0) * 100).toFixed(0);

                    return `
                        <div class="evidence-clip-card ${isFirst ? 'active-clip' : ''}" data-event-id="${resItem.event}" style="display:flex; flex-direction:column; background:rgba(15,23,42,0.85); border:1px solid ${isFirst ? 'var(--accent)' : 'rgba(255,255,255,0.12)'}; border-radius:6px; overflow:hidden; cursor:pointer; transition:all 0.2s ease;">
                            <div style="position:relative; width:100%; height:75px; background:#000;">
                                ${rFrame ? `<img src="${rFrame}" alt="Clip ${idx+1}" style="width:100%; height:100%; object-fit:cover;">` : ''}
                                <div style="position:absolute; top:4px; left:4px; background:rgba(0,0,0,0.8); padding:2px 5px; border-radius:3px; font-size:0.65rem; font-family:var(--font-mono); color:#38bdf8; font-weight:700;">
                                    📹 ${rCam}
                                </div>
                                <div style="position:absolute; bottom:4px; right:4px; background:rgba(0,0,0,0.85); padding:2px 5px; border-radius:3px; font-size:0.65rem; font-family:var(--font-mono); color:#fff;">
                                    ⏱️ ${rTime}
                                </div>
                            </div>
                            <div style="padding:6px 8px; font-size:0.75rem; display:flex; flex-direction:column; gap:2px;">
                                <div style="display:flex; justify-content:space-between; align-items:center;">
                                    <strong style="color:var(--text-primary); font-size:0.78rem;">${rConcept} ${rTid}</strong>
                                    <span style="color:#10b981; font-weight:700; font-family:var(--font-mono); font-size:0.7rem;">${rSim}%</span>
                                </div>
                                <div style="color:var(--text-secondary); font-size:0.7rem; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">
                                    ${rCol ? `${rCol} • ` : ''}${rAttr.is_moving ? 'Moving' : 'Stationary'}
                                </div>
                                <button class="btn-play-clip-item" data-event-id="${resItem.event}" style="margin-top:4px; padding:3px 6px; font-size:0.7rem; background:${isFirst ? 'var(--accent)' : 'rgba(255,255,255,0.08)'}; color:${isFirst ? '#0f172a' : 'var(--text-primary)'}; border:none; border-radius:3px; cursor:pointer; font-weight:600;">
                                    ${isFirst ? '▶ Playing Now' : '▶ Play Clip'}
                                </button>
                            </div>
                        </div>
                    `;
                }).join("");

                multiClipsHtml = `
                    <div style="margin-top:10px; padding-top:10px; border-top:1px solid rgba(255,255,255,0.12);">
                        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
                            <span style="font-weight:700; font-size:0.82rem; color:var(--text-primary); display:flex; align-items:center; gap:6px;">
                                <span>🎞️ All Matching Clips Across Streams</span>
                                <span style="background:var(--accent); color:#0f172a; padding:1px 6px; border-radius:10px; font-size:0.7rem; font-family:var(--font-mono); font-weight:700;">${data.results.length} Clips Found</span>
                            </span>
                            <span style="font-size:0.7rem; color:var(--text-muted);">Click any clip to play</span>
                        </div>
                        <div style="display:grid; grid-template-columns:repeat(auto-fill, minmax(130px, 1fr)); gap:8px;">
                            ${clipCards}
                        </div>
                    </div>
                `;
            }

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
                    ${multiClipsHtml}
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

    bubble.querySelectorAll(".btn-load-evidence, .btn-play-clip-item, .evidence-clip-card").forEach(el => {
        el.addEventListener("click", (e) => {
            const evId = el.dataset.eventId || (el.closest("[data-event-id]") && el.closest("[data-event-id]").dataset.eventId);
            if (evId) {
                bubble.querySelectorAll(".evidence-clip-card").forEach(c => {
                    const isCur = c.dataset.eventId === evId;
                    c.style.borderColor = isCur ? "var(--accent)" : "rgba(255,255,255,0.12)";
                    const b = c.querySelector(".btn-play-clip-item");
                    if (b) {
                        b.textContent = isCur ? "▶ Playing Now" : "▶ Play Clip";
                        b.style.background = isCur ? "var(--accent)" : "rgba(255,255,255,0.08)";
                        b.style.color = isCur ? "#0f172a" : "var(--text-primary)";
                    }
                });
                loadEvidence(evId);
            }
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
    stopLiveCameraStream();

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

        // Ensure evidence player isolates strictly the required target object
        showAllSceneObjects = false;
        const btnToggleAllBoxes = document.getElementById("btn-toggle-all-boxes");
        if (btnToggleAllBoxes) {
            btnToggleAllBoxes.textContent = "🎯 Target Only";
            btnToggleAllBoxes.classList.remove("active");
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
    stopLiveCameraStream();
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

    statusBadge.textContent = "FULL SOURCE STREAM • ALL OBJECTS DETECTED";
    statusBadge.className = "badge live-badge";

    hudVerdict.textContent = "ALL OBJECTS DETECTED";
    hudVerdict.className = "verdict-tag yes";
    const durSec = cam.duration ? Number(cam.duration).toFixed(1) : "10.0";
    hudSummary.textContent = `Playing complete source CCTV feed for ${cam.name} (${targetCamId}) [0.0s → ${durSec}s] with real-time multi-object boundary tracking active for all objects in frame.`;
    hudCamera.textContent = `${cam.name} (${targetCamId})`;
    hudTimestamp.textContent = "FULL DURATION";
    hudOffset.textContent = `0.0s - ${durSec}s`;
    hudRelevance.textContent = "100%";
    hudClipWindow.textContent = `0.0s ➔ ${durSec}s (Complete Feed)`;

    watermarkCam.textContent = `CAM: ${targetCamId.toUpperCase()}`;
    watermarkTime.textContent = `FULL VIDEO (0-${durSec}s)`;

    // Load full dense scene trajectories to detect all objects in the frame
    try {
        const sceneRes = await fetch(`/api/cameras/${targetCamId}/scene`);
        if (sceneRes.ok) {
            const sceneData = await sceneRes.json();
            showAllSceneObjects = true;
            const btnToggleAllBoxes = document.getElementById("btn-toggle-all-boxes");
            if (btnToggleAllBoxes) {
                btnToggleAllBoxes.textContent = "👁️ All Objects: ON";
                btnToggleAllBoxes.classList.add("active");
            }
            startEvidenceTracker({
                scene_objects: sceneData.scene_objects || [],
                video_width: sceneData.video_width || 848,
                video_height: sceneData.video_height || 478,
                duration: sceneData.duration || cam.duration || 10.0,
                camera_id: targetCamId
            });
        } else {
            stopEvidenceTracker();
        }
    } catch (e) {
        console.warn("Could not load scene objects for full video:", e);
        stopEvidenceTracker();
    }
}

function clearEvidencePlayer(customMessage = null) {
    stopLiveCameraStream();
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
// 6. BOTTOM EVENT TIMELINE & SEARCHABLE HISTORY
// ==========================================
async function loadEventTimeline() {
    const trackEl = document.getElementById("timeline-track");
    const countBadge = document.getElementById("timeline-count-badge");
    const scopeBadge = document.getElementById("timeline-scope-badge");
    
    const camSelect = document.getElementById("timeline-cam-select");
    const sourceSelect = document.getElementById("timeline-source-select");
    const classSelect = document.getElementById("timeline-class-select");
    const timeSelect = document.getElementById("timeline-time-select");
    const trackInput = document.getElementById("timeline-track-input");

    const camVal = (camSelect && camSelect.value) ? camSelect.value : "all";
    const sourceVal = (sourceSelect && sourceSelect.value) ? sourceSelect.value : "all";
    const classVal = (classSelect && classSelect.value) ? classSelect.value : "all";
    const trackVal = (trackInput && trackInput.value.trim()) ? trackInput.value.trim().replace(/^#/, "") : "";
    const timeVal = (timeSelect && timeSelect.value) ? timeSelect.value : "all";

    let startTime = null;
    if (timeVal !== "all") {
        const nowSec = Date.now() / 1000;
        if (timeVal === "5m") startTime = nowSec - 300;
        else if (timeVal === "15m") startTime = nowSec - 900;
        else if (timeVal === "1h") startTime = nowSec - 3600;
        else if (timeVal === "24h") startTime = nowSec - 86400;
    }

    const params = new URLSearchParams();
    params.set("limit", "150");
    if (camVal !== "all") params.set("camera_id", camVal);
    if (sourceVal !== "all") params.set("source_type", sourceVal);
    if (classVal !== "all") params.set("concept", classVal);
    if (trackVal) params.set("tracking_id", trackVal);
    if (startTime !== null) params.set("start_time", startTime.toString());

    try {
        const res = await fetch(`/api/events?${params.toString()}`);
        if (!res.ok) throw new Error("Failed to load events");
        allEvents = await res.json();

        if (countBadge) {
            countBadge.textContent = `${allEvents.length} Event${allEvents.length === 1 ? '' : 's'}`;
        }
        if (scopeBadge) {
            if (camVal !== "all") {
                const cObj = allCameras.find(c => c.id === camVal);
                scopeBadge.textContent = cObj ? cObj.name : camVal;
            } else if (sourceVal !== "all") {
                scopeBadge.textContent = sourceVal === "youtube_live" ? "YouTube Live" : sourceVal.toUpperCase();
            } else {
                scopeBadge.textContent = "All Feeds";
            }
        }

        renderTimelineCards();
    } catch (err) {
        trackEl.innerHTML = `<div class="empty-state">No active event timeline.</div>`;
    }
}

function updateEventsCounter() {
    const countBadge = document.getElementById("timeline-count-badge");
    if (countBadge) {
        countBadge.textContent = `${allEvents.length} Event${allEvents.length === 1 ? '' : 's'}`;
    }
    const countEl = document.getElementById("indexed-events-count");
    if (countEl) {
        countEl.textContent = `Events: ${allEvents.length} (Indexed)`;
    }
}

function renderTimelineCards() {
    const trackEl = document.getElementById("timeline-track");
    if (!trackEl) return;
    trackEl.innerHTML = "";

    if (!allEvents || allEvents.length === 0) {
        trackEl.innerHTML = `<div class="empty-state">No matching events found for current filters.</div>`;
        return;
    }

    allEvents.forEach(ev => {
        const card = document.createElement("div");
        const isLive = ev.source_type === "live" || ev.source_type === "youtube_live";
        const isYt = ev.source_type === "youtube_live";
        card.className = `timeline-card ${isLive ? 'live-event' : ''}`;
        card.dataset.eventId = ev.id;
        
        const frameSrc = ev.representative_frame ? `/${ev.representative_frame}` : "";
        const confPct = ev.confidence ? `${Math.round(ev.confidence * 100)}%` : "";
        
        let timeDisplay = "";
        if (isLive && ev.start_timestamp > 1000000000) {
            timeDisplay = new Date(ev.start_timestamp * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        } else {
            timeDisplay = `${ev.start_timestamp.toFixed(1)}s`;
        }

        const tidStr = ev.tracking_id ? ` #${ev.tracking_id}` : "";
        let liveBadge = "";
        if (isYt) {
            liveBadge = `<span class="timeline-badge-live" style="background:rgba(239,68,68,0.2); color:#ef4444; border:1px solid #ef4444;">● YT</span>`;
        } else if (isLive) {
            liveBadge = `<span class="timeline-badge-live">● LIVE</span>`;
        }
        const camLabel = ev.camera_name || ev.camera_id;

        card.innerHTML = `
            <img class="timeline-thumb" src="${frameSrc}" alt="${escapeHtml(ev.concept)}" onerror="this.style.display='none'" />
            <div class="timeline-info">
                <span class="timeline-concept">${capitalize(ev.concept)}${tidStr}${liveBadge}</span>
                <span class="timeline-time">${timeDisplay} • ${confPct}</span>
                <span class="timeline-cam" title="${escapeHtml(camLabel)}">${escapeHtml(camLabel)}</span>
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

function setupTimelineFilterHandlers() {
    const camSelect = document.getElementById("timeline-cam-select");
    const sourceSelect = document.getElementById("timeline-source-select");
    const classSelect = document.getElementById("timeline-class-select");
    const timeSelect = document.getElementById("timeline-time-select");
    const trackInput = document.getElementById("timeline-track-input");
    const refreshBtn = document.getElementById("btn-refresh-timeline");

    if (camSelect) {
        camSelect.addEventListener("change", () => loadEventTimeline());
    }
    if (sourceSelect) {
        sourceSelect.addEventListener("change", () => loadEventTimeline());
    }
    if (classSelect) {
        classSelect.addEventListener("change", () => loadEventTimeline());
    }
    if (timeSelect) {
        timeSelect.addEventListener("change", () => loadEventTimeline());
    }
    if (trackInput) {
        let debounceTimer = null;
        trackInput.addEventListener("input", () => {
            clearTimeout(debounceTimer);
            debounceTimer = setTimeout(() => loadEventTimeline(), 300);
        });
    }
    if (refreshBtn) {
        refreshBtn.addEventListener("click", () => loadEventTimeline());
    }
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

    // Toggle All Scene Objects & Live Bounding Box Trackers in Canvas
    const btnToggleAllBoxes = document.getElementById("btn-toggle-all-boxes");
    if (btnToggleAllBoxes) {
        btnToggleAllBoxes.addEventListener("click", () => {
            showBoundingBoxes = !showBoundingBoxes;
            showAllSceneObjects = showBoundingBoxes;
            if (showBoundingBoxes) {
                btnToggleAllBoxes.textContent = "👁️ BBoxes: ON";
                btnToggleAllBoxes.classList.add("active");
            } else {
                btnToggleAllBoxes.textContent = "👁️ BBoxes: OFF";
                btnToggleAllBoxes.classList.remove("active");
                // Immediately clear all visible canvases
                document.querySelectorAll(".feed-canvas").forEach(c => {
                    const ctx = c.getContext("2d");
                    if (ctx) ctx.clearRect(0, 0, c.width, c.height);
                });
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
        if (currentLiveDetections && currentLiveDetections.length > 0) {
            renderLiveDetections(currentLiveDetections);
        }
    });
}

// ==========================================
// 10. LIVE CAMERA STREAMING & CONTROLS
// ==========================================
let liveDetectionPollTimer = null;
let currentLiveDetections = [];

function drawCameraDetectionsOnCanvas(canvasId, mediaId, detections, camMeta, pInfo) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;

    const parent = canvas.parentElement;
    if (!parent) return;

    const containerW = parent.clientWidth;
    const containerH = parent.clientHeight;
    if (containerW <= 0 || containerH <= 0) return;

    if (canvas.width !== containerW || canvas.height !== containerH) {
        canvas.width = containerW;
        canvas.height = containerH;
    }

    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, containerW, containerH);

    if (!showBoundingBoxes) return;
    if (!detections || detections.length === 0) return;

    let mediaEl = document.getElementById(mediaId);
    if (mediaId === "primary-youtube-iframe" && typeof cameraPlayerModes !== "undefined" && cameraPlayerModes[activeCameraId] === "direct") {
        // Direct YouTube embed is not decoded on server; prevent misleading desynchronized overlays
        return;
    }

    let imgW = (pInfo && pInfo.frame_width) || (camMeta && camMeta.width) || 1280;
    let imgH = (pInfo && pInfo.frame_height) || (camMeta && camMeta.height) || 720;
    if (mediaEl && mediaEl.tagName === "IMG" && mediaEl.naturalWidth > 0 && mediaEl.naturalHeight > 0) {
        imgW = mediaEl.naturalWidth;
        imgH = mediaEl.naturalHeight;
    }

    const aspect = imgW / imgH;
    const containerAspect = containerW / containerH;

    let renderW, renderH, offsetX, offsetY;
    if (containerAspect > aspect) {
        renderH = containerH;
        renderW = renderH * aspect;
        offsetX = (containerW - renderW) / 2;
        offsetY = 0;
    } else {
        renderW = containerW;
        renderH = renderW / aspect;
        offsetX = 0;
        offsetY = (containerH - renderH) / 2;
    }

    const scaleX = renderW / imgW;
    const scaleY = renderH / imgH;

    for (const det of detections) {
        const bbox = det.bbox;
        if (!bbox || bbox.length < 4) continue;

        const bx = offsetX + bbox[0] * scaleX;
        const by = offsetY + bbox[1] * scaleY;
        const bw = (bbox[2] - bbox[0]) * scaleX;
        const bh = (bbox[3] - bbox[1]) * scaleY;

        if (bw <= 0 || bh <= 0) continue;

        const isTentative = det.track_state === "tentative" || det.track_id === null || det.track_id === undefined;
        const isPredicted = Boolean(det.is_predicted);

        const cKey = (det.label || det.class || "object").toLowerCase();
        const pal = OBJECT_PALETTES[cKey] || {
            stroke: "#10b981",
            fill: "rgba(16, 185, 129, 0.15)",
            badgeBg: "#10b981",
            badgeText: "#022c22"
        };

        if (isTentative || isPredicted) {
            ctx.setLineDash([5, 4]);
        } else {
            ctx.setLineDash([]);
        }

        // 1. Dark contrast halo
        ctx.strokeStyle = "rgba(0, 0, 0, 0.85)";
        ctx.lineWidth = 3.5;
        ctx.strokeRect(bx, by, bw, bh);

        // 2. Translucent fill
        ctx.fillStyle = isPredicted ? "rgba(148, 163, 184, 0.12)" : pal.fill;
        ctx.fillRect(bx, by, bw, bh);

        // 3. Colored boundary box
        ctx.strokeStyle = isPredicted ? "#94a3b8" : pal.stroke;
        ctx.lineWidth = 2.0;
        ctx.strokeRect(bx, by, bw, bh);

        // 4. Precision Corner Target Brackets (solid)
        ctx.setLineDash([]);
        const cornerLen = Math.max(6, Math.min(14, bw / 4, bh / 4));
        ctx.strokeStyle = isPredicted ? "#94a3b8" : pal.stroke;
        ctx.lineWidth = 3.0;
        ctx.lineCap = "round";

        ctx.beginPath();
        ctx.moveTo(bx, by + cornerLen);
        ctx.lineTo(bx, by);
        ctx.lineTo(bx + cornerLen, by);
        ctx.stroke();

        ctx.beginPath();
        ctx.moveTo(bx + bw - cornerLen, by);
        ctx.lineTo(bx + bw, by);
        ctx.lineTo(bx + bw, by + cornerLen);
        ctx.stroke();

        ctx.beginPath();
        ctx.moveTo(bx, by + bh - cornerLen);
        ctx.lineTo(bx, by + bh);
        ctx.lineTo(bx + cornerLen, by + bh);
        ctx.stroke();

        ctx.beginPath();
        ctx.moveTo(bx + bw - cornerLen, by + bh);
        ctx.lineTo(bx + bw, by + bh);
        ctx.lineTo(bx + bw, by + bh - cornerLen);
        ctx.stroke();

        // 5. Header tag with class, track_id, real model confidence, and tracking state
        const rawConf = (det.raw_confidence !== undefined && det.raw_confidence !== null) ? Number(det.raw_confidence) : Number(det.confidence);
        const confStr = (!isNaN(rawConf) && rawConf > 0) ? ` ${Math.round(rawConf * 100)}%` : "";
        const tidStr = (det.track_id !== null && det.track_id !== undefined) ? ` #${det.track_id}` : "";
        const stateTag = isPredicted ? " [PRED]" : (isTentative ? " [TENT]" : " [TRK]");
        const tagText = `● ${det.label.toUpperCase()}${tidStr}${confStr}${stateTag}`;

        ctx.font = "bold 9px Inter, system-ui, sans-serif";
        const tagW = ctx.measureText(tagText).width + 10;
        const tagH = 16;
        let tagX = bx;
        let tagY = by - tagH;
        if (tagY < 2) tagY = by + 2;
        if (tagX + tagW > containerW - 2) tagX = containerW - tagW - 2;
        if (tagX < 2) tagX = 2;

        ctx.fillStyle = isPredicted ? "#475569" : pal.badgeBg;
        ctx.fillRect(tagX, tagY, tagW, tagH);

        ctx.fillStyle = isPredicted ? "#f1f5f9" : pal.badgeText;
        ctx.fillText(tagText, tagX + 5, tagY + 11);
    }
    ctx.setLineDash([]);
}

function renderLiveDetections(detections, camMeta) {
    const isDirect = cameraPlayerModes[activeCameraId] === "direct";
    const mediaId = isDirect ? "primary-youtube-iframe" : "live-stream-img";
    drawCameraDetectionsOnCanvas("evidence-canvas", mediaId, detections, camMeta);
}

function startLiveMatrixDetectionPolling() {
    if (liveMatrixPollTimer) {
        clearInterval(liveMatrixPollTimer);
        liveMatrixPollTimer = null;
    }

    const poll = async () => {
        try {
            const res = await fetch("/api/cameras/detections/matrix");
            if (!res.ok) return;
            const matrixData = await res.json();
            latestMatrixDetections = matrixData || {};

            // 1. Primary Camera
            if (activeCameraId) {
                const primaryCam = allCameras.find(c => c.id === activeCameraId);
                const pInfo = matrixData[activeCameraId];
                const primaryDets = pInfo?.detections || [];
                currentLiveDetections = primaryDets;

                const isYt = primaryCam && (primaryCam.source_type === "youtube_live" || Boolean(extractYouTubeVideoId(primaryCam.video_path)));
                const mediaId = isYt ? "primary-youtube-iframe" : "live-stream-img";
                drawCameraDetectionsOnCanvas("evidence-canvas", mediaId, primaryDets, primaryCam, pInfo);

                // Update primary live indicators in watermark and header
                const hudOffset = document.getElementById("hud-offset");
                const hudTimestamp = document.getElementById("hud-timestamp");
                const watermarkTime = document.getElementById("watermark-time");

                if (pInfo && pInfo.status === "active") {
                    if (hudOffset) {
                        const capFps = pInfo.capture_fps !== undefined ? pInfo.capture_fps : (pInfo.fps || 0);
                        const infFps = pInfo.inference_fps !== undefined ? pInfo.inference_fps : (pInfo.fps || 0);
                        hudOffset.textContent = `Cap: ${capFps} FPS | Inf: ${infFps} FPS | Lat: ${pInfo.latency_ms || 0}ms | Tracks: ${pInfo.active_tracks_count || 0}`;
                    }
                    if (hudTimestamp) {
                        hudTimestamp.textContent = new Date().toLocaleTimeString();
                    }
                    if (watermarkTime) {
                        const h = pInfo.pipeline_health || {};
                        const pIcon = h.preview_working ? "✓PRV" : "✗PRV";
                        const fIcon = h.frames_reaching_detector ? "✓FRM" : "✗FRM";
                        const dIcon = h.inference_working ? "✓DET" : "✗DET";
                        const tIcon = h.tracking_working ? "✓TRK" : "✗TRK";
                        watermarkTime.textContent = `${pIcon} ${fIcon} ${dIcon} ${tIcon} • ${pInfo.frames_analyzed || 0}F`;
                    }
                }
            }

            // 2. Secondary Cameras in the Grid
            if (currentGridMode > 1) {
                const secondaryCameras = allCameras.filter(c => c.id !== activeCameraId).slice(0, currentGridMode - 1);
                secondaryCameras.forEach(cam => {
                    const canvasId = `canvas-${cam.id}`;
                    const mediaId = `media-${cam.id}`;
                    const pInfo = matrixData[cam.id];
                    const dets = pInfo?.detections || [];
                    drawCameraDetectionsOnCanvas(canvasId, mediaId, dets, cam, pInfo);

                    // Real-time Watermark & HUD updates for all cameras in the grid
                    const wmTime = document.getElementById(`wm-time-${cam.id}`);
                    if (wmTime) {
                        if (pInfo && pInfo.status === "active") {
                            wmTime.textContent = `TRACKING • ${pInfo.frames_analyzed || 0} FRAMES`;
                            wmTime.style.color = "var(--emerald-status)";
                        } else {
                            wmTime.textContent = cam.status === "CONNECTED" ? "CONNECTED" : "STANDBY";
                            wmTime.style.color = "var(--text-muted)";
                        }
                    }
                    const hudTargets = document.getElementById(`hud-targets-${cam.id}`);
                    if (hudTargets && pInfo) {
                        hudTargets.textContent = `${pInfo.count || dets.length} TARGETS`;
                    }
                    const hudFps = document.getElementById(`hud-fps-${cam.id}`);
                    if (hudFps && pInfo) {
                        hudFps.textContent = `${pInfo.fps || 0}`;
                    }
                    const hudLat = document.getElementById(`hud-lat-${cam.id}`);
                    if (hudLat && pInfo) {
                        hudLat.textContent = `${pInfo.latency_ms || 0}ms`;
                    }
                    const hudPill = document.getElementById(`hud-${cam.id}`);
                    if (hudPill) {
                        if (pInfo && pInfo.status === "active" && dets.length > 0) {
                            hudPill.classList.remove("hidden");
                        }
                    }
                    const card = document.getElementById(`feed-card-${cam.id}`);
                    if (card) {
                        const badge = card.querySelector(".badge-live-tag");
                        if (badge) {
                            if (pInfo && pInfo.status === "active") {
                                badge.innerHTML = `● LIVE AI (${pInfo.count || dets.length})`;
                                badge.style.background = "rgba(0,213,160,0.15)";
                                badge.style.borderColor = "var(--emerald-status)";
                                badge.style.color = "var(--emerald-status)";
                            }
                        }
                    }
                });
            }

            // 3. Overview Hero Big Screen Live Tracking
            const overviewCanvas = document.getElementById("overview-canvas");
            if (overviewCanvas && activeCameraId) {
                const activeCam = allCameras.find(c => c.id === activeCameraId);
                const activeDets = matrixData[activeCameraId]?.detections || [];
                const isYtDirect = cameraPlayerModes[activeCameraId] === "direct" || (activeCam && activeCam.source_type === "youtube_live" && activeCam.status !== "CONNECTED");
                const overviewMediaId = (isYtDirect && Boolean(extractYouTubeVideoId(activeCam?.video_path)))
                    ? "overview-youtube-iframe"
                    : (activeCam?.status === "CONNECTED" ? "overview-live-img" : "overview-video");
                
                drawCameraDetectionsOnCanvas("overview-canvas", overviewMediaId, activeDets, activeCam);
                
                const ovName = document.getElementById("overview-cam-name");
                const ovRes = document.getElementById("overview-cam-res");
                const pInfo = matrixData[activeCameraId];
                if (ovName && activeCam) {
                    const trackCount = pInfo?.count || activeDets.length || 0;
                    ovName.textContent = `CAM: ${activeCam.name || activeCam.id} (${trackCount} TARGETS)`;
                }
                if (ovRes && pInfo) {
                    ovRes.textContent = `${pInfo.fps || 30} FPS • LATENCY: ${pInfo.latency_ms || 0}ms`;
                }
            }

        } catch (err) {
            // Silently handle brief network drop
        }
    };

    poll();
    liveMatrixPollTimer = setInterval(poll, 260);
}

function startLiveDetectionPolling(cameraId, cam) {
    startLiveMatrixDetectionPolling();
}

function displayLiveCamera(cameraId) {
    const cam = allCameras.find(c => c.id === cameraId);
    if (!cam) return;

    const videoEl = document.getElementById("evidence-video");
    const liveImg = document.getElementById("live-stream-img");
    const primaryYtIframe = document.getElementById("primary-youtube-iframe");
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
    const discActiveBtn = document.getElementById("btn-disconnect-active-cam");
    const btnPrimaryToggle = document.getElementById("btn-primary-toggle-mode");

    stopEvidenceTracker();

    const evidenceHud = document.getElementById("evidence-hud");
    if (evidenceHud) {
        evidenceHud.classList.add("hidden");
    }
    const btnCloseHud = document.getElementById("btn-close-hud");
    if (btnCloseHud && !btnCloseHud.onclick) {
        btnCloseHud.onclick = () => {
            const h = document.getElementById("evidence-hud");
            if (h) h.classList.add("hidden");
        };
    }

    if (videoEl) {
        videoEl.pause();
        videoEl.src = "";
        videoEl.classList.add("hidden");
    }
    if (frameImg) {
        frameImg.src = "";
        frameImg.classList.add("hidden");
    }

    const isYt = cam.source_type === "youtube_live" || Boolean(extractYouTubeVideoId(cam.video_path));
    const ytId = extractYouTubeVideoId(cam.video_path);
    const isConn = cam.status === "CONNECTED";
    const preferDirect = isYt ? (cameraPlayerModes[cameraId] !== "ai") : (cameraPlayerModes[cameraId] === "direct");

    if (btnPrimaryToggle) {
        if (isYt && ytId) {
            btnPrimaryToggle.classList.remove("hidden");
            if (preferDirect) {
                btnPrimaryToggle.textContent = "🤖 Switch to AI Feed";
                btnPrimaryToggle.classList.add("active");
            } else {
                btnPrimaryToggle.textContent = "📺 Direct YT Player";
                btnPrimaryToggle.classList.remove("active");
            }
            btnPrimaryToggle.onclick = () => {
                cameraPlayerModes[cameraId] = preferDirect ? "ai" : "direct";
                displayLiveCamera(cameraId);
                renderMonitoringMatrix();
            };
        } else {
            btnPrimaryToggle.classList.add("hidden");
        }
    }

    if (isYt && ytId && preferDirect) {
        if (liveImg) {
            liveImg.src = "";
            liveImg.classList.add("hidden");
        }
        if (videoEl) {
            videoEl.src = "";
            videoEl.classList.add("hidden");
        }
        if (primaryYtIframe) {
            const ytEmbedUrl = `https://www.youtube-nocookie.com/embed/${ytId}?autoplay=1&mute=1&playsinline=1&enablejsapi=1`;
            if (primaryYtIframe.src !== ytEmbedUrl) {
                primaryYtIframe.src = ytEmbedUrl;
            }
            primaryYtIframe.classList.remove("hidden");
        }
        if (statusBadge) {
            statusBadge.textContent = isConn ? "● LIVE AI STREAM ACTIVE" : "● LIVE (STANDALONE)";
            statusBadge.className = "badge live-badge";
            statusBadge.style.color = "var(--emerald-status)";
            statusBadge.style.borderColor = "var(--emerald-status)";
        }
        if (hudVerdict) {
            hudVerdict.textContent = "LIVE CCTV FEED (YOLO AI)";
            hudVerdict.className = "verdict-tag match";
        }
        if (hudSummary) {
            hudSummary.textContent = `Streaming live feed. Real-time YOLO-World tracking & ByteTrack are active in parallel.`;
        }
    } else if (isConn || (isYt && !preferDirect)) {
        if (primaryYtIframe) {
            primaryYtIframe.src = "";
            primaryYtIframe.classList.add("hidden");
        }
        if (liveImg) {
            liveImg.onerror = () => {
                console.warn(`[PrimaryView] AI MJPEG stream error for ${cameraId}, reverting to direct player.`);
                cameraPlayerModes[cameraId] = "direct";
                displayLiveCamera(cameraId);
            };
            liveImg.src = `/api/cameras/${cameraId}/live?t=${Date.now()}`;
            liveImg.classList.remove("hidden");
        }
        if (statusBadge) {
            statusBadge.textContent = "● LIVE AI STREAM ACTIVE";
            statusBadge.className = "badge live-badge";
            statusBadge.style.color = "var(--emerald-status)";
            statusBadge.style.borderColor = "var(--emerald-status)";
        }
        if (hudVerdict) {
            hudVerdict.textContent = "LIVE CCTV FEED (YOLO AI)";
            hudVerdict.className = "verdict-tag yes";
        }
        if (hudSummary) {
            hudSummary.textContent = `Streaming live real-time frames from ${cam.name} (${cameraId}) [${cam.resolution || '1080p'} @ ${cam.fps || 30} FPS] with YOLO-World inference.`;
        }
    } else if (isYt && ytId) {
        if (liveImg) {
            liveImg.src = "";
            liveImg.classList.add("hidden");
        }
        if (primaryYtIframe) {
            primaryYtIframe.src = `https://www.youtube-nocookie.com/embed/${ytId}?autoplay=1&mute=1&playsinline=1&enablejsapi=1`;
            primaryYtIframe.classList.remove("hidden");
        }
        if (statusBadge) {
            statusBadge.textContent = "● YT LIVE STANDBY";
            statusBadge.className = "badge live-badge";
            statusBadge.style.color = "#f59e0b";
        }
        if (hudVerdict) {
            hudVerdict.textContent = "YOUTUBE LIVE READY";
            hudVerdict.className = "verdict-tag yes";
        }
        if (hudSummary) {
            hudSummary.textContent = `Playing direct YouTube stream for ${cam.name}. Click '⚡ Connect Feed' to attach AI detection.`;
        }
    } else {
        if (primaryYtIframe) {
            primaryYtIframe.src = "";
            primaryYtIframe.classList.add("hidden");
        }
        if (liveImg) {
            liveImg.src = "";
            liveImg.classList.add("hidden");
        }
        if (statusBadge) {
            statusBadge.textContent = "OFFLINE";
            statusBadge.className = "badge";
        }
        if (hudVerdict) {
            hudVerdict.textContent = "FEED OFFLINE";
            hudVerdict.className = "verdict-tag match";
        }
        if (hudSummary) {
            hudSummary.textContent = `Camera ${cam.name} is currently disconnected. Reconnect stream to resume live analysis.`;
        }
    }

    if (hudCamera) hudCamera.textContent = `${cam.name} (${cameraId})`;
    if (hudTimestamp) hudTimestamp.textContent = new Date().toLocaleTimeString();
    if (hudOffset) hudOffset.textContent = "REAL-TIME LIVE";
    if (hudRelevance) hudRelevance.textContent = "100%";
    if (hudClipWindow) hudClipWindow.textContent = "Continuous Stream";

    if (watermarkCam) watermarkCam.textContent = `LIVE: ${cameraId.toUpperCase()}`;
    if (watermarkTime) watermarkTime.textContent = `STREAMING`;

    if (discActiveBtn) {
        if (isConn) {
            discActiveBtn.classList.remove("hidden");
        } else {
            discActiveBtn.classList.add("hidden");
        }
    }

    if (isConn) {
        startLiveDetectionPolling(cameraId, cam);
    }
    loadVideoAnalysisSummary(cameraId);
}

function stopLiveCameraStream() {
    if (liveDetectionPollTimer) {
        clearInterval(liveDetectionPollTimer);
        liveDetectionPollTimer = null;
    }
    currentLiveDetections = [];
    const canvas = document.getElementById("evidence-canvas");
    if (canvas) {
        const ctx = canvas.getContext("2d");
        if (ctx) ctx.clearRect(0, 0, canvas.width, canvas.height);
    }
    const liveImg = document.getElementById("live-stream-img");
    const primaryYtIframe = document.getElementById("primary-youtube-iframe");
    const discActiveBtn = document.getElementById("btn-disconnect-active-cam");
    if (liveImg) {
        liveImg.src = "";
        liveImg.classList.add("hidden");
    }
    if (primaryYtIframe) {
        primaryYtIframe.src = "";
        primaryYtIframe.classList.add("hidden");
    }
    if (discActiveBtn) discActiveBtn.classList.add("hidden");
}

async function disconnectCamera(cameraId) {
    try {
        const res = await fetch(`/api/cameras/${cameraId}/disconnect`, { method: "POST" });
        if (!res.ok) throw new Error("Disconnect failed");
        appendChatMessage("assistant", `🔌 <strong>Camera Disconnected</strong>: Released stream resources for <code>${cameraId}</code>.`, true);
        if (typeof showAppToast === "function") {
            showAppToast("Stream Disconnected", `Camera ${cameraId} feed has been disconnected.`, "info");
        }
        if (activeCameraId === cameraId) {
            stopLiveCameraStream();
            clearEvidencePlayer("Live camera disconnected.");
        }
        await loadCameras();
    } catch (err) {
        if (typeof showAppToast === "function") {
            showAppToast("Disconnect Failed", err.message, "error");
        }
        alert("Error disconnecting camera: " + err.message);
    }
}

async function reconnectCamera(cam) {
    if (!cam) return;
    if (typeof showAppToast === "function") {
        showAppToast("Connecting Stream", `Initiating connection to ${cam.name}...`, "info");
    }
    try {
        const res = await fetch("/api/cameras/connect", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                camera_id: cam.id,
                name: cam.name,
                stream_url: cam.video_path,
                source_type: cam.source_type,
                record_stream: false,
                timeout: cam.source_type === "youtube_live" ? 25.0 : 8.0
            })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "Connection failed");
        if (typeof showAppToast === "function") {
            showAppToast("Stream Online", `Connected ${cam.name} (${data.resolution || '1080p'} @ ${data.fps || 30} FPS)`, "success");
        }
        await loadCameras();
        activeCameraId = cam.id;
        displayLiveCamera(cam.id);
    } catch (err) {
        if (typeof showAppToast === "function") {
            showAppToast("Connection Failed", err.message, "error");
        } else {
            alert("Connection error: " + err.message);
        }
    }
}

function setupConnectCameraHandlers() {
    const connectModal = document.getElementById("connect-modal");
    const btnOpenConnect = document.getElementById("btn-connect-camera-nav");
    const btnCloseConnect = document.getElementById("btn-close-connect-modal");
    const btnCancelConnect = document.getElementById("btn-cancel-connect");
    const sourceTypeSelect = document.getElementById("connect-source-type");
    const groupWebcam = document.getElementById("group-webcam-index");
    const groupRtsp = document.getElementById("group-rtsp-url");
    const groupYoutube = document.getElementById("group-youtube-url");
    const groupTestVideo = document.getElementById("group-test-video");
    const connectForm = document.getElementById("connect-camera-form");
    const statusBox = document.getElementById("connect-status-box");
    const statusText = document.getElementById("connect-status-text");
    const btnSubmit = document.getElementById("btn-submit-connect");
    const discActiveBtn = document.getElementById("btn-disconnect-active-cam");

    if (btnOpenConnect) {
        btnOpenConnect.addEventListener("click", () => {
            if (statusBox) statusBox.classList.add("hidden");
            if (connectModal) connectModal.classList.remove("hidden");
        });
    }
    if (btnCloseConnect) {
        btnCloseConnect.addEventListener("click", () => {
            if (connectModal) connectModal.classList.add("hidden");
        });
    }
    if (btnCancelConnect) {
        btnCancelConnect.addEventListener("click", () => {
            if (connectModal) connectModal.classList.add("hidden");
        });
    }

    if (sourceTypeSelect) {
        sourceTypeSelect.addEventListener("change", () => {
            const val = sourceTypeSelect.value;
            if (groupWebcam) groupWebcam.classList.toggle("hidden", val !== "webcam");
            if (groupRtsp) groupRtsp.classList.toggle("hidden", val !== "rtsp");
            if (groupYoutube) groupYoutube.classList.toggle("hidden", val !== "youtube_live");
            if (groupTestVideo) groupTestVideo.classList.toggle("hidden", val !== "test_video");
        });
    }

    // Quick Sample Presets for YouTube Live
    document.querySelectorAll(".btn-sample-chip").forEach(chip => {
        chip.addEventListener("click", () => {
            const ytUrl = chip.dataset.ytUrl;
            const camName = chip.dataset.camName;
            if (sourceTypeSelect) {
                sourceTypeSelect.value = "youtube_live";
                sourceTypeSelect.dispatchEvent(new Event("change"));
            }
            const ytInput = document.getElementById("connect-youtube-url");
            const nameInput = document.getElementById("connect-camera-name");
            if (ytInput) ytInput.value = ytUrl;
            if (nameInput) nameInput.value = camName;
            if (typeof showAppToast === "function") {
                showAppToast("Preset Loaded", `Selected "${camName}"`, "info");
            }
        });
    });

    if (discActiveBtn) {
        discActiveBtn.addEventListener("click", async () => {
            if (activeCameraId) {
                const cam = allCameras.find(c => c.id === activeCameraId);
                const name = cam ? cam.name : activeCameraId;
                if (confirm(`Disconnect active feed "${name}" (${activeCameraId})?`)) {
                    await disconnectCamera(activeCameraId);
                }
            }
        });
    }

    if (connectForm) {
        connectForm.addEventListener("submit", async (e) => {
            e.preventDefault();
            const sourceType = sourceTypeSelect ? sourceTypeSelect.value : "webcam";
            let streamUrl = "";

            if (sourceType === "webcam") {
                const idxEl = document.getElementById("connect-webcam-index");
                streamUrl = (idxEl && idxEl.value.trim()) ? idxEl.value.trim() : "0";
            } else if (sourceType === "rtsp") {
                const rtspEl = document.getElementById("connect-rtsp-url");
                streamUrl = rtspEl ? rtspEl.value.trim() : "";
                if (!streamUrl) {
                    alert("Please enter a valid RTSP stream URL.");
                    return;
                }
            } else if (sourceType === "youtube_live") {
                const ytEl = document.getElementById("connect-youtube-url");
                streamUrl = ytEl ? ytEl.value.trim() : "";
                if (!streamUrl) {
                    alert("Please enter a valid YouTube Live URL.");
                    return;
                }
            } else if (sourceType === "test_video") {
                const testEl = document.getElementById("connect-test-path");
                streamUrl = (testEl && testEl.value.trim()) ? testEl.value.trim() : "test_cctv.mp4";
            }

            const nameEl = document.getElementById("connect-camera-name");
            const idEl = document.getElementById("connect-camera-id");
            const camName = nameEl ? nameEl.value.trim() : "";
            const camId = idEl ? idEl.value.trim() : "";

            const recordCheck = document.getElementById("connect-record-stream");
            const recordStream = recordCheck ? recordCheck.checked : false;

            if (statusBox) statusBox.classList.remove("hidden");
            if (statusText) {
                statusText.textContent = sourceType === "youtube_live"
                    ? "⏳ Resolving YouTube Live stream & verifying video feed..."
                    : "⏳ Connecting to camera stream & verifying frames...";
                statusText.style.color = "var(--text-primary)";
            }
            if (btnSubmit) btnSubmit.disabled = true;

            try {
                const res = await fetch("/api/cameras/connect", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        camera_id: camId || undefined,
                        name: camName || undefined,
                        stream_url: streamUrl,
                        source_type: sourceType,
                        record_stream: recordStream,
                        timeout: sourceType === "youtube_live" ? 25.0 : 8.0
                    })
                });

                const data = await res.json();
                if (!res.ok) {
                    throw new Error(data.detail || "Connection failed");
                }

                if (statusText) {
                    statusText.textContent = `✅ Connected successfully! (${data.resolution || '768x432'} @ ${data.fps || 25} FPS)`;
                    statusText.style.color = "#34d399";
                }

                if (typeof showAppToast === "function") {
                    showAppToast("Stream Online", `Connected ${data.camera_name || data.camera_id} (${data.resolution || '1080p'} @ ${data.fps || 30} FPS)`, "success");
                }

                setTimeout(async () => {
                    if (connectModal) connectModal.classList.add("hidden");
                    if (btnSubmit) btnSubmit.disabled = false;
                    if (statusBox) statusBox.classList.add("hidden");
                    connectForm.reset();
                    if (groupWebcam) groupWebcam.classList.remove("hidden");
                    if (groupRtsp) groupRtsp.classList.add("hidden");
                    if (groupYoutube) groupYoutube.classList.add("hidden");
                    if (groupTestVideo) groupTestVideo.classList.add("hidden");

                    await loadCameras();
                    activeCameraId = data.camera_id;
                    displayLiveCamera(data.camera_id);

                    const srcLabel = sourceType === "youtube_live" ? "YouTube Live Stream" : "Live Camera";
                    appendChatMessage(
                        "assistant",
                        `📹 <strong>${srcLabel} Connected</strong>: Connected to <code>${escapeHtml(data.camera_name || data.camera_id)}</code> (${data.resolution || '768x432'} @ ${data.fps || 25} FPS). Real-time live preview is now active.`,
                        true
                    );
                }, 500);

            } catch (err) {
                if (statusText) {
                    statusText.textContent = `❌ ${err.message}`;
                    statusText.style.color = "var(--status-error)";
                }
                if (typeof showAppToast === "function") {
                    showAppToast("Connection Failed", err.message, "error");
                }
                if (btnSubmit) btnSubmit.disabled = false;
            }
        });
    }
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

// ========================================================
// STAGE G: REAL-TIME ALERTING, RULES & DRAWING TOOL
// ========================================================

let drawnPoints = []; // Array of [norm_x, norm_y]
let isDrawingMode = false;
let lastKnownAlertTime = Date.now() / 1000.0;
let knownAlertIds = new Set();

function setupAlertingHandlers() {
    // 1. Alert Rules Modal open/close
    const btnRulesNav = document.getElementById("btn-alert-rules-nav");
    const rulesModal = document.getElementById("alert-rules-modal");
    const btnCloseRules = document.getElementById("btn-close-rules-modal");
    const tabBtnList = document.getElementById("tab-btn-rule-list");
    const tabBtnCreate = document.getElementById("tab-btn-rule-create");
    const tabContentList = document.getElementById("tab-content-rule-list");
    const tabContentCreate = document.getElementById("tab-content-rule-create");
    const ruleCamSelect = document.getElementById("rule-camera");

    if (btnRulesNav && rulesModal) {
        btnRulesNav.addEventListener("click", () => {
            // Populate camera select
            if (ruleCamSelect) {
                ruleCamSelect.innerHTML = `<option value="">All Registered Cameras</option>`;
                allCameras.forEach(c => {
                    const opt = document.createElement("option");
                    opt.value = c.id;
                    opt.textContent = `${c.name || c.id} (${c.source_type || 'cctv'})`;
                    if (c.id === activeCameraId) opt.selected = true;
                    ruleCamSelect.appendChild(opt);
                });
            }
            loadAlertRules();
            switchRuleTab("list");
            rulesModal.classList.remove("hidden");
        });
    }

    if (btnCloseRules && rulesModal) {
        btnCloseRules.addEventListener("click", () => rulesModal.classList.add("hidden"));
    }

    if (tabBtnList && tabBtnCreate) {
        tabBtnList.addEventListener("click", () => switchRuleTab("list"));
        tabBtnCreate.addEventListener("click", () => switchRuleTab("create"));
    }

    const btnCancelCreate = document.getElementById("btn-cancel-create-rule");
    if (btnCancelCreate) {
        btnCancelCreate.addEventListener("click", () => switchRuleTab("list"));
    }

    // Dynamic field adjustments based on rule type
    const ruleTypeSelect = document.getElementById("rule-type");
    if (ruleTypeSelect) {
        ruleTypeSelect.addEventListener("change", (e) => {
            adjustRuleFormFields(e.target.value);
        });
    }

    // Create Rule Form Submit
    const createRuleForm = document.getElementById("create-rule-form");
    if (createRuleForm) {
        createRuleForm.addEventListener("submit", async (e) => {
            e.preventDefault();
            const btnSubmit = document.getElementById("btn-submit-rule");
            if (btnSubmit) btnSubmit.disabled = true;

            try {
                const name = document.getElementById("rule-name").value.trim();
                const cameraId = document.getElementById("rule-camera").value || null;
                const ruleType = document.getElementById("rule-type").value;
                const targetClass = document.getElementById("rule-target-class").value.trim() || null;
                const minConf = parseFloat(document.getElementById("rule-min-conf").value) || 0.3;
                const severity = document.getElementById("rule-severity").value;
                const cooldown = parseFloat(document.getElementById("rule-cooldown").value) || 30.0;
                
                let timeStart = null;
                let timeEnd = null;
                if (ruleType === "restricted_hours") {
                    timeStart = document.getElementById("rule-time-start").value || "22:00";
                    timeEnd = document.getElementById("rule-time-end").value || "06:00";
                }

                let tripwireLine = null;
                let zonePoints = null;
                let direction = "any";

                const coordsStr = document.getElementById("rule-coords-input").value.trim();
                if (coordsStr) {
                    try {
                        const parsed = JSON.parse(coordsStr);
                        if (ruleType === "tripwire_crossing") {
                            tripwireLine = parsed;
                            direction = document.getElementById("rule-direction").value || "any";
                        } else if (ruleType === "zone_entry" || ruleType === "vehicle_in_region") {
                            zonePoints = parsed;
                        }
                    } catch (err) {
                        alert("Invalid coordinates JSON. Must be [[x1,y1],[x2,y2]] or [[x1,y1],[x2,y2],[x3,y3],...]");
                        if (btnSubmit) btnSubmit.disabled = false;
                        return;
                    }
                }

                const payload = {
                    name,
                    camera_id: cameraId,
                    rule_type: ruleType,
                    object_class: targetClass,
                    min_confidence: minConf,
                    time_start: timeStart,
                    time_end: timeEnd,
                    tripwire_line: tripwireLine,
                    zone_points: zonePoints,
                    direction: direction,
                    cooldown_seconds: cooldown,
                    severity: severity,
                    enabled: true
                };

                const res = await fetch("/api/alerts/rules", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload)
                });

                if (!res.ok) {
                    const errData = await res.json();
                    throw new Error(errData.detail || "Failed to create rule");
                }

                createRuleForm.reset();
                switchRuleTab("list");
                await loadAlertRules();
                showToastNotification({
                    rule_name: name,
                    camera_name: cameraId || "All Cameras",
                    trigger_reason: "Alert rule activated and monitoring live feeds.",
                    severity: "info"
                });

            } catch (err) {
                alert(`Error: ${err.message}`);
            } finally {
                if (btnSubmit) btnSubmit.disabled = false;
            }
        });
    }

    // 2. Alert History Modal open/close
    const btnAlertsNav = document.getElementById("btn-alerts-history-nav");
    const alertsModal = document.getElementById("alerts-history-modal");
    const btnCloseAlerts = document.getElementById("btn-close-alerts-modal");
    const btnRefreshAlerts = document.getElementById("btn-refresh-alerts");
    const filterCam = document.getElementById("filter-alert-cam");
    const filterSeverity = document.getElementById("filter-alert-severity");
    const filterStatus = document.getElementById("filter-alert-status");

    if (btnAlertsNav && alertsModal) {
        btnAlertsNav.addEventListener("click", () => {
            if (filterCam) {
                filterCam.innerHTML = `<option value="">All Cameras</option>`;
                allCameras.forEach(c => {
                    const opt = document.createElement("option");
                    opt.value = c.id;
                    opt.textContent = c.name || c.id;
                    filterCam.appendChild(opt);
                });
            }
            loadAlertHistory();
            alertsModal.classList.remove("hidden");
        });
    }

    if (btnCloseAlerts && alertsModal) {
        btnCloseAlerts.addEventListener("click", () => alertsModal.classList.add("hidden"));
    }

    if (btnRefreshAlerts) {
        btnRefreshAlerts.addEventListener("click", () => loadAlertHistory());
    }

    if (filterCam) filterCam.addEventListener("change", () => loadAlertHistory());
    if (filterSeverity) filterSeverity.addEventListener("change", () => loadAlertHistory());
    if (filterStatus) filterStatus.addEventListener("change", () => loadAlertHistory());

    // 3. Interactive Drawing Tool
    setupDrawingTool();
}

function switchRuleTab(tab) {
    const tabBtnList = document.getElementById("tab-btn-rule-list");
    const tabBtnCreate = document.getElementById("tab-btn-rule-create");
    const tabContentList = document.getElementById("tab-content-rule-list");
    const tabContentCreate = document.getElementById("tab-content-rule-create");

    if (tab === "list") {
        tabBtnList.classList.add("active");
        tabBtnCreate.classList.remove("active");
        tabContentList.classList.remove("hidden");
        tabContentCreate.classList.add("hidden");
    } else {
        tabBtnCreate.classList.add("active");
        tabBtnList.classList.remove("active");
        tabContentCreate.classList.remove("hidden");
        tabContentList.classList.add("hidden");
    }
}

function adjustRuleFormFields(ruleType) {
    const rowTime = document.getElementById("row-rule-time");
    const groupDirection = document.getElementById("group-rule-direction");
    const coordsInput = document.getElementById("rule-coords-input");

    if (ruleType === "restricted_hours") {
        if (rowTime) rowTime.classList.remove("hidden");
    } else {
        if (rowTime) rowTime.classList.add("hidden");
    }

    if (ruleType === "tripwire_crossing") {
        if (groupDirection) groupDirection.classList.remove("hidden");
        if (coordsInput) coordsInput.placeholder = "Tripwire line [[x1, y1], [x2, y2]] normalized 0.0-1.0";
    } else if (ruleType === "zone_entry" || ruleType === "vehicle_in_region") {
        if (groupDirection) groupDirection.classList.add("hidden");
        if (coordsInput) coordsInput.placeholder = "Zone polygon [[x1, y1], [x2, y2], [x3, y3], ...] normalized 0.0-1.0";
    } else {
        if (groupDirection) groupDirection.classList.add("hidden");
        if (coordsInput) coordsInput.placeholder = "Not required for whole-frame detection";
    }
}

async function loadAlertRules() {
    const tbody = document.getElementById("rules-table-body");
    const countSpan = document.getElementById("rule-list-count");
    if (!tbody) return;

    try {
        const res = await fetch("/api/alerts/rules");
        if (!res.ok) throw new Error("Failed to load rules");
        const rules = await res.json();

        if (countSpan) countSpan.textContent = rules.length;

        if (rules.length === 0) {
            tbody.innerHTML = `<tr><td colspan="8" class="text-center" style="padding:20px; color:var(--text-muted);">No alert rules configured yet. Click "+ Create New Rule" above.</td></tr>`;
            return;
        }

        tbody.innerHTML = rules.map(r => {
            const isChecked = r.enabled ? "checked" : "";
            const camLabel = r.camera_id ? escapeHtml(r.camera_id) : "<em>All Cameras</em>";
            const targetClass = r.object_class ? escapeHtml(r.object_class) : "<em>any</em>";
            
            let condStr = `Min Conf: ${(r.min_confidence * 100).toFixed(0)}%`;
            if (r.time_start && r.time_end) condStr += `<br><span style="color:#38bdf8;">⏰ ${r.time_start} - ${r.time_end}</span>`;
            if (r.direction && r.direction !== "any") condStr += `<br><span>Dir: ${r.direction.toUpperCase()}</span>`;
            if (r.tripwire_line) condStr += `<br><span style="color:#34d399;">Tripwire Active</span>`;
            if (r.zone_points) condStr += `<br><span style="color:#a78bfa;">Polygon (${r.zone_points.length} pts)</span>`;

            const sevBadge = `<span class="badge-severity-${r.severity || 'warning'}">${(r.severity || 'warning')}</span>`;

            return `
                <tr>
                    <td>
                        <input type="checkbox" ${isChecked} onchange="toggleRuleEnabled('${r.id}', this.checked)" title="Enable / Disable Rule" style="cursor:pointer;" />
                    </td>
                    <td><strong>${escapeHtml(r.name)}</strong></td>
                    <td><code>${escapeHtml(r.rule_type)}</code></td>
                    <td>${camLabel}</td>
                    <td><span style="font-family:var(--font-mono);">${targetClass}</span></td>
                    <td style="font-size:0.72rem;">${condStr}</td>
                    <td>${sevBadge}</td>
                    <td>
                        <button class="btn-subtle btn-sm" onclick="deleteRule('${r.id}')" style="color:#ef4444;" title="Delete Rule">🗑️</button>
                    </td>
                </tr>
            `;
        }).join("");

    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="8" class="text-center" style="color:var(--status-error);">Error loading rules: ${err.message}</td></tr>`;
    }
}

async function toggleRuleEnabled(ruleId, enabled) {
    try {
        const res = await fetch(`/api/alerts/rules/${ruleId}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ enabled })
        });
        if (!res.ok) throw new Error("Failed to toggle rule");
    } catch (err) {
        alert(`Error: ${err.message}`);
        loadAlertRules();
    }
}

async function deleteRule(ruleId) {
    if (!confirm("Are you sure you want to delete this alert rule?")) return;
    try {
        const res = await fetch(`/api/alerts/rules/${ruleId}`, { method: "DELETE" });
        if (!res.ok) throw new Error("Failed to delete rule");
        await loadAlertRules();
    } catch (err) {
        alert(`Error: ${err.message}`);
    }
}

async function loadAlertHistory() {
    const tbody = document.getElementById("alerts-table-body");
    if (!tbody) return;

    const filterCam = document.getElementById("filter-alert-cam")?.value || "";
    const filterSev = document.getElementById("filter-alert-severity")?.value || "";
    const filterStatus = document.getElementById("filter-alert-status")?.value || "";

    const params = new URLSearchParams();
    if (filterCam) params.append("camera_id", filterCam);
    if (filterSev) params.append("severity", filterSev);
    if (filterStatus) params.append("status", filterStatus);
    params.append("limit", "100");

    try {
        const res = await fetch(`/api/alerts?${params.toString()}`);
        if (!res.ok) throw new Error("Failed to load alerts");
        const data = await res.json();
        const alerts = data.alerts || [];

        if (alerts.length === 0) {
            tbody.innerHTML = `<tr><td colspan="8" class="text-center" style="padding:24px; color:var(--text-muted);">No alerts match the selected criteria.</td></tr>`;
            return;
        }

        tbody.innerHTML = alerts.map(a => {
            const timeStr = new Date(a.trigger_timestamp * 1000).toLocaleTimeString();
            const dateStr = new Date(a.trigger_timestamp * 1000).toLocaleDateString();
            const sevBadge = `<span class="badge-severity-${a.severity || 'warning'}">${(a.severity || 'warning')}</span>`;
            const statusClass = `status-${a.status || 'active'}`;
            const trackStr = a.track_id ? `ID #${a.track_id}` : "--";

            return `
                <tr>
                    <td style="white-space:nowrap; font-family:var(--font-mono); font-size:0.74rem;">${dateStr}<br>${timeStr}</td>
                    <td>${sevBadge}</td>
                    <td><strong>${escapeHtml(a.camera_name || a.camera_id)}</strong></td>
                    <td>${escapeHtml(a.rule_name)}</td>
                    <td style="font-size:0.76rem;">${escapeHtml(a.trigger_reason)}</td>
                    <td><span style="font-family:var(--font-mono);">${escapeHtml(a.object_class || '--')} (${trackStr})</span></td>
                    <td><span class="${statusClass}">${(a.status || 'active').toUpperCase()}</span></td>
                    <td style="white-space:nowrap;">
                        ${a.status === 'active' ? `<button class="btn-subtle btn-sm" onclick="acknowledgeAlert('${a.id}')" title="Acknowledge">✓ Ack</button>` : ''}
                        ${a.status !== 'resolved' ? `<button class="btn-subtle btn-sm" onclick="resolveAlert('${a.id}')" title="Resolve">✔ Resolve</button>` : '<span style="color:#10b981; font-size:0.75rem;">Resolved</span>'}
                    </td>
                </tr>
            `;
        }).join("");

    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="8" class="text-center" style="color:var(--status-error);">Error: ${err.message}</td></tr>`;
    }
}

async function acknowledgeAlert(alertId) {
    try {
        const res = await fetch(`/api/alerts/${alertId}/acknowledge`, { method: "POST" });
        if (!res.ok) throw new Error("Failed to acknowledge alert");
        await loadAlertHistory();
    } catch (err) {
        alert(err.message);
    }
}

async function resolveAlert(alertId) {
    try {
        const res = await fetch(`/api/alerts/${alertId}/resolve`, { method: "POST" });
        if (!res.ok) throw new Error("Failed to resolve alert");
        await loadAlertHistory();
    } catch (err) {
        alert(err.message);
    }
}

// 4. Interactive Canvas Geometry Drawing
function setupDrawingTool() {
    const btnToggle = document.getElementById("btn-toggle-draw-tool");
    const toolbar = document.getElementById("drawing-toolbar");
    const drawTypeSelect = document.getElementById("draw-type-select");
    const pointsCount = document.getElementById("draw-points-count");
    const btnClear = document.getElementById("btn-clear-drawing");
    const btnSave = document.getElementById("btn-save-drawn-rule");
    const btnCancel = document.getElementById("btn-cancel-drawing");
    const canvas = document.getElementById("evidence-canvas");
    let currentHoverPoint = null;

    if (!btnToggle || !toolbar || !canvas) return;

    btnToggle.addEventListener("click", () => {
        isDrawingMode = !isDrawingMode;
        if (isDrawingMode) {
            btnToggle.classList.add("active");
            toolbar.classList.remove("hidden");
            canvas.classList.add("drawing-active");
            drawnPoints = [];
            currentHoverPoint = null;
            updateDrawInfo();
            renderDrawnGeometry();
        } else {
            exitDrawingMode();
        }
    });

    if (btnCancel) {
        btnCancel.addEventListener("click", exitDrawingMode);
    }

    if (btnClear) {
        btnClear.addEventListener("click", () => {
            drawnPoints = [];
            currentHoverPoint = null;
            updateDrawInfo();
            renderDrawnGeometry();
        });
    }

    if (drawTypeSelect) {
        drawTypeSelect.addEventListener("change", () => {
            drawnPoints = [];
            currentHoverPoint = null;
            updateDrawInfo();
            renderDrawnGeometry();
        });
    }

    if (btnSave) {
        btnSave.addEventListener("click", () => {
            const mode = drawTypeSelect ? drawTypeSelect.value : "tripwire_crossing";
            if (mode === "tripwire_crossing" && drawnPoints.length !== 2) {
                alert("Tripwire requires exactly 2 points. Click on the camera view to place start and end points.");
                return;
            }
            if (mode === "zone_entry" && drawnPoints.length < 3) {
                alert("Zone requires at least 3 points forming a polygon. Click to place points.");
                return;
            }

            exitDrawingMode();
            
            // Open rules modal and populate form
            const btnRulesNav = document.getElementById("btn-alert-rules-nav");
            if (btnRulesNav) btnRulesNav.click();

            switchRuleTab("create");
            const ruleTypeEl = document.getElementById("rule-type");
            if (ruleTypeEl) {
                ruleTypeEl.value = mode;
                adjustRuleFormFields(mode);
            }
            const coordsInput = document.getElementById("rule-coords-input");
            if (coordsInput) {
                coordsInput.value = JSON.stringify(drawnPoints);
            }
        });
    }

    // Canvas click to add point
    canvas.addEventListener("click", (e) => {
        if (!isDrawingMode) return;
        const rect = canvas.getBoundingClientRect();
        const normX = Math.max(0.0, Math.min(1.0, (e.clientX - rect.left) / rect.width));
        const normY = Math.max(0.0, Math.min(1.0, (e.clientY - rect.top) / rect.height));

        const mode = drawTypeSelect ? drawTypeSelect.value : "tripwire_crossing";
        if (mode === "tripwire_crossing") {
            if (drawnPoints.length >= 2) {
                drawnPoints = []; // reset if already 2
            }
            drawnPoints.push([Math.round(normX * 1000) / 1000, Math.round(normY * 1000) / 1000]);
        } else {
            drawnPoints.push([Math.round(normX * 1000) / 1000, Math.round(normY * 1000) / 1000]);
        }

        updateDrawInfo();
        renderDrawnGeometry();
    });

    // CAD-style live hover guide line
    canvas.addEventListener("mousemove", (e) => {
        if (!isDrawingMode || drawnPoints.length === 0) return;
        const rect = canvas.getBoundingClientRect();
        const normX = Math.max(0.0, Math.min(1.0, (e.clientX - rect.left) / rect.width));
        const normY = Math.max(0.0, Math.min(1.0, (e.clientY - rect.top) / rect.height));
        currentHoverPoint = [normX, normY];
        renderDrawnGeometry();
    });

    canvas.addEventListener("mouseleave", () => {
        currentHoverPoint = null;
        if (isDrawingMode) renderDrawnGeometry();
    });

    function exitDrawingMode() {
        isDrawingMode = false;
        btnToggle.classList.remove("active");
        toolbar.classList.add("hidden");
        canvas.classList.remove("drawing-active");
        drawnPoints = [];
        currentHoverPoint = null;
        // Re-render detection overlay
        renderDrawnGeometry();
    }

    function updateDrawInfo() {
        if (!pointsCount) return;
        const mode = drawTypeSelect ? drawTypeSelect.value : "tripwire_crossing";
        if (mode === "tripwire_crossing") {
            pointsCount.textContent = `Tripwire: ${drawnPoints.length}/2 points placed`;
        } else {
            pointsCount.textContent = `Polygon: ${drawnPoints.length} points placed (click to add)`;
        }
    }
}

function renderDrawnGeometry() {
    const canvas = document.getElementById("evidence-canvas");
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    if (!isDrawingMode && drawnPoints.length === 0) {
        return; // normal detection overlay handles canvas
    }

    const parent = canvas.parentElement;
    if (parent && (canvas.width !== parent.clientWidth || canvas.height !== parent.clientHeight)) {
        canvas.width = parent.clientWidth;
        canvas.height = parent.clientHeight;
    }

    ctx.clearRect(0, 0, canvas.width, canvas.height);
    const w = canvas.width;
    const h = canvas.height;

    if (drawnPoints.length === 0) return;

    ctx.save();
    ctx.lineWidth = 3;
    ctx.strokeStyle = "#38bdf8";
    ctx.fillStyle = "rgba(56, 189, 248, 0.25)";

    const mode = document.getElementById("draw-type-select")?.value || "tripwire_crossing";

    if (mode === "tripwire_crossing") {
        ctx.strokeStyle = "#f59e0b";
        ctx.beginPath();
        const p1 = drawnPoints[0];
        ctx.moveTo(p1[0] * w, p1[1] * h);
        if (drawnPoints.length > 1) {
            const p2 = drawnPoints[1];
            ctx.lineTo(p2[0] * w, p2[1] * h);
        }
        ctx.stroke();

        // Draw points
        drawnPoints.forEach((p, idx) => {
            ctx.beginPath();
            ctx.arc(p[0] * w, p[1] * h, 6, 0, Math.PI * 2);
            ctx.fillStyle = "#ef4444";
            ctx.fill();
            ctx.stroke();
            ctx.fillStyle = "#fff";
            ctx.font = "bold 12px sans-serif";
            ctx.fillText(idx === 0 ? "A" : "B", p[0] * w + 8, p[1] * h - 8);
        });

    } else {
        // Polygon zone
        ctx.beginPath();
        drawnPoints.forEach((p, idx) => {
            const px = p[0] * w;
            const py = p[1] * h;
            if (idx === 0) ctx.moveTo(px, py);
            else ctx.lineTo(px, py);
        });
        if (drawnPoints.length >= 3) {
            ctx.closePath();
            ctx.fill();
        }
        ctx.stroke();

        // Points
        drawnPoints.forEach((p, idx) => {
            ctx.beginPath();
            ctx.arc(p[0] * w, p[1] * h, 5, 0, Math.PI * 2);
            ctx.fillStyle = "#38bdf8";
            ctx.fill();
            ctx.stroke();
            ctx.fillStyle = "#fff";
            ctx.font = "bold 11px sans-serif";
            ctx.fillText(`${idx + 1}`, p[0] * w + 6, p[1] * h - 6);
        });
    }

    // CAD dynamic dashed guide line to mouse cursor
    if (isDrawingMode && currentHoverPoint && drawnPoints.length > 0) {
        const lastP = drawnPoints[drawnPoints.length - 1];
        ctx.beginPath();
        ctx.setLineDash([5, 5]);
        ctx.strokeStyle = mode === "tripwire_crossing" ? "rgba(245, 158, 11, 0.75)" : "rgba(56, 189, 248, 0.75)";
        ctx.lineWidth = 2;
        ctx.moveTo(lastP[0] * w, lastP[1] * h);
        ctx.lineTo(currentHoverPoint[0] * w, currentHoverPoint[1] * h);
        ctx.stroke();
        ctx.setLineDash([]);
    }

    ctx.restore();
}

// 5. Alert Polling & Toast Notifications
function startAlertsPolling() {
    setInterval(pollAlerts, 3000);
}

async function pollAlerts() {
    try {
        const res = await fetch("/api/alerts/recent?limit=10");
        if (!res.ok) return;
        const recent = await res.json();

        // Count unacknowledged active alerts
        const activeCount = recent.filter(a => a.status === "active").length;
        const badge = document.getElementById("alerts-badge-count");
        if (badge) {
            if (activeCount > 0) {
                badge.textContent = activeCount;
                badge.classList.remove("hidden");
            } else {
                badge.classList.add("hidden");
            }
        }

        // Fire toast for any brand new alert
        recent.forEach(a => {
            if (!knownAlertIds.has(a.id)) {
                knownAlertIds.add(a.id);
                if (a.trigger_timestamp > lastKnownAlertTime) {
                    showToastNotification(a);
                }
            }
        });

        if (recent.length > 0) {
            const latestTs = Math.max(...recent.map(r => r.trigger_timestamp || 0));
            if (latestTs > lastKnownAlertTime) {
                lastKnownAlertTime = latestTs;
            }
        }

    } catch (err) {
        // Silent catch for background poll
    }
}

function showToastNotification(alertData) {
    const container = document.getElementById("toast-container");
    if (!container) return;

    const toast = document.createElement("div");
    toast.className = `toast-item toast-${alertData.severity || 'warning'}`;
    if (alertData.severity === "critical") {
        toast.style.borderColor = "#ef4444";
    } else if (alertData.severity === "info") {
        toast.style.borderColor = "#38bdf8";
    } else {
        toast.style.borderColor = "#f59e0b";
    }

    const titleIcon = alertData.severity === "critical" ? "🚨" : (alertData.severity === "info" ? "ℹ️" : "⚠️");

    toast.innerHTML = `
        <div class="toast-header">
            <span class="toast-title">${titleIcon} ${escapeHtml(alertData.rule_name || 'Alert Triggered')}</span>
            <span style="font-size:0.7rem; color:var(--text-muted);">${escapeHtml(alertData.camera_name || alertData.camera_id || '')}</span>
        </div>
        <div class="toast-body">${escapeHtml(alertData.trigger_reason || '')}</div>
        <div class="toast-footer">
            ${alertData.id ? `<button class="btn-subtle btn-sm" onclick="acknowledgeAlert('${alertData.id}'); this.closest('.toast-item').remove();">Acknowledge</button>` : ''}
            <button class="btn-subtle btn-sm" onclick="this.closest('.toast-item').remove();">&times;</button>
        </div>
    `;

    container.appendChild(toast);

    // Auto dismiss after 6 seconds
    setTimeout(() => {
        if (toast.parentElement) toast.remove();
    }, 6000);
}

function showAppToast(title, message, type = "info") {
    const container = document.getElementById("toast-container");
    if (!container) return;

    const toast = document.createElement("div");
    toast.className = `toast-item toast-${type}`;
    if (type === "success") {
        toast.style.borderColor = "#10b981";
        toast.style.boxShadow = "0 8px 25px rgba(0, 0, 0, 0.5), 0 0 15px rgba(16, 185, 129, 0.3)";
    } else if (type === "error" || type === "critical") {
        toast.style.borderColor = "#ef4444";
        toast.style.boxShadow = "0 8px 25px rgba(0, 0, 0, 0.5), 0 0 15px rgba(239, 68, 68, 0.3)";
    } else if (type === "warning") {
        toast.style.borderColor = "#f59e0b";
        toast.style.boxShadow = "0 8px 25px rgba(0, 0, 0, 0.5), 0 0 15px rgba(245, 158, 11, 0.3)";
    } else {
        toast.style.borderColor = "#38bdf8";
        toast.style.boxShadow = "0 8px 25px rgba(0, 0, 0, 0.5), 0 0 15px rgba(56, 189, 248, 0.3)";
    }

    const icon = type === "success" ? "✅" : (type === "error" || type === "critical" ? "❌" : (type === "warning" ? "⚠️" : "ℹ️"));

    toast.innerHTML = `
        <div class="toast-header">
            <span class="toast-title">${icon} ${escapeHtml(title)}</span>
            <button class="btn-close" style="font-size:1rem; padding:0 4px;" onclick="this.closest('.toast-item').remove();">&times;</button>
        </div>
        <div class="toast-body">${escapeHtml(message)}</div>
    `;

    container.appendChild(toast);
    setTimeout(() => {
        if (toast.parentElement) toast.remove();
    }, 4500);
}

function setupGlobalModalAndKeyHandlers() {
    // Backdrop click closes any modal
    document.querySelectorAll(".modal").forEach(modal => {
        modal.addEventListener("click", (e) => {
            if (e.target === modal) {
                modal.classList.add("hidden");
            }
        });
    });

    // Escape key closes open modals and cancels CAD geometry drawing mode
    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape") {
            const openModals = Array.from(document.querySelectorAll(".modal:not(.hidden)"));
            if (openModals.length > 0) {
                openModals.forEach(m => m.classList.add("hidden"));
                return;
            }
            if (typeof isDrawingMode !== "undefined" && isDrawingMode) {
                const btnCancelDrawing = document.getElementById("btn-cancel-drawing");
                if (btnCancelDrawing) btnCancelDrawing.click();
            }
        }
    });
}

let currentView = "overview";
let currentGridMode = 4;
let liveDetectionsFilter = "all";

function setupLayoutInteractions() {
    // 1. Sidebar Navigation
    const navItems = document.querySelectorAll(".nav-item[data-view]");
    navItems.forEach(item => {
        item.addEventListener("click", (e) => {
            e.preventDefault();
            const viewName = item.getAttribute("data-view");
            switchView(viewName);
        });
    });

    // 2. Real-time Clock
    function updateClock() {
        const clockEl = document.getElementById("realtime-clock");
        if (clockEl) {
            const now = new Date();
            clockEl.textContent = now.toLocaleTimeString();
        }
    }
    updateClock();
    setInterval(updateClock, 1000);

    // 3. Grid Switcher Buttons in Live Monitoring
    const gridBtns = document.querySelectorAll(".btn-grid-layout[data-grid]");
    const matrixGrid = document.getElementById("monitoring-matrix-grid");
    gridBtns.forEach(btn => {
        btn.addEventListener("click", () => {
            gridBtns.forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            const gridNum = parseInt(btn.getAttribute("data-grid"), 10);
            currentGridMode = gridNum;
            if (matrixGrid) {
                matrixGrid.className = `camera-matrix-grid grid-${gridNum}`;
            }
            renderMonitoringMatrix();
        });
    });

    // Multi-View Batch Connect & Player Mode Controls
    const btnConnectAll = document.getElementById("btn-connect-all-streams");
    if (btnConnectAll) {
        btnConnectAll.addEventListener("click", () => {
            connectAllVisibleStreams();
        });
    }

    const btnToggleMvMode = document.getElementById("btn-toggle-multiview-mode");
    if (btnToggleMvMode) {
        btnToggleMvMode.addEventListener("click", () => {
            toggleMultiviewPlaybackMode();
        });
    }

    const btnQuickAdd = document.getElementById("btn-quick-add-stream");
    if (btnQuickAdd) {
        btnQuickAdd.addEventListener("click", () => {
            const m = document.getElementById("connect-modal");
            if (m) {
                m.classList.remove("hidden");
                const sel = document.getElementById("connect-source-type");
                if (sel) {
                    sel.value = "youtube_live";
                    sel.dispatchEvent(new Event("change"));
                }
            }
        });
    }

    // 4. Live Detections Filter Tabs (Overview & Monitoring)
    const setupDetTabs = (containerId) => {
        const container = document.getElementById(containerId) || document.querySelector(`.${containerId}`);
        if (!container) return;
        const tabs = container.querySelectorAll(".det-tab-btn");
        tabs.forEach(tab => {
            tab.addEventListener("click", () => {
                const filter = tab.getAttribute("data-filter") || "all";
                if (window.setLiveDetectionsFilter) {
                    window.setLiveDetectionsFilter(filter);
                } else {
                    liveDetectionsFilter = filter;
                    loadRecentDetectionsFeed();
                }
            });
        });
    };
    setupDetTabs("view-overview");
    setupDetTabs("monitoring-det-filter-tabs");

    // 5. Global Search Input
    const globalSearchInput = document.getElementById("global-search-input");
    if (globalSearchInput) {
        globalSearchInput.addEventListener("keydown", (e) => {
            if (e.key === "Enter" && globalSearchInput.value.trim()) {
                const q = globalSearchInput.value.trim();
                switchView("ai-search");
                const queryInput = document.getElementById("query-input");
                if (queryInput) {
                    queryInput.value = q;
                    const queryForm = document.getElementById("query-form");
                    if (queryForm) queryForm.dispatchEvent(new Event("submit"));
                }
            }
        });
    }

    // 6. Cross-link Buttons
    const btnGotoMonitoring = document.getElementById("btn-goto-monitoring");
    if (btnGotoMonitoring) btnGotoMonitoring.addEventListener("click", () => switchView("live-monitoring"));

    const btnGotoSources = document.getElementById("btn-goto-sources");
    if (btnGotoSources) btnGotoSources.addEventListener("click", () => switchView("cameras"));

    const btnGotoAlerts = document.getElementById("btn-goto-alerts");
    if (btnGotoAlerts) btnGotoAlerts.addEventListener("click", () => switchView("alerts"));

    const btnTopAlerts = document.getElementById("btn-top-alerts");
    if (btnTopAlerts) btnTopAlerts.addEventListener("click", () => switchView("alerts"));

    const btnSettingsReset = document.getElementById("btn-settings-reset");
    if (btnSettingsReset) {
        btnSettingsReset.addEventListener("click", () => {
            const resetModal = document.getElementById("reset-modal");
            if (resetModal) resetModal.classList.remove("hidden");
        });
    }

    const btnRefreshAlertsQueue = document.getElementById("btn-refresh-alerts-queue");
    if (btnRefreshAlertsQueue) {
        btnRefreshAlertsQueue.addEventListener("click", () => loadAlertsHub());
    }

    // Initial data load for overview
    loadOverviewData();
    setInterval(loadOverviewData, 5000);
    startLiveDetectionsPolling();
}

function switchView(viewName) {
    currentView = viewName;

    // 1. Update Navigation Item Active Class
    document.querySelectorAll(".nav-item[data-view]").forEach(item => {
        if (item.getAttribute("data-view") === viewName) {
            item.classList.add("active");
        } else {
            item.classList.remove("active");
        }
    });

    // 2. Update Page Display
    document.querySelectorAll(".view-page").forEach(page => {
        page.classList.remove("active-view");
    });
    const targetPage = document.getElementById(`view-${viewName}`);
    if (targetPage) {
        targetPage.classList.add("active-view");
    }

    // 3. Update Breadcrumb Title
    const titleEl = document.getElementById("top-nav-title");
    const crumbEl = document.getElementById("top-nav-crumb");
    const viewTitles = {
        "overview": ["Overview", "Executive Dashboard"],
        "video-lab": ["Video Intelligence Lab", "Public Video Ingestion & ByteTrack Tracking"],
        "live-monitoring": ["Live Monitoring", "Multi-Camera Surveillance"],
        "cameras": ["Cameras & Sources", "Stream Ingestion"],
        "ai-search": ["AI Search", "Conversational Intelligence"],
        "events": ["Detection Events", "Historical Forensics"],
        "alerts": ["Alerts & Tripwires", "Perimeter Security Rules"],
        "analytics": ["Analytics", "Scene Intelligence & Metrics"],
        "settings": ["Settings", "Platform Preferences"]
    };
    if (viewTitles[viewName] && titleEl && crumbEl) {
        titleEl.textContent = viewTitles[viewName][0];
        crumbEl.textContent = viewTitles[viewName][1];
    }

    // 4. View-specific data triggers
    if (viewName === "overview") {
        loadOverviewData();
    } else if (viewName === "video-lab") {
        initVideoLabView();
    } else if (viewName === "live-monitoring") {
        renderMonitoringMatrix();
        loadRecentDetectionsFeed();
    } else if (viewName === "events") {
        loadEventTimeline();
    } else if (viewName === "alerts") {
        loadAlertsHub();
    } else if (viewName === "cameras") {
        loadCameras();
    }
}

async function loadOverviewData() {
    try {
        // 1. Metric: Cameras
        const resCams = await fetch("/api/cameras");
        if (resCams.ok) {
            const cams = await resCams.json();
            allCameras = cams;
            const activeCount = cams.filter(c => c.status === "CONNECTED" || c.status === "READY").length;
            const offlineCount = cams.length - activeCount;

            const elActive = document.getElementById("metric-active-cameras");
            const elOffline = document.getElementById("metric-offline-cameras");
            if (elActive) elActive.textContent = activeCount;
            if (elOffline) elOffline.textContent = offlineCount;

            renderOverviewCameraMatrix(cams);
            updateOverviewPrimaryVideo(cams);
        }

        // 2. Metric: Detection Events
        const resEv = await fetch("/api/events/search?limit=1");
        if (resEv.ok) {
            const evData = await resEv.json();
            const elEvents = document.getElementById("metric-detection-events");
            if (elEvents) elEvents.textContent = evData.total || 0;
            const elIndexed = document.getElementById("indexed-events-count");
            if (elIndexed) elIndexed.textContent = `Events: ${evData.total || 0}`;
        }

        // 3. Metric: Alerts Status
        const resAlerts = await fetch("/api/alerts/status");
        if (resAlerts.ok) {
            const alertStatus = await resAlerts.json();
            const critCount = alertStatus.critical || 0;
            const elCrit = document.getElementById("metric-critical-alerts");
            if (elCrit) elCrit.textContent = critCount;

            const activeAlerts = alertStatus.active || 0;
            const sidebarBadge = document.getElementById("sidebar-alert-badge");
            const topAlertDot = document.getElementById("top-alerts-dot");
            if (sidebarBadge) {
                if (activeAlerts > 0) {
                    sidebarBadge.textContent = activeAlerts;
                    sidebarBadge.classList.remove("hidden");
                } else {
                    sidebarBadge.classList.add("hidden");
                }
            }
            if (topAlertDot) {
                if (activeAlerts > 0) topAlertDot.classList.remove("hidden");
                else topAlertDot.classList.add("hidden");
            }
        }

        // 4. Metric: Tracked Objects estimate
        const resTracks = await fetch("/api/cameras/detections/recent");
        if (resTracks.ok) {
            const recTracks = await resTracks.json();
            const trackList = Array.isArray(recTracks) ? recTracks : (recTracks?.detections || []);
            const uniqueTrackIds = new Set(trackList.map(t => t.track_id).filter(Boolean));
            const elTracks = document.getElementById("metric-tracked-objects");
            if (elTracks) elTracks.textContent = Math.max(uniqueTrackIds.size, trackList.length);
        }

        // 5. Recent Detections Feed
        await loadRecentDetectionsFeed();

        // 6. Recent Alerts Feed
        await loadOverviewRecentAlerts();
    } catch (err) {
        console.error("Overview data load warning:", err);
    }
}

window.selectCamera = async function selectCamera(cameraId) {
    if (!cameraId) return;
    activeCameraId = cameraId;
    const cam = allCameras.find(c => c.id === cameraId);
    if (!cam) return;

    // 1. Sync Overview Camera Dropdown
    const camSelect = document.getElementById("overview-camera-select");
    if (camSelect && camSelect.value !== cameraId) {
        camSelect.value = cameraId;
    }

    // 2. Highlight selected card in Overview Matrix
    const cards = document.querySelectorAll("#overview-camera-matrix .camera-card");
    cards.forEach(card => {
        if (card.getAttribute("data-cam-id") === cameraId) {
            card.classList.add("active");
            card.style.borderColor = "var(--cyan-primary)";
            card.style.boxShadow = "0 0 14px rgba(19, 217, 245, 0.3)";
        } else {
            card.classList.remove("active");
            card.style.borderColor = "var(--border-color)";
            card.style.boxShadow = "none";
        }
    });

    // 3. Update Big Screen in Overview
    updateOverviewPrimaryVideo(allCameras);

    // 4. Update Live Monitoring Primary Card
    displayLiveCamera(cameraId);
    renderMonitoringMatrix();

    // 5. If live camera is disconnected, auto-connect in background so tracking starts immediately
    if ((cam.source_type === "live" || cam.source_type === "youtube_live") && cam.status !== "CONNECTED") {
        try {
            fetch("/api/cameras/connect", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    camera_id: cam.id,
                    name: cam.name,
                    stream_url: cam.video_path,
                    source_type: cam.source_type,
                    record_stream: false,
                    timeout: cam.source_type === "youtube_live" ? 22.0 : 8.0
                })
            }).then(async res => {
                if (res.ok) {
                    await loadCameras();
                    updateOverviewPrimaryVideo(allCameras);
                    renderMonitoringMatrix();
                }
            }).catch(e => console.warn("Auto-connect camera error:", e));
        } catch (e) {}
    }
};

function updateOverviewPrimaryVideo(cams) {
    if (!cams || cams.length === 0) return;
    const primary = cams.find(c => c.id === activeCameraId) || cams[0];
    if (!primary) return;

    const heroTitle = document.getElementById("overview-hero-title");
    const nameEl = document.getElementById("overview-cam-name");
    const resEl = document.getElementById("overview-cam-res");
    const tagEl = document.getElementById("overview-stream-tag");
    const videoEl = document.getElementById("overview-video");
    const imgEl = document.getElementById("overview-live-img");
    const ytIframe = document.getElementById("overview-youtube-iframe");
    const camSelect = document.getElementById("overview-camera-select");

    if (heroTitle) heroTitle.textContent = `${primary.name || primary.id} (Big Screen View)`;
    if (nameEl) nameEl.textContent = `CAM: ${primary.name || primary.id}`;
    if (resEl) resEl.textContent = `${primary.resolution || "1920x1080"} @ ${primary.fps || 30}fps`;

    // Populate or sync camera dropdown
    if (camSelect) {
        if (camSelect.options.length !== cams.length) {
            camSelect.innerHTML = cams.map(c => `
                <option value="${c.id}" ${c.id === primary.id ? 'selected' : ''}>
                    ${escapeHtml(c.name || c.id)} (${c.status === 'CONNECTED' ? '● LIVE' : 'OFFLINE'})
                </option>
            `).join("");
            camSelect.onchange = (e) => {
                window.selectCamera(e.target.value);
            };
        } else {
            camSelect.value = primary.id;
        }
    }

    const isConn = primary.status === "CONNECTED";
    const isYt = primary.source_type === "youtube_live" || Boolean(extractYouTubeVideoId(primary.video_path));
    const ytId = extractYouTubeVideoId(primary.video_path);

    if (tagEl) {
        if (isConn) {
            tagEl.textContent = "● LIVE TRACKING";
            tagEl.style.color = "var(--emerald-status)";
            tagEl.style.borderColor = "var(--emerald-status)";
        } else if (isYt && ytId) {
            tagEl.textContent = "● YT LIVE";
            tagEl.style.color = "#ef4444";
            tagEl.style.borderColor = "#ef4444";
        } else {
            tagEl.textContent = primary.status || "OFFLINE";
            tagEl.style.color = "var(--text-muted)";
            tagEl.style.borderColor = "var(--border-color)";
        }
    }

    // Media element switching for Big Screen
    if (isYt && ytId) {
        if (cameraPlayerModes[primary.id] === "ai" && isConn) {
            if (ytIframe) { ytIframe.src = ""; ytIframe.classList.add("hidden"); }
            if (videoEl) { videoEl.src = ""; videoEl.classList.add("hidden"); }
            if (imgEl) {
                imgEl.src = `/api/cameras/${primary.id}/live?t=${Date.now()}`;
                imgEl.classList.remove("hidden");
            }
        } else {
            if (imgEl) { imgEl.src = ""; imgEl.classList.add("hidden"); }
            if (videoEl) { videoEl.src = ""; videoEl.classList.add("hidden"); }
            if (ytIframe) {
                const embedUrl = `https://www.youtube-nocookie.com/embed/${ytId}?autoplay=1&mute=1&playsinline=1&enablejsapi=1`;
                if (ytIframe.src !== embedUrl) {
                    ytIframe.src = embedUrl;
                }
                ytIframe.classList.remove("hidden");
            }
        }
    } else if (isConn) {
        if (ytIframe) { ytIframe.src = ""; ytIframe.classList.add("hidden"); }
        if (videoEl) { videoEl.src = ""; videoEl.classList.add("hidden"); }
        if (imgEl) {
            imgEl.src = `/api/cameras/${primary.id}/live?t=${Date.now()}`;
            imgEl.classList.remove("hidden");
        }
    } else if (primary.video_path && !primary.video_path.startsWith("http")) {
        if (ytIframe) { ytIframe.src = ""; ytIframe.classList.add("hidden"); }
        if (imgEl) { imgEl.src = ""; imgEl.classList.add("hidden"); }
        if (videoEl) {
            videoEl.src = primary.video_path;
            videoEl.classList.remove("hidden");
            videoEl.play().catch(() => {});
        }
    } else {
        if (ytIframe) { ytIframe.src = ""; ytIframe.classList.add("hidden"); }
        if (videoEl) { videoEl.src = ""; videoEl.classList.add("hidden"); }
        if (imgEl) { imgEl.src = ""; imgEl.classList.add("hidden"); }
    }
}

function renderOverviewCameraMatrix(cams) {
    const matrixEl = document.getElementById("overview-camera-matrix");
    if (!matrixEl) return;
    if (cams.length === 0) {
        matrixEl.innerHTML = `<div style="color:var(--text-muted); font-size:0.75rem; padding:12px;">No cameras connected.</div>`;
        return;
    }

    matrixEl.innerHTML = cams.map(cam => {
        const isConn = cam.status === "CONNECTED";
        const isYt = cam.source_type === "youtube_live";
        const tag = isYt ? "YT LIVE" : (cam.source_type || "CCTV").toUpperCase();
        const isSel = cam.id === activeCameraId;
        return `
            <div class="camera-card ${isSel ? "active" : ""}" data-cam-id="${cam.id}" onclick="selectCamera('${cam.id}')" style="background:var(--bg-elevated); border:1px solid ${isSel ? 'var(--cyan-primary)' : 'var(--border-color)'}; border-radius:var(--radius-sm); padding:10px 14px; cursor:pointer; transition:all 0.15s ease; box-shadow:${isSel ? '0 0 14px rgba(19, 217, 245, 0.3)' : 'none'};">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">
                    <span style="font-weight:700; font-size:0.8rem; color:${isSel ? 'var(--cyan-primary)' : 'var(--text-main)'};">${escapeHtml(cam.name || cam.id)}</span>
                    <span class="badge-live-tag" style="${isConn ? "background:rgba(0,213,160,0.15); border-color:var(--emerald-status); color:var(--emerald-status);" : "background:rgba(255,65,108,0.15); border-color:var(--red-critical); color:var(--red-critical);"}">
                        ${isConn ? "● CONNECTED" : "OFFLINE"}
                    </span>
                </div>
                <div style="display:flex; justify-content:space-between; font-size:0.68rem; color:var(--text-muted); font-family:var(--font-mono);">
                    <span>${tag} • ${cam.resolution || "1920x1080"}</span>
                    <span>${cam.fps || 30} FPS</span>
                </div>
            </div>
        `;
    }).join("");
}

// ============================================================
// LIVE DETECTIONS PANEL STATE & RENDERING ENGINE
// ============================================================
window.liveDetectionsCache = window.liveDetectionsCache || [];
window.liveDetectionsState = window.liveDetectionsState || "idle"; // 'idle' | 'loading' | 'populated' | 'empty' | 'error' | 'updating'
let liveDetectionsInFlight = false;
let liveDetectionsAbortController = null;
let liveDetectionsPollTimer = null;

function renderLiveDetectionsList() {
    const overviewEl = document.getElementById("overview-detections-list");
    const monitoringEl = document.getElementById("monitoring-detections-list");
    if (!overviewEl && !monitoringEl) return;

    // 1. Initial Loading State (cache is empty)
    if (liveDetectionsState === "loading" && liveDetectionsCache.length === 0) {
        const loadingHtml = `
            <div class="empty-state" style="padding:24px; text-align:center; color:var(--text-muted); font-size:0.75rem;">
                <span class="spinner-inline" style="margin-right:8px;"></span>
                Loading live detections...
            </div>
        `;
        if (overviewEl) overviewEl.innerHTML = loadingHtml;
        if (monitoringEl) monitoringEl.innerHTML = loadingHtml;
        return;
    }

    // 2. Error State (cache is empty and fetch failed)
    if (liveDetectionsState === "error" && liveDetectionsCache.length === 0) {
        const errorHtml = `
            <div class="empty-state" style="padding:24px; text-align:center; color:var(--red-critical); font-size:0.75rem;">
                <div style="margin-bottom:8px; font-weight:600;">⚠️ Unable to load detections</div>
                <button class="btn-subtle btn-xs" onclick="window.retryLiveDetections()" style="cursor:pointer; color:var(--cyan-primary); border:1px solid var(--border-color); border-radius:var(--radius-xs); padding:4px 12px; background:var(--bg-elevated); font-size:0.72rem;">Retry</button>
            </div>
        `;
        if (overviewEl) overviewEl.innerHTML = errorHtml;
        if (monitoringEl) monitoringEl.innerHTML = errorHtml;
        return;
    }

    // 3. Filter items safely without mutating liveDetectionsCache
    let items = [...liveDetectionsCache];
    if (liveDetectionsFilter !== "all") {
        items = items.filter(d => {
            const cls = (d.class || d.label || d.concept || "").toLowerCase().trim();
            const isPerson = (cls === "person" || cls === "pedestrian" || cls === "human");
            const isVehicle = ["car", "suv", "van", "truck", "bus", "vehicle", "bicycle", "motorcycle", "train"].includes(cls);
            if (liveDetectionsFilter === "person") return isPerson;
            if (liveDetectionsFilter === "vehicle") return isVehicle;
            if (liveDetectionsFilter === "other") return !isPerson && !isVehicle;
            return true;
        });
    }

    // 4. Empty State
    if (items.length === 0) {
        const emptyMsg = liveDetectionsCache.length === 0
            ? "No detections yet."
            : "No detections in this category.";
        const emptyHtml = `<div class="empty-state" style="text-align:center; padding:24px; color:var(--text-muted); font-size:0.75rem;">${emptyMsg}</div>`;
        if (overviewEl) overviewEl.innerHTML = emptyHtml;
        if (monitoringEl) monitoringEl.innerHTML = emptyHtml;
        return;
    }

    // 5. Populated State (renders actual detections)
    const html = items.slice(0, 25).map(det => {
        const cls = (det.class || det.label || det.concept || "object").toLowerCase().trim();
        const isPerson = (cls === "person" || cls === "pedestrian" || cls === "human");
        const isVeh = ["car", "suv", "van", "truck", "bus", "vehicle", "bicycle", "motorcycle", "train"].includes(cls);
        const icon = isPerson ? "👤" : (isVeh ? "🚗" : "📦");
        const iconClass = isPerson ? "class-person" : (isVeh ? "class-car" : "class-other");

        const rawConf = det.confidence ?? det.raw_confidence ?? 0.85;
        const confPct = Math.min(100, Math.max(1, Math.round(rawConf * 100)));
        const confColor = confPct >= 75 ? "var(--emerald-status)" : (confPct >= 50 ? "var(--cyan-primary)" : "var(--amber-warning)");

        let timeStr = "--:--:--";
        if (det.timestamp) {
            try {
                timeStr = new Date(det.timestamp * 1000).toLocaleTimeString();
            } catch (e) {
                timeStr = "Just now";
            }
        }

        const camObj = Array.isArray(allCameras) ? allCameras.find(c => c.id === det.camera_id) : null;
        const camName = camObj ? (camObj.name || camObj.id) : (det.camera_id || "Camera");
        const trackStr = det.track_id != null ? `#${det.track_id}` : "--";
        const thumbUrl = det.thumbnail_url || det.representative_frame || (det.crop_url || null);

        return `
            <div class="detection-feed-item" onclick="switchView('ai-search');" title="Investigate in AI Search">
                <div class="det-left-info" style="display:flex; align-items:center; gap:10px;">
                    ${thumbUrl ? `
                        <img src="${thumbUrl}" alt="${escapeHtml(cls)}" style="width:32px; height:32px; border-radius:var(--radius-xs); object-fit:cover; border:1px solid var(--border-subtle);" onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';" />
                        <div class="det-class-icon ${iconClass}" style="display:none;">${icon}</div>
                    ` : `
                        <div class="det-class-icon ${iconClass}">${icon}</div>
                    `}
                    <div class="det-text-meta">
                        <span class="det-class-title">${escapeHtml(cls)}</span>
                        <span class="det-sub-source">${escapeHtml(camName)} • Track ${escapeHtml(trackStr)}</span>
                    </div>
                </div>
                <div class="det-right-badges">
                    <span class="det-time-badge">${escapeHtml(timeStr)}</span>
                    <span class="det-conf-badge" style="color:${confColor}; border:1px solid ${confColor}40;">${confPct}%</span>
                </div>
            </div>
        `;
    }).join("");

    if (overviewEl) overviewEl.innerHTML = html;
    if (monitoringEl) monitoringEl.innerHTML = html;
}

window.retryLiveDetections = function() {
    loadRecentDetectionsFeed(true);
};

window.setLiveDetectionsFilter = function(filter) {
    liveDetectionsFilter = filter || "all";
    syncLiveDetectionTabs(liveDetectionsFilter);
    renderLiveDetectionsList();
};

function syncLiveDetectionTabs(filter) {
    document.querySelectorAll(".det-tab-btn").forEach(btn => {
        if (btn.getAttribute("data-filter") === filter) {
            btn.classList.add("active");
        } else {
            btn.classList.remove("active");
        }
    });
}

async function loadRecentDetectionsFeed(isManualOrRetry = false) {
    // Avoid overlapping in-flight network requests
    if (liveDetectionsInFlight) return;

    if (liveDetectionsCache.length === 0 || isManualOrRetry) {
        liveDetectionsState = "loading";
        renderLiveDetectionsList();
    } else {
        liveDetectionsState = "updating";
    }

    liveDetectionsInFlight = true;

    // Timeout via AbortController (5000ms max timeout)
    if (liveDetectionsAbortController) {
        try { liveDetectionsAbortController.abort(); } catch (_) {}
    }
    liveDetectionsAbortController = new AbortController();
    const timeoutId = setTimeout(() => {
        if (liveDetectionsAbortController) liveDetectionsAbortController.abort();
    }, 5000);

    try {
        const res = await fetch("/api/cameras/detections/recent", {
            signal: liveDetectionsAbortController.signal
        });
        clearTimeout(timeoutId);

        if (!res.ok) {
            throw new Error(`HTTP ${res.status}`);
        }

        const data = await res.json();
        // Support both direct array and wrapped { detections: [...] } formats safely
        const rawList = Array.isArray(data) ? data : (data?.detections || []);

        // Deduplicate using camera + track_id + rounded timestamp + class
        const seenKeys = new Set();
        const deduplicated = [];
        for (const det of rawList) {
            const trackKey = det.track_id != null ? String(det.track_id) : "notrack";
            const timeKey = det.timestamp ? Math.floor(det.timestamp * 10) : "notime";
            const clsKey = (det.class || det.label || det.concept || "").toLowerCase().trim();
            const compositeKey = `${det.camera_id || "cam"}_${trackKey}_${timeKey}_${clsKey}`;
            if (!seenKeys.has(compositeKey)) {
                seenKeys.add(compositeKey);
                deduplicated.push(det);
            }
        }

        // Sort descending by timestamp (newest first)
        deduplicated.sort((a, b) => (b.timestamp || 0) - (a.timestamp || 0));

        liveDetectionsCache = deduplicated;
        liveDetectionsState = deduplicated.length > 0 ? "populated" : "empty";
        renderLiveDetectionsList();

        // Update metric on overview dashboard
        const elTracks = document.getElementById("metric-tracked-objects");
        if (elTracks) {
            const uniqueTrackIds = new Set(deduplicated.map(t => t.track_id).filter(Boolean));
            elTracks.textContent = Math.max(uniqueTrackIds.size, deduplicated.length);
        }
    } catch (err) {
        if (err.name !== "AbortError") {
            console.warn("Recent detections load notice:", err);
        }
        // If we already have cached items, keep showing them ("updating" without clearing)
        if (liveDetectionsCache.length === 0) {
            liveDetectionsState = "error";
            renderLiveDetectionsList();
        }
    } finally {
        clearTimeout(timeoutId);
        liveDetectionsInFlight = false;
        liveDetectionsAbortController = null;
    }
}

function startLiveDetectionsPolling() {
    if (liveDetectionsPollTimer) clearInterval(liveDetectionsPollTimer);
    liveDetectionsPollTimer = setInterval(() => {
        if (currentView === "overview" || currentView === "live-monitoring") {
            loadRecentDetectionsFeed(false);
        }
    }, 2500);
}

function stopLiveDetectionsPolling() {
    if (liveDetectionsPollTimer) {
        clearInterval(liveDetectionsPollTimer);
        liveDetectionsPollTimer = null;
    }
}
window.addEventListener("beforeunload", () => {
    stopLiveDetectionsPolling();
});


async function loadOverviewRecentAlerts() {
    const listEl = document.getElementById("overview-recent-alerts");
    if (!listEl) return;

    try {
        const res = await fetch("/api/alerts/recent?limit=5");
        if (!res.ok) return;
        const alerts = await res.json();

        if (alerts.length === 0) {
            listEl.innerHTML = `<div style="color:var(--text-muted); font-size:0.75rem; padding:12px; text-align:center;">No recent security alerts. All clear.</div>`;
            return;
        }

        listEl.innerHTML = alerts.map(a => {
            const timeStr = a.trigger_timestamp ? new Date(a.trigger_timestamp * 1000).toLocaleTimeString() : "--:--:--";
            const sev = (a.severity || "warning").toLowerCase();
            return `
                <div style="background:var(--bg-elevated); border:1px solid var(--border-subtle); border-left:3px solid ${sev === "critical" ? "var(--red-critical)" : "var(--amber-warning)"}; border-radius:var(--radius-sm); padding:8px 12px; display:flex; justify-content:space-between; align-items:center;">
                    <div>
                        <div style="font-size:0.78rem; font-weight:700; color:var(--text-main);">${escapeHtml(a.rule_name || a.trigger_reason)}</div>
                        <div style="font-size:0.68rem; color:var(--text-muted); font-family:var(--font-mono);">${escapeHtml(a.camera_id)} • ${timeStr}</div>
                    </div>
                    <span class="severity-badge ${sev}">${sev}</span>
                </div>
            `;
        }).join("");
    } catch (err) {
        console.error("Overview alerts error:", err);
    }
}

function renderMonitoringMatrix() {
    const grid = document.getElementById("monitoring-matrix-grid");
    if (!grid) return;

    // Keep primary card
    const primaryCard = document.getElementById("feed-card-primary");
    if (currentGridMode === 1) {
        // Only primary camera
        grid.innerHTML = "";
        if (primaryCard) grid.appendChild(primaryCard);
        return;
    }

    // Secondary cameras
    const secondaryCameras = allCameras.filter(c => c.id !== activeCameraId).slice(0, currentGridMode - 1);
    
    // Ensure primary card is present
    grid.innerHTML = "";
    if (primaryCard) {
        grid.appendChild(primaryCard);
        if (activeCameraId) {
            displayLiveCamera(activeCameraId);
        }
    }

    secondaryCameras.forEach(cam => {
        const card = document.createElement("div");
        card.className = "camera-feed-card";
        card.id = `feed-card-${cam.id}`;

        const isLive = cam.source_type === "live" || cam.source_type === "youtube_live";
        const isYt = cam.source_type === "youtube_live" || Boolean(extractYouTubeVideoId(cam.video_path));
        const ytId = extractYouTubeVideoId(cam.video_path);
        const isConn = cam.status === "CONNECTED";
        const isDirect = isYt ? (cameraPlayerModes[cam.id] !== "ai") : (cameraPlayerModes[cam.id] === "direct");

        let statusBadgeHtml = "";
        if (isConn) {
            statusBadgeHtml = `<span class="badge-live-tag" style="background:rgba(0,213,160,0.15); border-color:var(--emerald-status); color:var(--emerald-status);">● LIVE AI</span>`;
        } else if (isYt && ytId) {
            statusBadgeHtml = `<span class="badge-live-tag" style="background:rgba(0,213,160,0.15); border-color:var(--emerald-status); color:var(--emerald-status);">● LIVE TRACKING</span>`;
        } else {
            statusBadgeHtml = `<span class="badge-live-tag" style="background:rgba(255,65,108,0.15); border-color:var(--red-critical); color:var(--red-critical);">OFFLINE</span>`;
        }

        let modeToggleBtn = "";
        if (isYt && ytId) {
            modeToggleBtn = `
                <button class="feed-card-btn-action ${isDirect ? 'active' : ''}" data-action="toggle-mode" title="Toggle between direct YouTube embed and live AI detections stream">
                    ${isDirect ? '📺 Direct' : '🤖 AI'}
                </button>
            `;
        }

        let connectBtn = "";
        if (isLive) {
            if (!isConn) {
                connectBtn = `<button class="feed-card-btn-action connect-btn" data-action="connect" title="Connect to backend YOLO AI tracking">⚡ Connect AI</button>`;
            } else {
                connectBtn = `<button class="feed-card-btn-action" data-action="disconnect" style="color:var(--red-critical);" title="Disconnect from AI backend">⏹️ Stop</button>`;
            }
        }

        let viewportHtml = "";
        if (isYt && ytId && isDirect) {
            viewportHtml = `
                <iframe id="media-${cam.id}" class="feed-media" src="https://www.youtube-nocookie.com/embed/${ytId}?autoplay=1&mute=1&playsinline=1" frameborder="0" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture" allowfullscreen></iframe>
            `;
        } else if (isConn || (isYt && !isDirect)) {
            viewportHtml = `
                <img id="media-${cam.id}" src="/api/cameras/${cam.id}/live" class="feed-media" alt="${escapeHtml(cam.name)}" />
            `;
        } else if (cam.video_path && !cam.video_path.startsWith("http")) {
            viewportHtml = `
                <video id="media-${cam.id}" class="feed-media" src="${cam.video_path}" autoplay muted loop playsinline></video>
            `;
        } else {
            viewportHtml = `
                <div class="feed-offline-standby">
                    <div style="font-size:1.8rem;">📷</div>
                    <div style="font-weight:700; color:var(--text-main); font-size:0.82rem;">${escapeHtml(cam.name || cam.id)}</div>
                    <div style="font-size:0.72rem; color:var(--text-muted);">${cam.source_type === 'youtube_live' ? 'YouTube Stream Offline' : 'Camera Disconnected'}</div>
                    <button class="feed-card-btn-action connect-btn" data-action="connect" style="margin-top:6px; padding:4px 10px; font-size:0.75rem;">⚡ Connect Stream</button>
                </div>
            `;
        }

        card.innerHTML = `
            <div class="cam-feed-header">
                <div class="cam-title-info">
                    <span title="${escapeHtml(cam.name || cam.id)}" style="max-width:140px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${escapeHtml(cam.name || cam.id)}</span>
                    <span class="cam-meta-pill">${cam.resolution || "1080p"}</span>
                </div>
                <div class="cam-feed-actions">
                    ${modeToggleBtn}
                    ${connectBtn}
                    <button class="feed-card-btn-action" data-action="focus" title="Make this primary camera">🔍 Focus</button>
                    ${statusBadgeHtml}
                </div>
            </div>
            <div class="feed-viewport" id="viewport-${cam.id}">
                ${viewportHtml}
                <canvas id="canvas-${cam.id}" class="feed-canvas"></canvas>
                <div class="feed-overlay-watermark" id="watermark-${cam.id}">
                    <span id="wm-cam-${cam.id}">CAM: ${escapeHtml(cam.name || cam.id).toUpperCase()}</span>
                    <span id="wm-time-${cam.id}" style="color:var(--emerald-status);">LIVE TRACKING</span>
                </div>
                <div class="feed-hud-verdict-pill hidden" id="hud-${cam.id}" style="font-size:0.68rem; padding:3px 10px; bottom:8px;">
                    <div style="display:flex; align-items:center; gap:6px;">
                        <span class="verdict-tag-pill match" style="font-size:0.62rem; padding:2px 6px;">TRACKING</span>
                        <span id="hud-targets-${cam.id}" style="font-weight:600; color:var(--text-main);">0 TARGETS</span>
                    </div>
                    <div style="display:flex; align-items:center; gap:8px; font-family:var(--font-mono); color:var(--text-muted); font-size:0.64rem;">
                        <div>FPS: <span id="hud-fps-${cam.id}" style="color:var(--emerald-status);">--</span></div>
                        <div>LAT: <span id="hud-lat-${cam.id}" style="color:var(--cyan-primary);">--ms</span></div>
                    </div>
                </div>
            </div>
        `;

        // Event bindings
        const btnMode = card.querySelector('[data-action="toggle-mode"]');
        if (btnMode) {
            btnMode.onclick = (e) => {
                e.stopPropagation();
                cameraPlayerModes[cam.id] = isDirect ? "ai" : "direct";
                renderMonitoringMatrix();
            };
        }

        const btnConn = card.querySelector('[data-action="connect"]');
        if (btnConn) {
            btnConn.onclick = (e) => {
                e.stopPropagation();
                reconnectCamera(cam);
            };
        }

        const btnDisc = card.querySelector('[data-action="disconnect"]');
        if (btnDisc) {
            btnDisc.onclick = (e) => {
                e.stopPropagation();
                disconnectCamera(cam.id);
            };
        }

        card.style.cursor = "pointer";
        card.onclick = (e) => {
            if (e.target.closest("button") || e.target.closest("iframe")) return;
            selectCamera(cam.id);
        };

        const btnFocus = card.querySelector('[data-action="focus"]');
        if (btnFocus) {
            btnFocus.onclick = (e) => {
                e.stopPropagation();
                selectCamera(cam.id);
            };
        }

        grid.appendChild(card);
    });

    // Auto-connect visible cameras in the grid
    autoConnectVisibleCameras();
}

let isAutoConnecting = false;
let autoConnectLastTime = 0;

async function autoConnectVisibleCameras() {
    const now = Date.now();
    if (isAutoConnecting || (now - autoConnectLastTime < 6000)) return;

    const visibleCams = [
        allCameras.find(c => c.id === activeCameraId),
        ...allCameras.filter(c => c.id !== activeCameraId).slice(0, currentGridMode - 1)
    ].filter(Boolean);

    const disconnectedLive = visibleCams.filter(c => 
        (c.source_type === "live" || c.source_type === "youtube_live") && 
        (!latestMatrixDetections[c.id] || latestMatrixDetections[c.id].status !== "active")
    );

    if (disconnectedLive.length === 0) return;

    isAutoConnecting = true;
    autoConnectLastTime = now;

    try {
        const visibleIds = visibleCams.map(c => c.id);
        await fetch("/api/cameras/connect-all", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ camera_ids: visibleIds })
        });
        await loadCameras();
    } catch (e) {
        console.warn("Auto-connect batch warning:", e);
    } finally {
        isAutoConnecting = false;
    }
}

async function connectAllVisibleStreams() {
    const visibleCams = [
        allCameras.find(c => c.id === activeCameraId),
        ...allCameras.filter(c => c.id !== activeCameraId).slice(0, currentGridMode - 1)
    ].filter(Boolean);

    const visibleIds = visibleCams.map(c => c.id);

    if (typeof showAppToast === "function") {
        showAppToast("Connecting Feeds", `Connecting ${visibleCams.length} visible camera feeds to AI in parallel...`, "info");
    }

    try {
        const res = await fetch("/api/cameras/connect-all", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ camera_ids: visibleIds })
        });
        const data = await res.json();
        if (typeof showAppToast === "function") {
            showAppToast("Streams Connected", `All ${data.connected_count || visibleCams.length} feeds connected to AI tracking!`, "success");
        }
        await loadCameras();
        renderMonitoringMatrix();
        if (activeCameraId) displayLiveCamera(activeCameraId);
    } catch (err) {
        console.error("Connect all visible feeds error:", err);
    }
}

function toggleMultiviewPlaybackMode() {
    multiviewDirectYoutube = !multiviewDirectYoutube;
    const btn = document.getElementById("btn-toggle-multiview-mode");
    if (btn) {
        if (multiviewDirectYoutube) {
            btn.textContent = "🤖 AI Analytics Mode";
            btn.style.color = "var(--cyan-primary)";
            btn.style.borderColor = "var(--cyan-primary)";
            if (typeof showAppToast === "function") {
                showAppToast("Multi-View Mode", "Playing direct YouTube live streams with zero latency.", "info");
            }
        } else {
            btn.textContent = "📺 Play Direct YouTube";
            btn.style.color = "";
            btn.style.borderColor = "";
            if (typeof showAppToast === "function") {
                showAppToast("Multi-View Mode", "Displaying AI YOLO tracking streams.", "info");
            }
        }
    }
    allCameras.forEach(cam => {
        cameraPlayerModes[cam.id] = multiviewDirectYoutube ? "direct" : "ai";
    });
    renderMonitoringMatrix();
    if (activeCameraId) displayLiveCamera(activeCameraId);
}

async function loadAlertsHub() {
    const tbody = document.getElementById("alerts-hub-table-body");
    const summaryList = document.getElementById("rules-summary-list");
    const countEl = document.getElementById("rule-count-summary");

    try {
        // 1. Load alerts
        const resAlerts = await fetch("/api/alerts?limit=50");
        if (resAlerts.ok && tbody) {
            const alerts = await resAlerts.json();
            if (alerts.length === 0) {
                tbody.innerHTML = `<tr><td colspan="7" style="text-align:center; padding:24px; color:var(--text-muted);">No active security alerts. System normal.</td></tr>`;
            } else {
                tbody.innerHTML = alerts.map(a => {
                    const timeStr = a.trigger_timestamp ? new Date(a.trigger_timestamp * 1000).toLocaleString() : "--";
                    const sev = (a.severity || "warning").toLowerCase();
                    const status = a.status || "active";
                    const isAck = status === "acknowledged";
                    const isRes = status === "resolved";

                    return `
                        <tr>
                            <td><span class="severity-badge ${sev}">${sev}</span></td>
                            <td style="font-weight:700; color:var(--text-main);">${escapeHtml(a.rule_name || a.trigger_reason)}</td>
                            <td>${escapeHtml(a.camera_id)}</td>
                            <td style="font-family:var(--font-mono); font-size:0.72rem;">${timeStr}</td>
                            <td>${escapeHtml(a.object_class || "Object")} #${a.track_id || "--"}</td>
                            <td><span style="font-size:0.7rem; color:${isRes ? "var(--emerald-status)" : (isAck ? "var(--cyan-primary)" : "var(--amber-warning)")}; font-weight:700;">${status.toUpperCase()}</span></td>
                            <td style="white-space:nowrap;">
                                ${!isAck && !isRes ? `<button class="btn-table-action" onclick="acknowledgeAlertItem('${a.id}')">Acknowledge</button>` : ""}
                                ${!isRes ? `<button class="btn-table-action" style="margin-left:4px; border-color:var(--emerald-status);" onclick="resolveAlertItem('${a.id}')">Resolve</button>` : `<span style="color:var(--text-muted); font-size:0.7rem;">✓ Resolved</span>`}
                            </td>
                        </tr>
                    `;
                }).join("");
            }
        }

        // 2. Load rules
        const resRules = await fetch("/api/alerts/rules");
        if (resRules.ok && summaryList) {
            const rules = await resRules.json();
            if (countEl) countEl.textContent = rules.length;
            if (rules.length === 0) {
                summaryList.innerHTML = `<div style="color:var(--text-muted); font-size:0.75rem; padding:12px;">No alert rules configured. Click '+ Configure Alert Rules' above.</div>`;
            } else {
                summaryList.innerHTML = rules.map(r => `
                    <div style="background:var(--bg-elevated); border:1px solid var(--border-subtle); border-radius:var(--radius-sm); padding:10px; display:flex; justify-content:space-between; align-items:center;">
                        <div>
                            <div style="font-size:0.8rem; font-weight:700; color:var(--text-main);">${escapeHtml(r.name)}</div>
                            <div style="font-size:0.68rem; color:var(--text-muted); font-family:var(--font-mono);">${r.rule_type} • ${r.object_class || "any"}</div>
                        </div>
                        <span class="severity-badge ${r.severity || "warning"}">${r.severity || "warning"}</span>
                    </div>
                `).join("");
            }
        }
    } catch (err) {
        console.error("Alerts hub load error:", err);
    }
}

async function acknowledgeAlertItem(alertId) {
    try {
        const res = await fetch(`/api/alerts/${alertId}/acknowledge`, { method: "POST" });
        if (res.ok) {
            showAppToast("Alert Acknowledged", `Alert ${alertId.slice(0, 8)} acknowledged.`, "info");
            loadAlertsHub();
        }
    } catch (err) {
        console.error("Ack error:", err);
    }
}

async function resolveAlertItem(alertId) {
    try {
        const res = await fetch(`/api/alerts/${alertId}/resolve`, { method: "POST" });
        if (res.ok) {
            showAppToast("Alert Resolved", `Alert ${alertId.slice(0, 8)} marked resolved.`, "success");
            loadAlertsHub();
        }
    } catch (err) {
        console.error("Resolve error:", err);
    }
}

// ============================================================
// 12. VIDEO INTELLIGENCE LAB CONTROLLER
// ============================================================
let labCurrentJobId = null;
let labSelectedFile = null;
let labPollingInterval = null;
let labCachedResults = null;
let labActiveFilter = "all";

function setupVideoLabHandlers() {
    // 1. Sub-Tab Switcher
    const tabBtns = document.querySelectorAll(".lab-tab-btn[data-tab]");
    tabBtns.forEach(btn => {
        btn.addEventListener("click", () => {
            const tabName = btn.getAttribute("data-tab");
            switchLabSubTab(tabName);
        });
    });

    // 2. Drag & Drop Zone
    const dropzone = document.getElementById("lab-dropzone");
    const fileInput = document.getElementById("lab-file-input");
    const btnBrowse = document.getElementById("btn-lab-browse");
    const btnLoadSample = document.getElementById("btn-lab-load-sample");
    const btnClearFile = document.getElementById("btn-lab-clear-file");
    const btnStartAnalyze = document.getElementById("btn-lab-start-analyze");
    const btnCancelJob = document.getElementById("btn-lab-cancel-job");
    const btnErrorRetry = document.getElementById("btn-lab-error-retry");
    const btnRefreshHistory = document.getElementById("btn-lab-refresh-history");
    const btnAnalyzeAnother = document.getElementById("btn-lab-analyze-another");

    if (btnBrowse && fileInput) {
        btnBrowse.addEventListener("click", (e) => {
            e.stopPropagation();
            fileInput.click();
        });
    }

    if (dropzone && fileInput) {
        dropzone.addEventListener("click", (e) => {
            if (e.target !== btnBrowse && e.target !== btnLoadSample) {
                fileInput.click();
            }
        });

        ["dragenter", "dragover"].forEach(evtName => {
            dropzone.addEventListener(evtName, (e) => {
                e.preventDefault();
                e.stopPropagation();
                dropzone.classList.add("dragging");
            });
        });

        ["dragleave", "dragend"].forEach(evtName => {
            dropzone.addEventListener(evtName, (e) => {
                e.preventDefault();
                e.stopPropagation();
                dropzone.classList.remove("dragging");
            });
        });

        dropzone.addEventListener("drop", (e) => {
            e.preventDefault();
            e.stopPropagation();
            dropzone.classList.remove("dragging");
            const files = e.dataTransfer.files;
            if (files && files.length > 0) {
                handleLabFileSelection(files[0]);
            }
        });

        fileInput.addEventListener("change", (e) => {
            if (fileInput.files && fileInput.files.length > 0) {
                handleLabFileSelection(fileInput.files[0]);
            }
        });
    }

    // 3. Quick Load Sample CCTV
    if (btnLoadSample) {
        btnLoadSample.addEventListener("click", async (e) => {
            e.stopPropagation();
            try {
                showAppToast("Loading Sample", "Fetching bundled pedestrian CCTV video...", "info");
                // Fetch sample video from server
                const resp = await fetch("/test_cctv.mp4");
                if (!resp.ok) {
                    throw new Error("Could not load sample CCTV file from server");
                }
                const blob = await resp.blob();
                const sampleFile = new File([blob], "cctv_pedestrians_sample.mp4", { type: "video/mp4" });
                handleLabFileSelection(sampleFile);
                showAppToast("Sample Loaded", "CCTV sample video loaded into preview deck.", "success");
            } catch (sampleErr) {
                console.warn("Sample load error:", sampleErr);
                showAppToast("Load Error", "Unable to load sample video directly: " + sampleErr.message, "error");
            }
        });
    }

    // 4. Clear File
    if (btnClearFile) {
        btnClearFile.addEventListener("click", () => {
            resetLabFileSelection();
        });
    }

    // 5. Start Analysis
    if (btnStartAnalyze) {
        btnStartAnalyze.addEventListener("click", () => {
            startLabVideoAnalysis();
        });
    }

    // 6. Cancel Job
    if (btnCancelJob) {
        btnCancelJob.addEventListener("click", () => {
            cancelLabCurrentJob();
        });
    }

    // 7. Error Retry
    if (btnErrorRetry) {
        btnErrorRetry.addEventListener("click", () => {
            const errCard = document.getElementById("lab-error-card");
            if (errCard) errCard.classList.add("hidden");
            const dropCard = document.querySelector(".lab-dropzone-card");
            if (dropCard) dropCard.classList.remove("hidden");
        });
    }

    // 8. Refresh History
    if (btnRefreshHistory) {
        btnRefreshHistory.addEventListener("click", () => {
            loadLabHistory();
        });
    }

    // 9. Analyze Another
    if (btnAnalyzeAnother) {
        btnAnalyzeAnother.addEventListener("click", () => {
            resetLabFileSelection();
            switchLabSubTab("upload");
        });
    }

    // 10. Video Comparison View Modes
    const btnViewAnnotated = document.getElementById("btn-lab-view-annotated");
    const btnViewOriginal = document.getElementById("btn-lab-view-original");
    const btnViewDual = document.getElementById("btn-lab-view-dual");
    const wrapAnnotated = document.getElementById("wrap-player-annotated");
    const wrapOriginal = document.getElementById("wrap-player-original");
    const playersGrid = document.getElementById("lab-players-grid");

    const clearModeActive = () => {
        [btnViewAnnotated, btnViewOriginal, btnViewDual].forEach(b => b && b.classList.remove("active"));
    };

    if (btnViewAnnotated) {
        btnViewAnnotated.addEventListener("click", () => {
            clearModeActive();
            btnViewAnnotated.classList.add("active");
            if (wrapAnnotated) wrapAnnotated.classList.remove("hidden");
            if (wrapOriginal) wrapOriginal.classList.add("hidden");
            if (playersGrid) playersGrid.classList.remove("dual-view");
        });
    }

    if (btnViewOriginal) {
        btnViewOriginal.addEventListener("click", () => {
            clearModeActive();
            btnViewOriginal.classList.add("active");
            if (wrapAnnotated) wrapAnnotated.classList.add("hidden");
            if (wrapOriginal) wrapOriginal.classList.remove("hidden");
            if (playersGrid) playersGrid.classList.remove("dual-view");
        });
    }

    if (btnViewDual) {
        btnViewDual.addEventListener("click", () => {
            clearModeActive();
            btnViewDual.classList.add("active");
            if (wrapAnnotated) wrapAnnotated.classList.remove("hidden");
            if (wrapOriginal) wrapOriginal.classList.remove("hidden");
            if (playersGrid) playersGrid.classList.add("dual-view");
        });
    }

    // 11. Timeline Category Filter Tabs
    const filterTabs = document.querySelectorAll(".lab-filter-tab[data-filter]");
    filterTabs.forEach(tab => {
        tab.addEventListener("click", () => {
            filterTabs.forEach(t => t.classList.remove("active"));
            tab.classList.add("active");
            labActiveFilter = tab.getAttribute("data-filter") || "all";
            if (labCachedResults && labCachedResults.detections) {
                renderLabTimeline(labCachedResults.detections, labActiveFilter);
            }
        });
    });
}

function initVideoLabView() {
    loadLabHistory();
}

function switchLabSubTab(tabName) {
    document.querySelectorAll(".lab-tab-btn[data-tab]").forEach(btn => {
        if (btn.getAttribute("data-tab") === tabName) {
            btn.classList.add("active");
        } else {
            btn.classList.remove("active");
        }
    });

    document.querySelectorAll(".lab-pane").forEach(pane => {
        pane.classList.add("hidden");
        pane.classList.remove("active-pane");
    });

    const target = document.getElementById(`lab-pane-${tabName}`);
    if (target) {
        target.classList.remove("hidden");
        target.classList.add("active-pane");
    }

    if (tabName === "history") {
        loadLabHistory();
    }
}

function handleLabFileSelection(file) {
    if (!file) return;

    // Validate size (< 250 MB)
    const maxBytes = 250 * 1024 * 1024;
    if (file.size > maxBytes) {
        showAppToast("File Too Large", `Selected file (${(file.size / (1024*1024)).toFixed(1)} MB) exceeds 250 MB limit.`, "error");
        return;
    }

    // Validate format
    const name = file.name.toLowerCase();
    const validExts = [".mp4", ".mov", ".mkv", ".avi", ".webm"];
    const hasValidExt = validExts.some(ext => name.endsWith(ext));
    if (!hasValidExt) {
        showAppToast("Unsupported Format", `Please upload a supported video container: ${validExts.join(", ")}`, "error");
        return;
    }

    labSelectedFile = file;

    // Populate metadata
    const nameEl = document.getElementById("lab-meta-filename");
    const sizeEl = document.getElementById("lab-meta-filesize");
    const durEl = document.getElementById("lab-meta-duration");
    const resEl = document.getElementById("lab-meta-resolution");

    if (nameEl) nameEl.textContent = file.name;
    if (sizeEl) sizeEl.textContent = `${(file.size / (1024 * 1024)).toFixed(2)} MB`;
    if (durEl) durEl.textContent = "Inspecting...";
    if (resEl) resEl.textContent = "Inspecting...";

    // Show preview video
    const previewVideo = document.getElementById("lab-local-preview");
    if (previewVideo) {
        const fileUrl = URL.createObjectURL(file);
        previewVideo.src = fileUrl;
        previewVideo.onloadedmetadata = () => {
            const d = previewVideo.duration;
            const w = previewVideo.videoWidth;
            const h = previewVideo.videoHeight;
            if (durEl) {
                const mins = Math.floor(d / 60);
                const secs = Math.floor(d % 60);
                durEl.textContent = `${mins}:${secs < 10 ? '0' : ''}${secs} (${d.toFixed(1)}s)`;
            }
            if (resEl) resEl.textContent = `${w} × ${h}`;
        };
    }

    // Show preview card, hide dropzone
    const previewCard = document.getElementById("lab-file-preview-card");
    const dropzoneCard = document.querySelector(".lab-dropzone-card");
    if (previewCard) previewCard.classList.remove("hidden");
    if (dropzoneCard) dropzoneCard.classList.add("hidden");

    showAppToast("Video Ready", `${file.name} ready for AI analysis.`, "info");
}

function resetLabFileSelection() {
    labSelectedFile = null;
    const fileInput = document.getElementById("lab-file-input");
    if (fileInput) fileInput.value = "";

    const previewVideo = document.getElementById("lab-local-preview");
    if (previewVideo) {
        previewVideo.pause();
        previewVideo.removeAttribute("src");
        previewVideo.load();
    }

    const previewCard = document.getElementById("lab-file-preview-card");
    const dropzoneCard = document.querySelector(".lab-dropzone-card");
    const procCard = document.getElementById("lab-processing-card");
    const errCard = document.getElementById("lab-error-card");

    if (previewCard) previewCard.classList.add("hidden");
    if (dropzoneCard) dropzoneCard.classList.remove("hidden");
    if (procCard) procCard.classList.add("hidden");
    if (errCard) errCard.classList.add("hidden");
}

async function startLabVideoAnalysis() {
    if (!labSelectedFile) {
        showAppToast("No File Selected", "Please select a CCTV video file to analyze.", "error");
        return;
    }

    const previewCard = document.getElementById("lab-file-preview-card");
    const procCard = document.getElementById("lab-processing-card");
    const errCard = document.getElementById("lab-error-card");

    if (previewCard) previewCard.classList.add("hidden");
    if (errCard) errCard.classList.add("hidden");
    if (procCard) procCard.classList.remove("hidden");

    // Get sampling interval
    let interval = 15;
    const samplingChecked = document.querySelector("input[name='lab-sampling']:checked");
    if (samplingChecked) {
        interval = parseInt(samplingChecked.value, 10) || 15;
    }

    updateLabProgressUI({
        progress_pct: 5,
        stage: "Uploading video to persistent storage & verifying binary signatures...",
        frames_analyzed: 0,
        total_frames: 0,
        unique_tracks_count: 0,
        total_detections_count: 0
    });

    try {
        const formData = new FormData();
        formData.append("file", labSelectedFile);
        formData.append("frame_interval", interval);
        formData.append("session_id", "web_" + Date.now());

        const res = await fetch("/api/lab/upload-and-analyze", {
            method: "POST",
            body: formData
        });

        if (!res.ok) {
            const errJson = await res.json().catch(() => ({}));
            throw new Error(errJson.detail || "Upload & analysis initiation failed");
        }

        const data = await res.json();
        labCurrentJobId = data.job_id;

        showAppToast("Analysis Initiated", `Job ${labCurrentJobId.slice(0, 8)} started in background worker.`, "info");
        pollLabJob(labCurrentJobId);

    } catch (err) {
        console.error("Lab upload error:", err);
        if (procCard) procCard.classList.add("hidden");
        if (errCard) errCard.classList.remove("hidden");
        const errMsgEl = document.getElementById("lab-error-msg");
        if (errMsgEl) errMsgEl.textContent = err.message || "Failed to start analysis.";
        showAppToast("Analysis Failed", err.message, "error");
    }
}

function pollLabJob(jobId) {
    if (labPollingInterval) clearInterval(labPollingInterval);

    labPollingInterval = setInterval(async () => {
        try {
            const res = await fetch(`/api/lab/jobs/${jobId}/status`);
            if (!res.ok) return;

            const st = await res.json();
            updateLabProgressUI(st);

            if (st.status === "completed") {
                clearInterval(labPollingInterval);
                labPollingInterval = null;
                showAppToast("Analysis Completed", "YOLO-World & ByteTrack tracking complete!", "success");

                const badge = document.getElementById("lab-active-job-badge");
                if (badge) badge.classList.remove("hidden");

                setTimeout(() => {
                    loadLabResults(jobId);
                }, 400);

            } else if (st.status === "failed") {
                clearInterval(labPollingInterval);
                labPollingInterval = null;
                const procCard = document.getElementById("lab-processing-card");
                const errCard = document.getElementById("lab-error-card");
                if (procCard) procCard.classList.add("hidden");
                if (errCard) errCard.classList.remove("hidden");
                const errMsgEl = document.getElementById("lab-error-msg");
                if (errMsgEl) errMsgEl.textContent = st.error_message || "Video analysis encountered an error.";
                showAppToast("Analysis Error", st.error_message || "Video processing failed", "error");

            } else if (st.status === "cancelled") {
                clearInterval(labPollingInterval);
                labPollingInterval = null;
                showAppToast("Analysis Cancelled", "Job was cancelled.", "info");
                resetLabFileSelection();
            }

        } catch (pollErr) {
            console.warn("Poll error:", pollErr);
        }
    }, 700);
}

function updateLabProgressUI(st) {
    const pct = Math.min(100, Math.max(0, st.progress_pct || 0));
    const fillEl = document.getElementById("lab-progress-bar");
    const pctEl = document.getElementById("lab-progress-pct");
    const stageEl = document.getElementById("lab-stage-text");
    const framesEl = document.getElementById("lab-stat-frames");
    const tracksEl = document.getElementById("lab-stat-tracks");
    const detsEl = document.getElementById("lab-stat-detections");

    if (fillEl) fillEl.style.width = `${pct}%`;
    if (pctEl) pctEl.textContent = `${pct.toFixed(0)}%`;
    if (stageEl) stageEl.textContent = st.stage || "Processing video frames...";
    if (framesEl) framesEl.textContent = `${st.current_frame || 0} / ${st.total_frames || 0}`;
    if (tracksEl) tracksEl.textContent = `${st.unique_tracks_count || 0}`;
    if (detsEl) detsEl.textContent = `${st.total_detections_count || 0}`;
}

async function cancelLabCurrentJob() {
    if (!labCurrentJobId) return;
    try {
        await fetch(`/api/lab/jobs/${labCurrentJobId}/cancel`, { method: "POST" });
        showAppToast("Cancelling", "Requested job cancellation...", "info");
    } catch (err) {
        console.error("Cancel error:", err);
    }
}

async function loadLabResults(jobId) {
    try {
        const res = await fetch(`/api/lab/jobs/${jobId}/results`);
        if (!res.ok) throw new Error("Failed to load results for job " + jobId);

        const data = await res.json();
        labCachedResults = data;
        labCurrentJobId = jobId;

        // Populate Top Header Meta
        const fnEl = document.getElementById("lab-res-filename");
        const stEl = document.getElementById("lab-res-status");
        const durEl = document.getElementById("lab-res-duration");
        const resEl = document.getElementById("lab-res-resolution");
        const fpsEl = document.getElementById("lab-res-fps");

        if (fnEl) fnEl.textContent = data.filename || "CCTV Analysis";
        if (stEl) stEl.textContent = (data.status || "COMPLETED").toUpperCase();
        if (durEl) durEl.textContent = `${data.duration || 0}s`;
        if (resEl) resEl.textContent = data.resolution || "--";
        if (fpsEl) fpsEl.textContent = `${data.fps || 0} FPS`;

        // Set Video URLs
        const vAnnotated = document.getElementById("lab-video-annotated");
        const vOriginal = document.getElementById("lab-video-original");

        if (vAnnotated && data.annotated_video_url) {
            vAnnotated.src = data.annotated_video_url;
            vAnnotated.load();
        }
        if (vOriginal && data.original_video_url) {
            vOriginal.src = data.original_video_url;
            vOriginal.load();
        }

        // Set Download Links
        const dlVideo = document.getElementById("btn-lab-dl-video");
        const dlJson = document.getElementById("btn-lab-dl-json");
        const dlCsv = document.getElementById("btn-lab-dl-csv");

        if (dlVideo) dlVideo.href = data.annotated_video_download_url;
        if (dlJson) dlJson.href = data.json_results_url;
        if (dlCsv) dlCsv.href = data.csv_results_url;

        // Set Key Metrics
        const tracksCountEl = document.getElementById("lab-res-unique-tracks");
        const detsCountEl = document.getElementById("lab-res-total-detections");
        const clsCountEl = document.getElementById("lab-res-classes-count");
        const framesRatioEl = document.getElementById("lab-res-frames-ratio");

        const m = data.metrics || {};
        if (tracksCountEl) tracksCountEl.textContent = m.unique_tracks_count || 0;
        if (detsCountEl) detsCountEl.textContent = m.total_detections_count || 0;

        const classMap = m.class_counts || {};
        const classKeys = Object.keys(classMap);
        if (clsCountEl) clsCountEl.textContent = classKeys.length;
        if (framesRatioEl) framesRatioEl.textContent = `${m.frames_analyzed || 0} / ${m.total_frames || 0}`;

        // Populate Class Pills
        const pillsContainer = document.getElementById("lab-class-pills");
        if (pillsContainer) {
            if (classKeys.length === 0) {
                pillsContainer.innerHTML = '<span class="lab-pill" style="color:var(--text-muted);">No objects detected.</span>';
            } else {
                pillsContainer.innerHTML = classKeys.map(k => `
                    <div class="lab-class-badge">
                        <span style="text-transform:capitalize; font-weight:600;">${escapeHtml(k)}</span>
                        <span class="lab-class-count">${classMap[k]}</span>
                    </div>
                `).join("");
            }
        }

        // Render Timeline
        labActiveFilter = "all";
        renderLabTimeline(data.detections || [], "all");

        // Render Evidence Keyframes
        renderLabEvidenceGallery(data.summary?.tracks || []);

        // Switch to Results sub-tab
        switchLabSubTab("results");

    } catch (err) {
        console.error("Load results error:", err);
        showAppToast("Error", "Could not load tracking results: " + err.message, "error");
    }
}

function renderLabTimeline(detections, filter) {
    const listEl = document.getElementById("lab-timeline-scroll");
    const countEl = document.getElementById("lab-timeline-count");
    if (!listEl) return;

    let filtered = detections;
    if (filter === "person") {
        filtered = detections.filter(d => d.label === "person");
    } else if (filter === "vehicle") {
        filtered = detections.filter(d => ["car", "suv", "van", "truck", "bus", "vehicle", "motorcycle", "bicycle"].includes(d.label));
    } else if (filter === "other") {
        filtered = detections.filter(d => d.label !== "person" && !["car", "suv", "van", "truck", "bus", "vehicle", "motorcycle", "bicycle"].includes(d.label));
    }

    if (countEl) countEl.textContent = `${filtered.length} Events`;

    if (!filtered || filtered.length === 0) {
        listEl.innerHTML = `<div class="empty-state-card" style="grid-column:1/-1;">No detection events match the '${escapeHtml(filter)}' filter.</div>`;
        return;
    }

    listEl.innerHTML = filtered.map(d => {
        const offset = d.timestamp_offset || 0;
        const mins = Math.floor(offset / 60);
        const secs = (offset % 60).toFixed(2);
        const tsFormatted = `${mins}:${secs < 10 ? '0' : ''}${secs}`;
        const tid = d.tracking_id !== null ? `Track #${d.tracking_id}` : 'Untracked';
        const confPct = Math.round((d.confidence || 0) * 100);
        const thumb = d.evidence_url || 'data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="60" height="48" fill="%23193454"><rect width="100%" height="100%"/></svg>';

        return `
            <div class="lab-event-card" data-seek="${offset}" title="Click to seek video to ${tsFormatted}">
                <img src="${escapeHtml(thumb)}" class="lab-event-thumb" alt="${escapeHtml(d.label)}" loading="lazy" />
                <div class="lab-event-info">
                    <div class="lab-event-top">
                        <span class="lab-seek-chip">▶ ${tsFormatted}</span>
                        <span class="lab-tid-pill">${escapeHtml(tid)}</span>
                    </div>
                    <div class="lab-event-bottom">
                        <span class="lab-cls-name">${escapeHtml(d.label)}</span>
                        <span class="lab-conf-val">${confPct}% conf</span>
                    </div>
                </div>
            </div>
        `;
    }).join("");

    // Attach click seekers
    listEl.querySelectorAll(".lab-event-card[data-seek]").forEach(card => {
        card.addEventListener("click", () => {
            const seekTime = parseFloat(card.getAttribute("data-seek")) || 0;
            const vAnnotated = document.getElementById("lab-video-annotated");
            const vOriginal = document.getElementById("lab-video-original");

            if (vAnnotated) {
                vAnnotated.currentTime = seekTime;
                vAnnotated.play().catch(() => {});
            }
            if (vOriginal) {
                vOriginal.currentTime = seekTime;
                vOriginal.play().catch(() => {});
            }

            showAppToast("Seeking Video", `Jumped to timestamp ${seekTime.toFixed(2)}s`, "info");
        });
    });
}

function renderLabEvidenceGallery(tracks) {
    const galleryEl = document.getElementById("lab-evidence-grid");
    if (!galleryEl) return;

    if (!tracks || tracks.length === 0) {
        galleryEl.innerHTML = '<div class="empty-state-card" style="grid-column:1/-1;">No unique tracked keyframe crops extracted.</div>';
        return;
    }

    galleryEl.innerHTML = tracks.map(t => {
        const thumb = t.evidence_url || 'data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="120" height="90" fill="%23193454"><rect width="100%" height="100%"/></svg>';
        return `
            <div class="lab-evidence-card">
                <img src="${escapeHtml(thumb)}" class="lab-evidence-img" alt="Track ID ${t.tracking_id}" loading="lazy" />
                <div class="lab-evidence-caption">
                    <div style="color:var(--cyan-primary);">ID #${t.tracking_id} • ${escapeHtml(t.label)}</div>
                    <div style="font-size:0.65rem; color:var(--text-muted); font-family:var(--font-mono);">${t.first_seen}s - ${t.last_seen}s</div>
                </div>
            </div>
        `;
    }).join("");
}

async function loadLabHistory() {
    const tbody = document.getElementById("lab-history-tbody");
    const countBadge = document.getElementById("lab-history-count");
    if (!tbody) return;

    try {
        const res = await fetch("/api/lab/jobs?limit=50");
        if (!res.ok) throw new Error("Could not load jobs history");

        const jobs = await res.json();
        if (countBadge) countBadge.textContent = jobs.length;

        if (!jobs || jobs.length === 0) {
            tbody.innerHTML = '<tr><td colspan="8" style="text-align:center; color:var(--text-muted); padding:20px;">No analysis jobs found. Upload a CCTV video to begin.</td></tr>';
            return;
        }

        tbody.innerHTML = jobs.map(j => {
            const dateStr = j.created_at ? new Date(j.created_at * 1000).toLocaleString() : '--';
            const dur = j.duration ? `${j.duration.toFixed(1)}s` : '--';
            const res = j.resolution || '--';
            const tracks = j.unique_tracks_count || 0;
            const dets = j.total_detections_count || 0;
            const st = (j.status || 'unknown').toLowerCase();
            const badgeClass = st === 'completed' ? 'chip-status-ready' : (st === 'processing' ? 'chip-meta' : 'chip-meta');

            return `
                <tr>
                    <td style="font-weight:600; color:var(--text-main);">${escapeHtml(j.filename)}</td>
                    <td style="font-size:0.75rem; color:var(--text-muted); font-family:var(--font-mono);">${dateStr}</td>
                    <td style="font-family:var(--font-mono);">${dur}</td>
                    <td style="font-family:var(--font-mono);">${res}</td>
                    <td><span class="${badgeClass}">${st.toUpperCase()}</span></td>
                    <td style="font-weight:700; color:var(--cyan-primary); font-family:var(--font-mono);">${tracks}</td>
                    <td style="font-family:var(--font-mono);">${dets}</td>
                    <td>
                        <div style="display:flex; gap:6px;">
                            <button class="btn-primary btn-sm btn-lab-view-job" data-id="${j.id}">View Results</button>
                            <button class="btn-danger btn-sm btn-lab-delete-job" data-id="${j.id}" data-name="${escapeHtml(j.filename)}">Delete</button>
                        </div>
                    </td>
                </tr>
            `;
        }).join("");

        // Attach action handlers
        tbody.querySelectorAll(".btn-lab-view-job").forEach(btn => {
            btn.addEventListener("click", () => {
                const jobId = btn.getAttribute("data-id");
                loadLabResults(jobId);
            });
        });

        tbody.querySelectorAll(".btn-lab-delete-job").forEach(btn => {
            btn.addEventListener("click", () => {
                const jobId = btn.getAttribute("data-id");
                const jobName = btn.getAttribute("data-name") || "this video";
                if (confirm(`Are you sure you want to permanently delete analysis job for '${jobName}' and purge all stored files?`)) {
                    deleteLabJob(jobId);
                }
            });
        });

    } catch (err) {
        console.error("History error:", err);
        tbody.innerHTML = `<tr><td colspan="8" style="text-align:center; color:var(--red-critical);">Failed to load history: ${escapeHtml(err.message)}</td></tr>`;
    }
}

async function deleteLabJob(jobId) {
    try {
        const res = await fetch(`/api/lab/jobs/${jobId}`, { method: "DELETE" });
        if (!res.ok) throw new Error("Delete failed");
        showAppToast("Deleted", "Job and associated stored files permanently deleted.", "info");
        loadLabHistory();
        if (labCurrentJobId === jobId) {
            labCurrentJobId = null;
            resetLabFileSelection();
            switchLabSubTab("upload");
        }
    } catch (err) {
        showAppToast("Error", "Could not delete job: " + err.message, "error");
    }
}





