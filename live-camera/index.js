(function () {
    window.Liferay.on('allPortletsReady', function () {
        const slider = document.getElementById("routeList");
        const prevBtn = document.getElementById("prevBtn");
        const nextBtn = document.getElementById("nextBtn");

        if (!slider) {
            return;
        }

        const updateNavButtons = () => {
            const { scrollLeft, scrollWidth, clientWidth } = slider;

            if (prevBtn) {
                if (scrollLeft <= 5) prevBtn.classList.add('hidden');
                else prevBtn.classList.remove('hidden');
            }

            if (nextBtn) {
                if (scrollLeft + clientWidth >= scrollWidth - 5) nextBtn.classList.add('hidden');
                else nextBtn.classList.remove('hidden');
            }
        };

        const scrollStep = 400;

        if (prevBtn) {
            prevBtn.addEventListener("click", () => {
                slider.scrollBy({ left: -scrollStep, behavior: "smooth" });
            });
        }

        if (nextBtn) {
            nextBtn.addEventListener("click", () => {
                slider.scrollBy({ left: scrollStep, behavior: "smooth" });
            });
        }

        slider.addEventListener("scroll", updateNavButtons);
        setTimeout(updateNavButtons, 600);

    })
})();

(function () {
    const loadGoogleMaps = () => {
        if (window.google && window.google.maps) {
            initMap();
            return;
        }

        const script = document.createElement("script");
        script.src = "https://maps.googleapis.com/maps/api/js?key=AIzaSyB1hMVFgbfsjv4AMtE0T3eoQds-TuZQkrY";
        script.async = true;
        script.defer = true;

        script.onload = initMap;
        script.onerror = () => {
            console.error("Không load được Google Maps API");
        };

        document.head.appendChild(script);
    };

    window.__loadGoogleMaps__ = loadGoogleMaps;
})();
const slider = document.getElementById("routeList");
let isDown = false;
let startX;
let scrollLeft;
let velocity = 0;
let rafID;

slider.addEventListener("mousedown", (e) => {
    isDown = true;
    startX = e.pageX;
    scrollLeft = slider.scrollLeft;
    velocity = 0;
    cancelAnimationFrame(rafID);
});
slider.addEventListener("mouseleave", endDrag);
slider.addEventListener("mouseup", endDrag);
slider.addEventListener("mousemove", (e) => {
    if (!isDown) return;
    e.preventDefault();
    const x = e.pageX;
    const walk = x - startX;
    slider.scrollLeft = scrollLeft - walk;
    velocity = walk * 0.15;
});
function endDrag() {
    if (!isDown) return;
    isDown = false;
    momentum();
}
function momentum() {
    slider.scrollLeft -= velocity;
    velocity *= 0.95;
    if (Math.abs(velocity) > 0.5) {
        rafID = requestAnimationFrame(momentum);
    }
}
slider.addEventListener(
    "wheel",
    (e) => {
        if (Math.abs(e.deltaY) > Math.abs(e.deltaX)) {
            e.preventDefault();
            slider.scrollLeft += e.deltaY;
        }
    },
    { passive: false }
);
const API_BASE_URL = "";
const API_HIGHWAYS_URL = `${API_BASE_URL}/o/c/highways/`;
const CAMERA_SHOW_STATE_API_URL = `${API_BASE_URL}/o/vec-setting-camera-show-state`;
const API_HEADERS = {
    accept: "application/json",
};
let ROUTE_DATA = [];
const FALLBACK_HIGHWAY_ITEMS = [];
async function fetchHighwaysData() {
    try {
        const response = await fetch(API_HIGHWAYS_URL, {
            method: "GET",
            headers: API_HEADERS,
        });
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const data = await response.json();
        return data.items || [];
    } catch (error) {
        console.error("Error fetching highways data:", error);
        return FALLBACK_HIGHWAY_ITEMS;
    }
}
const CAMERA_HIGHWAY_CONFIGS = {
    42753: {
        apiBasePath: "/o/its-hld",
    },
    44147: {
        apiBasePath: "/o/its",
    },
};
const HLS_JS_URL = "https://cdn.jsdelivr.net/npm/hls.js@1.5.20/dist/hls.min.js";
const HLS_ATTACH_RETRY_COUNT = 3;
const HLS_ATTACH_RETRY_DELAY_MS = 1200;
const CAMERA_STARTUP_MIN_DURATION_MS = 8000;
const CAMERA_STARTUP_STATUS = "Đang khởi động camera...";
const CAMERA_THUMBNAIL_FALLBACK_URL = "https://upload.wikimedia.org/wikipedia/commons/1/14/No_Image_Available.jpg?utm_source=commons.wikimedia.org&utm_campaign=index&utm_content=original";
let currentCameraList = [];
let hlsScriptPromise = null;
let activeCameraSession = null;
let cameraRenderRequestId = 0;
let cameraModalRequestId = 0;
let cacheBustCounter = 0;
const cameraShowStateCache = new Map();
const cameraListCache = new Map();

function escapeHtml(value) {
    return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
}

function delay(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

function withCacheBust(url) {
    const value = `${Date.now()}-${cacheBustCounter++}`;

    try {
        const parsedUrl = new URL(url, window.location.href);
        parsedUrl.searchParams.set("_", value);
        return parsedUrl.href;
    } catch (error) {
        const separator = url.includes("?") ? "&" : "?";
        return `${url}${separator}_=${encodeURIComponent(value)}`;
    }
}

function normalizeCameraName(name) {
    const rawName = typeof name === "string" ? name : String(name ?? "");

    if (!rawName) {
        return "";
    }

    const ipv4Segment = "(?:25[0-5]|2[0-4]\\d|1\\d\\d|[1-9]?\\d)";
    const ipv4Pattern = new RegExp(
        `\\b${ipv4Segment}(?:\\.${ipv4Segment}){3}\\b`,
        "g"
    );
    const parenthesizedIpv4Pattern = new RegExp(
        `\\([^()]*\\b${ipv4Segment}(?:\\.${ipv4Segment}){3}\\b[^()]*\\)`,
        "g"
    );

    return rawName
        .replace(parenthesizedIpv4Pattern, " ")
        .replace(ipv4Pattern, " ")
        .replace(/\s+/g, " ")
        .trim();
}

function getCameraCoordinate(camera, keys) {
    for (const key of keys) {
        const value = Number(camera[key]);
        if (Number.isFinite(value)) return value;
    }

    return null;
}

function mapCameraLocation(camera) {
    const lat = getCameraCoordinate(camera, ["lat", "latitude", "camera_lat", "cameraLatitude"]);
    const lng = getCameraCoordinate(camera, ["lng", "long", "lon", "longitude", "camera_lng", "camera_long", "cameraLongitude"]);

    if (lat === null || lng === null) return null;

    return {
        lat,
        lng,
        id: camera.camera_id,
        name: camera.name || "Camera",
    };
}

function getCameraConfig(highwayId) {
    return CAMERA_HIGHWAY_CONFIGS[Number(highwayId)] || null;
}

function getCameraApiUrl(highwayId) {
    const config = getCameraConfig(highwayId);

    if (!config?.apiBasePath) {
        return null;
    }

    return `https://portal.tctvec.vn${config.apiBasePath}/api/cameras`;
}

async function fetchCameras(highwayId) {
    const cameraApiUrl = getCameraApiUrl(highwayId);

    if (!cameraApiUrl) {
        return [];
    }

    if (cameraListCache.has(cameraApiUrl)) {
        return cameraListCache.get(cameraApiUrl);
    }

    const response = await fetch(withCacheBust(cameraApiUrl), {
        method: "GET",
        cache: "no-store",
        headers: { accept: "application/json" },
    });

    if (!response.ok) {
        throw new Error(`Camera API error: ${response.status}`);
    }

    const data = await response.json();
    const cameras = Array.isArray(data.items)
        ? data.items.map((camera) => ({
            ...camera,
            name: normalizeCameraName(camera.name) || "Camera",
            __cameraApiUrl: cameraApiUrl,
            __highwayId: Number(highwayId),
        }))
        : [];

    cameraListCache.set(cameraApiUrl, cameras);

    return cameras;
}

function getCameraIdentity(camera) {
    return String(camera?.camera_id ?? camera?.id ?? "").trim();
}

function createCameraShowStateMap(items) {
    return new Map(
        (items || [])
            .filter((item) => item && item.cameraId)
            .map((item) => [
                String(item.cameraId),
                {
                    cameraId: String(item.cameraId),
                    internetVisible: item.internetVisible !== false,
                },
            ])
    );
}

async function fetchCameraShowState(highwayId) {
    const normalizedHighwayId = Number(highwayId);

    if (!normalizedHighwayId) {
        return [];
    }

    if (cameraShowStateCache.has(normalizedHighwayId)) {
        return cameraShowStateCache.get(normalizedHighwayId);
    }

    try {
        const response = await fetch(
            `${CAMERA_SHOW_STATE_API_URL}?highwayId=${encodeURIComponent(normalizedHighwayId)}`,
            {
                method: "GET",
                credentials: "include",
                headers: {
                    accept: "application/json",
                    "X-Requested-With": "XMLHttpRequest",
                },
            }
        );

        if (!response.ok) {
            throw new Error(`Camera show state API error: ${response.status}`);
        }

        const data = await response.json();
        const items = Array.isArray(data?.items) ? data.items : [];

        cameraShowStateCache.set(normalizedHighwayId, items);

        return items;
    } catch (error) {
        console.error("Error fetching camera show state:", error);
        return [];
    }
}

function filterCamerasByInternetShowState(cameras, items) {
    const settingsMap = createCameraShowStateMap(items);

    return (cameras || []).filter((camera) => {
        const cameraId = getCameraIdentity(camera);

        if (!cameraId) {
            return false;
        }

        const setting = settingsMap.get(cameraId);

        if (!setting) {
            return true;
        }

        return setting.internetVisible !== false;
    });
}

function loadHlsScript() {
    if (window.Hls) return Promise.resolve();
    if (hlsScriptPromise) return hlsScriptPromise;

    hlsScriptPromise = new Promise((resolve, reject) => {
        const script = document.createElement("script");
        script.src = HLS_JS_URL;
        script.async = true;
        script.onload = resolve;
        script.onerror = () => reject(new Error("Không tải được HLS.js"));
        document.head.appendChild(script);
    });

    return hlsScriptPromise;
}

function createCacheBustingHlsLoader() {
    const BaseLoader = window.Hls?.DefaultConfig?.loader;
    if (!BaseLoader) return undefined;

    return class CacheBustingHlsLoader extends BaseLoader {
        load(context, config, callbacks) {
            if (context && (context.type === "manifest" || context.type === "level")) {
                context.url = withCacheBust(context.url);
            }

            super.load(context, config, callbacks);
        }
    };
}

async function renderCameras(highwayId) {
    const cameraContent = document.querySelector('.td-camera__content');
    if (!cameraContent) return;

    const requestId = ++cameraRenderRequestId;
    const cameraApiUrl = getCameraApiUrl(highwayId);

    if (!cameraApiUrl) {
        currentCameraList = [];
        cameraContent.innerHTML = '<div class="p-4 text-gray-500">Không có camera trực tuyến</div>';
        if (currentRoute?.mapData) currentRoute.mapData.cameraLocations = [];
        return;
    }

    cameraContent.innerHTML = '<div class="p-4 text-gray-500">Đang tải camera...</div>';

    let cameras = [];
    try {
        const [cameraData, cameraShowStates] = await Promise.all([
            fetchCameras(highwayId),
            fetchCameraShowState(highwayId),
        ]);

        cameras = filterCamerasByInternetShowState(cameraData, cameraShowStates);
    } catch (error) {
        if (requestId !== cameraRenderRequestId) return;
        console.error("Error fetching cameras:", error);
        cameraContent.innerHTML = '<div class="p-4 text-red-500">Không tải được danh sách camera</div>';
        return;
    }

    if (requestId !== cameraRenderRequestId) return;

    currentCameraList = cameras;

    if (!cameras.length) {
        cameraContent.innerHTML = '<div class="p-4 text-gray-500">Không có camera trực tuyến</div>';
        return;
    }

    cameraContent.innerHTML = cameras
        .map(
            (camera) => `
        <div class="camera-box" data-camera-id="${escapeHtml(camera.camera_id)}">
          <div class="camera-label">${escapeHtml(camera.name || 'Camera')}</div>
          <img class="camera-thumbnail" src="${escapeHtml(camera.thumbnail_url || CAMERA_THUMBNAIL_FALLBACK_URL)}" alt="${escapeHtml(camera.name || 'Camera')}" />
          <button class="camera-play-btn">
            <img src="https://res.cloudinary.com/drwairjk5/image/upload/v1767609285/Variant3_e7bc0u.svg" alt="Play" />
          </button>
        </div>
      `
        )
        .join("");

    const cameraBoxes = cameraContent.querySelectorAll(".camera-box");
    cameraBoxes.forEach((box) => {
        const thumbnail = box.querySelector(".camera-thumbnail");
        if (thumbnail) {
            thumbnail.addEventListener("error", () => {
                thumbnail.src = CAMERA_THUMBNAIL_FALLBACK_URL;
            }, { once: true });
        }

        box.addEventListener("click", () => {
            const cameraId = box.getAttribute("data-camera-id");
            const camera = cameras.find((item) => item.camera_id === cameraId);
            if (camera) openCameraModal(camera);
        });
    });

    const cameraLocations = cameras
        .map(mapCameraLocation)
        .filter(Boolean);

    if (currentRoute && currentRoute.mapData) {
        currentRoute.mapData.cameraLocations = cameraLocations;
    }

    const routeInData = ROUTE_DATA.find(r => r.id === highwayId);
    if (routeInData && routeInData.mapData) {
        routeInData.mapData.cameraLocations = cameraLocations;
    }

    if (map && currentRoute) {
        clearMarkers();
        displayMarkers(currentRoute);
    }
}

async function startCameraWatch(camera) {
    const cameraApiUrl = camera?.__cameraApiUrl || getCameraApiUrl(camera?.__highwayId);

    if (!cameraApiUrl) {
        throw new Error("Không tìm thấy cấu hình camera cho tuyến này");
    }

    const response = await fetch(withCacheBust(`${cameraApiUrl}/${encodeURIComponent(camera.camera_id)}/watch/start`), {
        method: "POST",
        cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
    });

    if (!response.ok) {
        throw new Error(`Watch start error: ${response.status}`);
    }

    const data = await response.json();
    if (!data.hls_url) {
        throw new Error("Camera chưa có luồng HLS");
    }

    // Trả kèm apiUrl để session giữ đúng instance của tuyến; heartbeat và watch/stop
    // sau đó không phải tra lại từ danh sách camera nữa.
    return { ...data, apiUrl: cameraApiUrl };
}

function stopCameraWatch(apiUrl, cameraId, sessionId, useBeacon = false) {
    if (!apiUrl || !cameraId || !sessionId) return;

    const url = `${apiUrl}/${encodeURIComponent(cameraId)}/watch/stop`;
    const body = JSON.stringify({ session_id: sessionId });

    // Khi tab đang bị đóng thì fetch thường bị hủy giữa đường, nên ưu tiên sendBeacon.
    // sendBeacon trả false nếu queue đầy hoặc request cần preflight (cross-origin với
    // Content-Type application/json), khi đó phải rơi xuống fetch keepalive.
    if (useBeacon && navigator.sendBeacon) {
        const queued = navigator.sendBeacon(url, new Blob([body], { type: "application/json" }));
        if (queued) return;
    }

    fetch(url, {
        method: "POST",
        keepalive: true,
        headers: { "Content-Type": "application/json" },
        body,
    }).catch((error) => {
        console.error("Camera watch stop failed:", error);
    });
}

function teardownCameraSession(session, useBeacon = false) {
    // released chặn nhả hai lần: một session có thể bị đóng modal nhả trước, rồi
    // promise attach HLS đang chờ mới resolve và nhả lần nữa.
    if (!session || session.released) return;
    session.released = true;

    if (session.heartbeatTimer) {
        clearInterval(session.heartbeatTimer);
    }

    if (session.hls) {
        session.hls.destroy();
    }

    // Bắt buộc: không gọi /watch/stop thì backend chỉ biết viewer đã đi sau khi
    // heartbeat TTL 90s hết hạn, và trong 90s đó worker vẫn giữ session Milestone.
    stopCameraWatch(session.apiUrl, session.cameraId, session.sessionId, useBeacon);
}

function releaseActiveCameraWatch(useBeacon = false) {
    const session = activeCameraSession;
    activeCameraSession = null;
    teardownCameraSession(session, useBeacon);
}

function startCameraHeartbeat(apiUrl, cameraId, sessionId) {
    return setInterval(() => {
        // apiUrl lấy từ session, không tra lại currentCameraList: đổi tuyến trong lúc
        // modal đang mở làm find() trả undefined và heartbeat ngừng lặng lẽ, để backend
        // thu hồi session sau TTL 90s ngay khi người dùng vẫn đang xem.
        fetch(`${apiUrl}/${encodeURIComponent(cameraId)}/watch/heartbeat`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ session_id: sessionId }),
        }).catch((error) => {
            console.error("Camera heartbeat failed:", error);
        });
    }, 30000);
}

async function attachHlsStream(video, hlsUrl) {
    await loadHlsScript();

    if (window.Hls?.isSupported()) {
        const CacheBustingHlsLoader = createCacheBustingHlsLoader();
        const hls = new window.Hls({
            loader: CacheBustingHlsLoader,
            lowLatencyMode: true,
            liveSyncDurationCount: 2,
            manifestLoadingMaxRetry: 6,
            manifestLoadingRetryDelay: 500,
            levelLoadingMaxRetry: 6,
            levelLoadingRetryDelay: 500,
            fragLoadingMaxRetry: 6,
            fragLoadingRetryDelay: 500,
            xhrSetup(xhr) {
                xhr.setRequestHeader("Cache-Control", "no-cache");
                xhr.setRequestHeader("Pragma", "no-cache");
            },
        });
        hls.loadSource(hlsUrl);
        hls.attachMedia(video);
        hls.on(window.Hls.Events.ERROR, function (_, data) {
            if (!data.fatal) return;

            if (data.type === window.Hls.ErrorTypes.NETWORK_ERROR) {
                hls.startLoad();
                return;
            }

            if (data.type === window.Hls.ErrorTypes.MEDIA_ERROR) {
                hls.recoverMediaError();
                return;
            }

            hls.destroy();
        });
        return hls;
    }

    video.src = withCacheBust(hlsUrl);
    return null;
}

async function attachHlsStreamWithRetry(video, hlsUrl) {
    let lastError;

    for (let attempt = 1; attempt <= HLS_ATTACH_RETRY_COUNT; attempt += 1) {
        try {
            return await attachHlsStream(video, hlsUrl);
        } catch (error) {
            lastError = error;
            video.pause();
            video.removeAttribute("src");
            video.load();
            if (attempt < HLS_ATTACH_RETRY_COUNT) {
                await delay(HLS_ATTACH_RETRY_DELAY_MS);
            }
        }
    }

    throw lastError;
}

function getCameraModalFullscreenIcon(isFullscreen) {
    if (isFullscreen) {
        return `
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <path d="M9 15H5V19" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
        <path d="M15 9H19V5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
        <path d="M5 15L10 10" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
        <path d="M19 9L14 14" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
      </svg>
    `;
    }

    return `
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M9 5H5V9" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
      <path d="M15 19H19V15" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
      <path d="M5 9L10 14" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
      <path d="M19 15L14 10" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
    </svg>
  `;
}

function syncCameraModalFullscreenButton() {
    const videoContainer = document.getElementById("cameraModalVideoContainer");
    const fullscreenButton = document.getElementById("cameraModalFullscreenBtn");
    if (!videoContainer || !fullscreenButton) return;

    const isFullscreen = document.fullscreenElement === videoContainer;
    fullscreenButton.innerHTML = getCameraModalFullscreenIcon(isFullscreen);
}

async function toggleCameraModalFullscreen() {
    const videoContainer = document.getElementById("cameraModalVideoContainer");
    if (!videoContainer) return;

    try {
        if (document.fullscreenElement === videoContainer) {
            await document.exitFullscreen();
        } else {
            await videoContainer.requestFullscreen();
        }
    } catch (error) {
        console.error("Fullscreen toggle failed:", error);
    }
}

async function openCameraModal(camera) {
    const requestId = ++cameraModalRequestId;
    let modal = document.getElementById("cameraModal");
    if (!modal) {
        modal = document.createElement("div");
        modal.id = "cameraModal";
        modal.className = "hidden fixed inset-0 z-100 overflow-y-auto";
        modal.innerHTML = `
      <div class="fixed inset-0 bg-black bg-opacity-85 transition-opacity" onclick="closeCameraModal()"></div>
      <div class="flex min-h-full items-center justify-center p-4">
        <div class="relative bg-white rounded-lg shadow-2xl max-w-[1000px] w-full overflow-hidden" style="padding: 20px;">
          <button onclick="closeCameraModal()" class="absolute top-2 right-2 z-10 bg-red-500 hover:bg-red-600 transition-colors rounded-full flex items-center justify-center" style="width: 25px; height: 25px; border: none; cursor: pointer;">
            <span style="color: white; font-size: 14px; font-weight: 700; line-height: 1;">×</span>
          </button>
          <div>
            <div id="cameraModalVideoContainer" class="relative bg-black rounded-lg overflow-hidden" style="aspect-ratio: 16/9; margin-bottom: 0;">
              <video id="cameraVideo" class="w-full h-full" autoplay muted playsinline disablepictureinpicture controlslist="nodownload noplaybackrate nofullscreen noremoteplayback" style="border: 0;"></video>
              <button id="cameraModalFullscreenBtn" type="button" aria-label="Toggle fullscreen" style="position:absolute;right:16px;bottom:16px;z-index:3;width:42px;height:42px;border:none;border-radius:999px;background:rgba(0,0,0,.72);color:#fff;display:flex;align-items:center;justify-content:center;cursor:pointer;transition:background .2s ease,transform .2s ease;">
                ${getCameraModalFullscreenIcon(false)}
              </button>
              <div id="cameraModalStatus" style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:white;background:rgba(0,0,0,.75);font-size:14px;z-index:2;">
                ${CAMERA_STARTUP_STATUS}
              </div>
            </div>
            <h3 class="text-gray-900 text-lg font-semibold" id="cameraModalTitle" style="margin: 20px 0 0 0; padding: 0; color: #333; font-size: 18px; font-weight: 600;"></h3>
          </div>
        </div>
      </div>
    `;
        document.body.appendChild(modal);
    }

    releaseActiveCameraWatch();

    document.getElementById("cameraModalTitle").textContent = camera.name || "Camera";
    const video = document.getElementById("cameraVideo");
    const status = document.getElementById("cameraModalStatus");
    const fullscreenButton = document.getElementById("cameraModalFullscreenBtn");

    if (!modal.dataset.bound) {
        document.addEventListener("fullscreenchange", syncCameraModalFullscreenButton);
        video.addEventListener("pause", () => {
            if (status.style.display === "none" && !modal.classList.contains("hidden")) {
                video.play().catch(() => { });
            }
        });
        video.addEventListener("contextmenu", (event) => {
            event.preventDefault();
        });
        fullscreenButton.addEventListener("click", () => {
            toggleCameraModalFullscreen();
        });
        fullscreenButton.addEventListener("mouseenter", () => {
            fullscreenButton.style.background = "rgba(0,0,0,.84)";
            fullscreenButton.style.transform = "scale(1.05)";
        });
        fullscreenButton.addEventListener("mouseleave", () => {
            fullscreenButton.style.background = "rgba(0,0,0,.72)";
            fullscreenButton.style.transform = "scale(1)";
        });
        modal.dataset.bound = "true";
    }

    video.pause();
    video.removeAttribute("src");
    video.load();
    status.textContent = CAMERA_STARTUP_STATUS;
    status.style.display = "flex";
    modal.classList.remove("hidden");
    document.body.style.overflow = "hidden";
    syncCameraModalFullscreenButton();

    const startupStartedAt = Date.now();
    const waitForMinimumStartupDuration = async () => {
        const remainingTime = CAMERA_STARTUP_MIN_DURATION_MS - (Date.now() - startupStartedAt);
        if (remainingTime > 0) {
            await delay(remainingTime);
        }
    };

    let session = null;

    try {
        const watchData = await startCameraWatch(camera);

        // Ghi nhận session NGAY khi backend đã tạo, không chờ HLS attach xong. Attach
        // có thể mất nhiều giây; nếu chờ tới lúc đó mới gán activeCameraSession thì
        // đóng modal giữa lúc đang khởi động sẽ không gửi được /watch/stop.
        session = {
            cameraId: camera.camera_id,
            sessionId: watchData.session_id,
            apiUrl: watchData.apiUrl,
            heartbeatTimer: null,
            hls: null,
            released: false,
        };

        if (requestId !== cameraModalRequestId) {
            teardownCameraSession(session);
            return;
        }

        activeCameraSession = session;

        const hls = await attachHlsStreamWithRetry(video, watchData.hls_url);

        // activeCameraSession !== session nghĩa là trong lúc chờ đã có camera khác được
        // mở, hoặc modal đã bị đóng; nhả đúng session của mình, không đụng session mới.
        if (requestId !== cameraModalRequestId || activeCameraSession !== session) {
            if (hls) hls.destroy();
            teardownCameraSession(session);
            return;
        }

        session.hls = hls;
        session.heartbeatTimer = startCameraHeartbeat(session.apiUrl, camera.camera_id, session.sessionId);

        await waitForMinimumStartupDuration();

        // Vẫn phải kiểm tra lại sau khi chờ đủ startup: người dùng có thể đã đóng modal
        // trong 8 giây đó.
        if (requestId !== cameraModalRequestId || activeCameraSession !== session) {
            if (activeCameraSession === session) {
                activeCameraSession = null;
            }
            teardownCameraSession(session);
            return;
        }

        status.style.display = "none";
        video.play().catch(() => { });
    } catch (error) {
        console.error("Error opening camera:", error);

        // watch/start có thể đã tạo session rồi mới lỗi ở bước HLS: phải nhả, nếu không
        // worker cứ chạy tới khi heartbeat TTL hết hạn.
        if (activeCameraSession === session) {
            activeCameraSession = null;
        }
        teardownCameraSession(session);

        await waitForMinimumStartupDuration();
        if (requestId !== cameraModalRequestId) {
            return;
        }

        status.textContent = "Không mở được luồng camera";
    }
}

window.closeCameraModal = async function () {
    cameraModalRequestId += 1;
    const modal = document.getElementById("cameraModal");
    if (modal) {
        releaseActiveCameraWatch();
        const video = document.getElementById("cameraVideo");
        const videoContainer = document.getElementById("cameraModalVideoContainer");

        if (document.fullscreenElement === videoContainer) {
            try {
                await document.exitFullscreen();
            } catch (error) {
                console.error("Exit fullscreen failed:", error);
            }
        }

        video.pause();
        video.removeAttribute("src");
        video.load();
        modal.classList.add("hidden");
        document.body.style.overflow = "";
    }
}

// Đóng tab, F5, chuyển trang: pagehide là event duy nhất chắc chắn chạy trên cả
// desktop và mobile Safari, nên nhả session ở đây.
window.addEventListener("pagehide", () => {
    releaseActiveCameraWatch(true);
});

window.openCameraFromMap = function (cameraId) {
    const camera = currentCameraList.find((item) => item.camera_id === cameraId);
    if (camera) openCameraModal(camera);
}
function mapApiDataToRouteData(apiItems) {
    return apiItems.map((item) => {
        const locationParts = (item.location || "").split(" - ");
        const startName = locationParts[0] || "";
        const endName = locationParts[locationParts.length - 1] || "";

        const imgUrl = item.image && item.image.link && item.image.link.href ? `${API_BASE_URL}${item.image.link.href}` : "https://placehold.co/400x165";

        const startLat = parseFloat(item.startLat) || 0;
        const startLng = parseFloat(item.startLng) || 0;
        const endLat = parseFloat(item.endLat) || 0;
        const endLng = parseFloat(item.endLng) || 0;

        const drivingLanes = item.drivingLaneNum || "";
        const emergencyLanes = item.emergencyLaneNum || "";
        let lanesInfo = "";
        if (drivingLanes) {
            lanesInfo = `${drivingLanes} làn xe chạy`;
            if (emergencyLanes) {
                lanesInfo += `, ${emergencyLanes} làn dừng khẩn cấp`;
            }
        }

        return {
            id: item.id,
            title: item.name || "",
            location: item.location || "",
            img: imgUrl,
            info: {
                time: "",
                progress: "",
                investment: "",
                lanes: item.drivingLaneNum || "",
                start: startName,
                end: endName,
            },
            intro: item.description || "",
            lanesInfo: lanesInfo,
            mapData: {
                origin: { lat: startLat, lng: startLng, name: startName },
                destination: { lat: endLat, lng: endLng, name: endName },
                waypoints: [],
                cameraLocations: [],
            },
        };
    });
}
function renderRouteCards(routeData) {
    const routeList = document.getElementById("routeList");
    if (!routeList || !routeData.length) return;

    routeList.innerHTML = routeData
        .map(
            (route, index) => `
    <div class="td-route__card${index === 0 ? " active" : ""}" data-index="${index}">
      <img src="${route.img}" alt="${route.title}" />
      <div class="td-route__info">
        <h4 title="${route.title}">${route.title}</h4>
        <p class="route-info__row gap-1" title="${route.location}">
          <svg class="icon icon-location inline" viewBox="0 0 11 14">
            <path d="M4.71045 13.7175C0.737461 7.95788 0 7.36676 0 5.25C0 2.3505 2.3505 0 5.25 0C8.1495 0 10.5 2.3505 10.5 5.25C10.5 7.36676 9.76254 7.95788 5.78955 13.7175C5.52882 14.0942 4.97115 14.0941 4.71045 13.7175ZM5.25 7.4375C6.45813 7.4375 7.4375 6.45813 7.4375 5.25C7.4375 4.04187 6.45813 3.0625 5.25 3.0625C4.04187 3.0625 3.0625 4.04187 3.0625 5.25C3.0625 6.45813 4.04187 7.4375 5.25 7.4375Z" />
          </svg>
          ${route.location}
        </p>
        <span class="route-info__row gap-1" title="${route.lanesInfo}">
          <svg class="icon icon-lane inline" viewBox="0 0 14 11">
            <path d="M13.125 6.125C13.125 3.93066 11.7775 2.05297 9.8659 1.2682L8.75 3.5V0.4375C8.75 0.321468 8.70391 0.210188 8.62186 0.128141C8.53981 0.0460937 8.42853 0 8.3125 0H5.6875C5.57147 0 5.46019 0.0460937 5.37814 0.128141C5.29609 0.210188 5.25 0.321468 5.25 0.4375V3.5L4.1341 1.2682C2.2225 2.05297 0.875 3.93066 0.875 6.125V7.875H13.125V6.125ZM13.5625 8.75H0.4375C0.321468 8.75 0.210188 8.79609 0.128141 8.87814C0.0460936 8.96019 0 9.07147 0 9.1875L0 10.0625C0 10.1785 0.0460936 10.2898 0.128141 10.3719C0.210188 10.4539 0.321468 10.5 0.4375 10.5H13.5625C13.6785 10.5 13.7898 10.4539 13.8719 10.3719C13.9539 10.2898 14 10.1785 14 10.0625V9.1875C14 9.07147 13.9539 8.96019 13.8719 8.87814C13.7898 8.79609 13.6785 8.75 13.5625 8.75Z" />
          </svg>
          ${route.lanesInfo || "Chưa có thông tin"}
        </span>
      </div>
    </div>
  `
        )
        .join("");

    attachRouteCardListeners(routeData);
}
function attachRouteCardListeners(routeDataArray) {
    const cards = document.querySelectorAll(".td-route__card");
    cards.forEach((card) => {
        card.addEventListener("click", () => {
            const index = parseInt(card.dataset.index, 10);
            cards.forEach((c) => c.classList.remove("active"));
            card.classList.add("active");
            const route = routeDataArray[index];
            if (route) {
                renderDetail(route);
                displayRoute(route);
            }
        });
    });
}
async function initializeApp() {
    const dateFilter = document.getElementById("dateFilter");
    if (dateFilter) {
        const today = new Date().toISOString().split("T")[0];
        dateFilter.value = today;
    }

    const apiItems = await fetchHighwaysData();
    ROUTE_DATA = mapApiDataToRouteData(apiItems);
    filteredRouteData = [...ROUTE_DATA];


    if (ROUTE_DATA.length > 0) {
        populateRouteSelect(ROUTE_DATA);
        renderRouteCards(ROUTE_DATA);
        renderDetail(ROUTE_DATA[0]);
        setupFilterListeners();

        if (map) {
            renderCameras(ROUTE_DATA[0].id);
            displayRoute(ROUTE_DATA[0]);
        }
    }
}
let filteredRouteData = [];

function populateRouteSelect(routes) {

    const routeSelect = document.getElementById("routeSelect");
    if (!routeSelect) return;

    routeSelect.innerHTML = '';

    routes.forEach((route, index) => {
        const option = document.createElement("option");
        option.value = index;
        option.textContent = route.title;
        routeSelect.appendChild(option);
    });
}

function filterRoutes() {

    const routeSelect = document.getElementById("routeSelect");

    const selectedRoute = routeSelect?.value || "";
    const searchTerm = String(window.searchTerm || "").toLowerCase();

    if (selectedRoute !== "") {
        const index = parseInt(selectedRoute, 10);
        if (index >= 0 && index < ROUTE_DATA.length) {
            const route = ROUTE_DATA[index];
            filteredRouteData = [route];
            renderRouteCards(filteredRouteData);
            selectRouteCard(0);
            renderDetail(route);
            displayRoute(route);
            renderCameras(route.id);
            return;
        }
    }

    if (searchTerm) {
        filteredRouteData = ROUTE_DATA.filter((route) => {
            const titleMatch = route.title.toLowerCase().includes(searchTerm);
            const locationMatch = route.location.toLowerCase().includes(searchTerm);
            const introMatch = (route.intro || "").toLowerCase().includes(searchTerm);
            return titleMatch || locationMatch || introMatch;
        });
    } else {
        filteredRouteData = [...ROUTE_DATA];
    }

    if (filteredRouteData.length > 0) {
        renderRouteCards(filteredRouteData);
        selectRouteCard(0);
        renderDetail(filteredRouteData[0]);
        displayRoute(filteredRouteData[0]);
    } else {
        const routeList = document.getElementById("routeList");
        if (routeList) {
            routeList.innerHTML = '<div class="p-4 text-gray-500">Không tìm thấy tuyến đường phù hợp</div>';
        }
    }
}

function selectRouteCard(index) {
    const cards = document.querySelectorAll(".td-route__card");
    cards.forEach((card, i) => {
        if (i === index) {
            card.classList.add("active");
        } else {
            card.classList.remove("active");
        }
    });
}

function setupFilterListeners() {
    const routeSelect = document.getElementById("routeSelect");
    const dateFilter = document.getElementById("dateFilter");

    if (routeSelect) {
        routeSelect.addEventListener("change", () => {
            filterRoutes();
        });
    }

    if (dateFilter) {
        dateFilter.addEventListener("change", () => {
            filterRoutes();
        });
    }
}
let map;
let directionsService;
let directionsRenderer;
let trafficLayer;
let markers = [];
let currentRoute = null;

function initMap() {
    const mapElement = document.getElementById("google-map");
    if (!mapElement) {
        console.error("Map element #google-map not found!");
        return;
    }

    map = new window.google.maps.Map(mapElement, {
        zoom: 10,
        center: { lat: 10.8231, lng: 106.6297 },
        mapTypeControl: false,
        streetViewControl: false,
        fullscreenControl: false,
        styles: [
            {
                featureType: "poi",
                stylers: [{ visibility: "off" }],
            },
        ],
    });

    trafficLayer = new window.google.maps.TrafficLayer();
    trafficLayer.setMap(map);

    directionsService = new window.google.maps.DirectionsService();
    directionsRenderer = new window.google.maps.DirectionsRenderer({
        map: map,
        suppressMarkers: true,
        polylineOptions: {
            strokeColor: "#460ffb",
            strokeWeight: 5,
            strokeOpacity: 0.8,
        },
    });

    if (ROUTE_DATA.length > 0) {
        displayRoute(ROUTE_DATA[0]);
    }
}

window.toggleTrafficLayer = function (show) {
    if (trafficLayer) {
        trafficLayer.setMap(show ? map : null);
    }
}

function displayRoute(routeData) {
    if (!routeData || !routeData.mapData || !map) return;

    currentRoute = routeData;

    const { origin, destination, waypoints } = routeData.mapData;

    const googleWaypoints = waypoints
        ? waypoints.map((wp) => ({
            location: new window.google.maps.LatLng(wp.lat, wp.lng),
            stopover: true,
        }))
        : [];

    const request = {
        origin: new window.google.maps.LatLng(origin.lat, origin.lng),
        destination: new window.google.maps.LatLng(destination.lat, destination.lng),
        waypoints: googleWaypoints,
        travelMode: window.google.maps.TravelMode.DRIVING,
        optimizeWaypoints: false,
    };

    directionsService.route(request, (result, status) => {
        if (status === "OK") {
            directionsRenderer.setDirections(result);

            setTimeout(() => {
                clearMarkers();
                displayMarkers(routeData);
            }, 300);
        } else {
            console.error("Directions request failed:", status);
            drawStraightLine(routeData);
        }
    });
}

function drawStraightLine(routeData) {
    const { origin, destination, waypoints } = routeData.mapData;

    const pathCoordinates = [origin, ...(waypoints || []), destination];

    new window.google.maps.Polyline({
        path: pathCoordinates,
        geodesic: true,
        strokeColor: "#E31C2A",
        strokeOpacity: 1.0,
        strokeWeight: 4,
        map: map,
    });

    const bounds = new window.google.maps.LatLngBounds();
    pathCoordinates.forEach((coord) => bounds.extend(coord));
    map.fitBounds(bounds);

    displayMarkers(routeData);
}

function isOptionChecked(optionId) {
    if (typeof dataOptions === "undefined") return true;
    const option = dataOptions.find((opt) => opt.id === optionId);
    return option ? option.checked : true;
}

function displayMarkers(routeData) {
    if (!routeData || !routeData.mapData) return;

    if (!map) {
        setTimeout(() => displayMarkers(routeData), 500);
        return;
    }

    const { cameraLocations } = routeData.mapData;
    const showCameras = isOptionChecked("camera");
    if (cameraLocations?.length) {
        cameraLocations.forEach((camera) => {
            if (!Number.isFinite(camera.lat) || !Number.isFinite(camera.lng)) return;

            const marker = new window.google.maps.Marker({
                position: { lat: camera.lat, lng: camera.lng },
                map: map,
                title: camera.name,
                visible: showCameras,
                icon: {
                    url: "https://res.cloudinary.com/drwairjk5/image/upload/v1767820122/Group_1000002467_wkbbsl.svg",
                    scaledSize: new window.google.maps.Size(40, 40),
                },
                zIndex: 1000,
            });

            const infoWindow = new window.google.maps.InfoWindow({
                content: `
          <div style="min-width:160px">
            <strong>📹 ${camera.name}</strong><br/>
            ${camera.id
                        ? `<button onclick="openCameraFromMap('${camera.id}')"
                    style="margin-top:6px;padding:6px 10px;background:#2563eb;color:white;border:none;border-radius:4px;cursor:pointer">
                    Xem trực tiếp
                  </button>`
                        : `<small>Không có camera</small>`
                    }
          </div>
        `,
            });

            marker.addListener("click", () => {
                closeAllInfoWindows();
                infoWindow.open(map, marker);
            });

            markers.push({ marker, infoWindow, type: "camera" });
        });
    }

    if (cameraLocations?.length && showCameras) {
        const bounds = new window.google.maps.LatLngBounds();
        markers.forEach(({ marker }) => {
            if (marker.getVisible()) {
                bounds.extend(marker.getPosition());
            }
        });
        if (routeData.mapData.origin) {
            bounds.extend(routeData.mapData.origin);
        }
        if (routeData.mapData.destination) {
            bounds.extend(routeData.mapData.destination);
        }
        map.fitBounds(bounds);
    }
}

function clearMarkers() {
    markers.forEach(({ marker }) => marker.setMap(null));
    markers = [];
}

function closeAllInfoWindows() {
    markers.forEach(({ infoWindow }) => infoWindow.close());
}

function toggleRoute(show) {
    if (directionsRenderer) {
        directionsRenderer.setMap(show ? map : null);
    }
}

function toggleCameras(show) {
    markers.forEach(({ marker, type }) => {
        if (type === "camera") {
            marker.setVisible(show);
        }
    });
}
const img = document.querySelector(".td-detail__img");
const title = document.querySelector(".td-detail h3");
const locationEl = document.querySelector(".td-detail__location");

function renderDetail(data) {
    img.src = data.img;
    title.textContent = data.title;
    locationEl.textContent = "📍 " + data.location;

    if (data.id) {
        renderCameras(data.id);
    }
}
(async () => {
    await initializeApp();

    if (window.__loadGoogleMaps__) {
        window.__loadGoogleMaps__();
    }
})();

const dataOptions = [
    {
        id: "route",
        title: "Hiển thị tuyến",
        checked: true,
        icon: `<svg width="20" class="text-gray-500" height="20" viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
    <g clip-path="url(#clip0_647_13613)">
      <path d="M8.40101 1.66602C4.55885 2.44593 1.66699 5.84283 1.66699 9.91516C1.66699 14.564 5.43565 18.3327 10.0845 18.3327C14.1568 18.3327 17.5537 15.4408 18.3337 11.5987" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
      <path
        d="M15.7813 15.0007C16.1013 14.9312 16.3976 14.8207 16.6667 14.6698M12.2396 14.4512C12.7359 14.6686 13.2147 14.8297 13.6709 14.9355M9.04551 12.4571C9.39007 12.699 9.75836 12.9891 10.1169 13.2394M2.5 11.5214C2.76861 11.3903 3.05859 11.2397 3.38542 11.1107M5.37587 10.834C5.84411 10.886 6.36917 11.0195 6.96453 11.27"
        stroke="currentColor"
        stroke-width="1.5"
        stroke-linecap="round"
        stroke-linejoin="round"
      />
      <path d="M15.417 5.83398C15.417 5.14363 14.8573 4.58398 14.167 4.58398C13.4766 4.58398 12.917 5.14363 12.917 5.83398C12.917 6.52434 13.4766 7.08398 14.167 7.08398C14.8573 7.08398 15.417 6.52434 15.417 5.83398Z" stroke="currentColor" stroke-width="1.5" />
      <path d="M14.1667 1.66602C16.4216 1.66602 18.3333 3.51446 18.3333 5.75699C18.3333 8.03523 16.3904 9.634 14.5958 10.7212C14.465 10.7943 14.3172 10.8327 14.1667 10.8327C14.0162 10.8327 13.8683 10.7943 13.7375 10.7212C11.9462 9.6234 10 8.04311 10 5.75699C10 3.51446 11.9118 1.66602 14.1667 1.66602Z" stroke="currentColor" stroke-width="1.5" />
    </g>
    <defs>
      <clipPath id="clip0_647_13613">
        <rect width="20" height="20" fill="white" />
      </clipPath>
    </defs>
  </svg>`,
    },
    {
        id: "camera",
        title: "Camera trực tuyến",
        checked: true,
        icon: `<svg class="w-5 h-5 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z"></path>
  </svg>`,
    },
];

function renderOptions() {
    const optionsContainer = document.getElementById("optionsContainer");
    if (!optionsContainer) return;

    optionsContainer.innerHTML = dataOptions
        .map((option) => {
            const isChecked = option.checked;
            const textColor = isChecked ? "text-blue-500" : "";
            const bgColor = isChecked ? "bg-blue-50" : "";
            const iconColor = isChecked ? "text-blue-500" : "text-gray-500";
            const checkedAttr = isChecked ? "checked" : "";

            return `
    <div class="mt-2">
      <label class="option-item flex items-center gap-3 rounded-lg cursor-pointer" data-option-id="${option.id}">
      <div class="w-8 h-8 ${bgColor} rounded-lg flex items-center justify-center">
        ${option.icon.replace("text-gray-500", iconColor)}
      </div>
      <span class="flex-1 text-sm ${textColor} ">${option.title}</span>
      <div class="custom-checkbox">
        <input type="checkbox" ${checkedAttr} />
      </div>
    </label></div>
  `;
        })
        .join("");
}

const settingsPanel = document.getElementById("settingsPanel");
const closeBtn = document.getElementById("closeBtn");
const openBtn = document.getElementById("openBtn");
settingsPanel.classList.add("hidden");
openBtn.classList.remove("hidden");

renderOptions();

closeBtn.addEventListener("click", () => {
    settingsPanel.classList.add("hidden");
    openBtn.classList.remove("hidden");
});

openBtn.addEventListener("click", () => {
    settingsPanel.classList.remove("hidden");
    openBtn.classList.add("hidden");
});

const mapLayerCards = document.querySelectorAll(".map-layer-card");
mapLayerCards.forEach((card) => {
    card.addEventListener("click", () => {
        mapLayerCards.forEach((c) => {
            c.classList.remove("active");
        });
        card.classList.add("active");

        const layer = card.dataset.layer;
        if (map) {
            if (layer === "street") {
                map.setMapTypeId(window.google.maps.MapTypeId.ROADMAP);
            } else if (layer === "satellite") {
                map.setMapTypeId(window.google.maps.MapTypeId.HYBRID);
            }
        }
    });
});

const optionsContainer = document.getElementById("optionsContainer");
optionsContainer.addEventListener("change", (e) => {
    if (e.target.type === "checkbox") {
        const label = e.target.closest("label");
        const optionId = label.dataset.optionId;
        const icon = label.querySelector(".w-8.h-8");
        const span = label.querySelector("span");

        const option = dataOptions.find((opt) => opt.id === optionId);
        if (option) {
            option.checked = e.target.checked;
        }

        const isChecked = e.target.checked;
        switch (optionId) {
            case "route":
                toggleRoute(isChecked);
                break;
            case "camera":
                toggleCameras(isChecked);
                break;
            default:
                break;
        }

        if (e.target.checked) {
            span.classList.add("text-blue-500");
            span.classList.remove("text-gray-700");
            icon.classList.remove("bg-gray-100");
            icon.classList.add("bg-blue-50");
            const svg = icon.querySelector("svg");
            if (svg) {
                svg.classList.remove("text-gray-500");
                svg.classList.add("text-blue-500");
            }
        } else {
            span.classList.remove("text-blue-500");
            span.classList.add("text-gray-700");
            icon.classList.add("bg-gray-100");
            icon.classList.remove("bg-blue-50");
            const svg = icon.querySelector("svg");
            if (svg) {
                svg.classList.add("text-gray-500");
                svg.classList.remove("text-blue-500");
            }
        }
    }
});
