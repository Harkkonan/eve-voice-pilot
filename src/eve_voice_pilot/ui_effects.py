from __future__ import annotations


PLEX_BUTTON_EFFECT_CSS = r"""
    .plex-petal-layer {
      position: fixed;
      inset: 0;
      overflow: visible;
      pointer-events: none;
      z-index: 2147483000;
      contain: layout style paint;
    }
    .plex-petal {
      position: absolute;
      left: var(--plex-x);
      top: var(--plex-y);
      width: var(--plex-size);
      height: calc(var(--plex-size) * .62);
      display: grid;
      place-items: center;
      border: 1px solid rgba(255, 226, 122, .86);
      border-radius: 72% 31% 68% 32% / 54% 62% 38% 46%;
      background:
        radial-gradient(circle at 36% 28%, rgba(255, 249, 190, .96) 0 18%, transparent 34%),
        linear-gradient(135deg, #ffe082 0%, #f2b443 42%, #d97824 100%);
      box-shadow:
        inset 0 1px 0 rgba(255, 255, 255, .45),
        inset 0 -6px 12px rgba(122, 57, 10, .24),
        0 0 16px rgba(255, 174, 47, .32),
        0 8px 18px rgba(0, 0, 0, .22);
      color: #321904;
      font: 900 calc(var(--plex-size) * .22) / 1 "Segoe UI", system-ui, sans-serif;
      letter-spacing: .05em;
      text-shadow: 0 1px 0 rgba(255, 242, 180, .58);
      user-select: none;
      will-change: transform, opacity;
      animation: plex-petal-fall var(--plex-duration) cubic-bezier(.12, .72, .28, 1) var(--plex-delay) forwards;
    }
    .plex-petal::after {
      content: "";
      position: absolute;
      inset: 17% 13% auto auto;
      width: 30%;
      height: 22%;
      border-radius: 50%;
      background: rgba(255, 244, 185, .52);
      transform: rotate(-16deg);
    }
    .plex-petal:nth-child(2n) {
      border-radius: 36% 67% 34% 66% / 60% 47% 53% 40%;
    }
    .managed-document-duck-layer {
      position: fixed;
      inset: 0;
      overflow: hidden;
      pointer-events: none;
      z-index: 2147482998;
      contain: layout style paint;
    }
    .managed-document-duck-scene {
      position: absolute;
      left: var(--duck-x, 18vw);
      top: var(--duck-y, 52vh);
      width: 126px;
      height: 70px;
      transform: translate(-50%, -50%);
      opacity: 0;
      will-change: transform, opacity;
      animation: managed-document-duck-swim var(--duck-loop, 18s) ease-in-out infinite;
    }
    .managed-document-duck {
      position: absolute;
      left: 30px;
      top: 19px;
      width: 54px;
      height: 31px;
      border-radius: 58% 48% 48% 58% / 62% 62% 42% 42%;
      background:
        radial-gradient(circle at 33% 29%, rgba(255, 255, 234, .95) 0 13%, transparent 16%),
        linear-gradient(135deg, #ffe27c 0%, #f5b844 78%);
      box-shadow:
        inset 0 2px 0 rgba(255, 255, 255, .42),
        inset 0 -7px 10px rgba(142, 84, 5, .22),
        0 6px 14px rgba(0, 0, 0, .28);
      animation: managed-document-duck-bob 1.35s ease-in-out infinite;
    }
    .managed-document-duck::before {
      content: "";
      position: absolute;
      right: -9px;
      top: -17px;
      width: 27px;
      height: 27px;
      border-radius: 50%;
      background:
        radial-gradient(circle at 66% 38%, #201104 0 8%, transparent 10%),
        linear-gradient(135deg, #ffe98c 0%, #f6c24f 88%);
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, .48);
    }
    .managed-document-duck::after {
      content: "";
      position: absolute;
      right: -27px;
      top: -6px;
      width: 24px;
      height: 11px;
      border-radius: 62% 38% 54% 46%;
      background: linear-gradient(90deg, #ff9d31, #e76f1d);
      transform: rotate(4deg);
      box-shadow: inset 0 1px 0 rgba(255, 235, 170, .48);
    }
    .managed-document-duck-water {
      position: absolute;
      left: 14px;
      right: 7px;
      bottom: 3px;
      height: 22px;
      border-radius: 50%;
      background:
        radial-gradient(ellipse at 22% 62%, rgba(97, 199, 217, .72) 0 8%, transparent 10%),
        radial-gradient(ellipse at 44% 66%, rgba(97, 199, 217, .56) 0 9%, transparent 11%),
        radial-gradient(ellipse at 67% 63%, rgba(224, 168, 74, .42) 0 8%, transparent 10%),
        linear-gradient(90deg, rgba(97, 199, 217, .26), rgba(224, 168, 74, .18), rgba(97, 199, 217, .18));
      filter: drop-shadow(0 2px 7px rgba(0, 0, 0, .18));
      transform-origin: center;
      animation: managed-document-duck-ripple 1.8s ease-in-out infinite;
    }
    .managed-document-duck-splash {
      position: absolute;
      left: var(--splash-x);
      top: var(--splash-y);
      width: var(--splash-size);
      height: calc(var(--splash-size) * .62);
      display: grid;
      place-items: center;
      border: 1px solid rgba(255, 226, 122, .8);
      border-radius: 72% 31% 68% 32% / 54% 62% 38% 46%;
      background:
        radial-gradient(circle at 34% 28%, rgba(255, 252, 204, .9) 0 18%, transparent 34%),
        linear-gradient(135deg, #ffe082 0%, #f2b443 48%, #d97824 100%);
      color: #321904;
      font: 900 calc(var(--splash-size) * .22) / 1 "Segoe UI", system-ui, sans-serif;
      letter-spacing: .04em;
      text-shadow: 0 1px 0 rgba(255, 242, 180, .58);
      opacity: 0;
      will-change: transform, opacity;
      animation: managed-document-duck-splash var(--duck-loop, 18s) ease-out infinite;
      animation-delay: var(--splash-delay);
    }
    @keyframes plex-petal-fall {
      0% {
        opacity: 0;
        transform: translate(-50%, -58%) rotate(var(--plex-start-rot)) scale(.62);
      }
      12% {
        opacity: 1;
      }
      70% {
        opacity: .96;
      }
      100% {
        opacity: 0;
        transform:
          translate(calc(-50% + var(--plex-drift)), calc(-58% + var(--plex-drop)))
          rotate(var(--plex-rot))
          scale(.88);
      }
    }
    @keyframes managed-document-duck-swim {
      0% {
        opacity: 0;
        transform: translate(-58vw, -64%) rotate(-6deg);
      }
      6% {
        opacity: 1;
        transform: translate(-50%, -84%) rotate(-4deg);
      }
      11% {
        transform: translate(-42%, -50%) rotate(0deg);
      }
      34% {
        transform: translate(12vw, -62%) rotate(3deg);
      }
      58% {
        transform: translate(36vw, -43%) rotate(-2deg);
      }
      82% {
        opacity: 1;
        transform: translate(9vw, -67%) rotate(2deg);
      }
      100% {
        opacity: 0;
        transform: translate(65vw, -56%) rotate(5deg);
      }
    }
    @keyframes managed-document-duck-bob {
      0%, 100% { transform: translateY(0) rotate(-1deg); }
      50% { transform: translateY(-5px) rotate(2deg); }
    }
    @keyframes managed-document-duck-ripple {
      0%, 100% { opacity: .86; transform: scaleX(.9); }
      50% { opacity: .56; transform: scaleX(1.08); }
    }
    @keyframes managed-document-duck-splash {
      0%, 7% {
        opacity: 0;
        transform: translate(0, 0) rotate(-18deg) scale(.55);
      }
      13% {
        opacity: .94;
      }
      24% {
        opacity: 0;
        transform: translate(var(--splash-drift), -34px) rotate(var(--splash-rot)) scale(.86);
      }
      100% {
        opacity: 0;
        transform: translate(var(--splash-drift), -34px) rotate(var(--splash-rot)) scale(.86);
      }
    }
    @media (prefers-reduced-motion: reduce) {
      .plex-petal,
      .managed-document-duck-scene,
      .managed-document-duck,
      .managed-document-duck-water,
      .managed-document-duck-splash {
        display: none;
        animation: none;
      }
    }
"""


PLEX_BUTTON_EFFECT_SCRIPT = r"""
    (() => {
      const PLEX_BUTTON_SELECTOR = "button, .button-link, .actions a[href]";
      const MANAGED_DOCUMENT_CHANGE_EVENT = "eve-managed-document-change";
      const MANAGED_DOCUMENT_WATCH_SELECTOR = "[data-managed-document-watch]";
      const MANAGED_DOCUMENT_DUCK_DURATION_MS = 5 * 60 * 1000;
      const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
      let activePlexPetals = 0;
      let activeDocumentDuck = null;
      let activeDocumentDuckTimer = 0;
      const perfParams = new URLSearchParams(window.location.search);
      if (["1", "true", "yes"].includes(String(perfParams.get("ui_perf") || "").toLowerCase())) {
        window.localStorage?.setItem("eveVoiceUiPerf", "1");
      } else if (["0", "false", "no"].includes(String(perfParams.get("ui_perf") || "").toLowerCase())) {
        window.localStorage?.removeItem("eveVoiceUiPerf");
      }
      const reportUiPerf = (kind, payload = {}) => {
        if (window.localStorage?.getItem("eveVoiceUiPerf") !== "1") return;
        const body = JSON.stringify({
          kind,
          path: window.location.pathname,
          hash: window.location.hash,
          tab: document.body?.dataset?.activeTab || "",
          now_ms: performance.now(),
          ...payload,
        });
        window.fetch("/api/ui-performance", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body,
          keepalive: true,
        }).catch(() => {});
      };

      window.addEventListener("load", () => {
        if (window.localStorage?.getItem("eveVoiceUiPerf") !== "1") return;
        window.setTimeout(() => {
          const nav = performance.getEntriesByType("navigation")[0];
          reportUiPerf("page-load", {
            dom_content_loaded_ms: nav ? nav.domContentLoadedEventEnd : 0,
            load_ms: nav ? nav.loadEventEnd : 0,
            response_end_ms: nav ? nav.responseEnd : 0,
            transfer_size_bytes: nav ? nav.transferSize : 0,
            decoded_body_size_bytes: nav ? nav.decodedBodySize : 0,
            html_chars: document.documentElement?.outerHTML?.length || 0,
          });
        }, 0);
      }, { once: true });

      function plexPetalLayer() {
        let layer = document.querySelector(".plex-petal-layer");
        const created = !layer;
        if (!layer) {
          layer = document.createElement("div");
          layer.className = "plex-petal-layer";
          layer.setAttribute("aria-hidden", "true");
          document.body.appendChild(layer);
        }
        return { layer, created };
      }

      function managedDocumentDuckLayer() {
        let layer = document.querySelector(".managed-document-duck-layer");
        const created = !layer;
        if (!layer) {
          layer = document.createElement("div");
          layer.className = "managed-document-duck-layer";
          layer.setAttribute("aria-hidden", "true");
          document.body.appendChild(layer);
        }
        return { layer, created };
      }

      function canCelebratePlexPress(button) {
        if (!button || reducedMotion.matches || activePlexPetals > 84) return false;
        if (button.matches("[data-no-plex], [aria-disabled='true']")) return false;
        if (button.closest("[data-no-plex]")) return false;
        if ("disabled" in button && button.disabled) return false;
        return true;
      }

      function managedDocumentDuckTarget(detail = {}) {
        const selector = String(detail.targetSelector || detail.selector || "").trim();
        let target = selector ? document.querySelector(selector) : null;
        if (!target && detail.target instanceof Element) target = detail.target;
        if (!target) target = document.querySelector("[data-managed-document]");
        if (!target) target = document.querySelector(".spreadsheet-report-panel:not([hidden])");
        if (!target) target = document.querySelector(".decision-output:not(:empty)");
        const rect = target?.getBoundingClientRect?.();
        const viewportWidth = Math.max(1, window.innerWidth || document.documentElement.clientWidth || 1);
        const viewportHeight = Math.max(1, window.innerHeight || document.documentElement.clientHeight || 1);
        const x = rect && rect.width ? Math.max(80, Math.min(viewportWidth - 80, rect.left + rect.width * .28)) : viewportWidth * .22;
        const y = rect && rect.height ? Math.max(86, Math.min(viewportHeight - 64, rect.top + Math.min(rect.height * .42, 280))) : viewportHeight * .55;
        return { x, y, target_name: target?.id || target?.dataset?.managedDocument || target?.className || "viewport" };
      }

      function stopManagedDocumentDuck() {
        window.clearTimeout(activeDocumentDuckTimer);
        activeDocumentDuckTimer = 0;
        if (activeDocumentDuck) {
          activeDocumentDuck.remove();
          activeDocumentDuck = null;
        }
        const layer = document.querySelector(".managed-document-duck-layer");
        if (layer && !layer.childElementCount) layer.remove();
      }

      function startManagedDocumentDuck(detail = {}) {
        const startedAt = performance.now();
        if (reducedMotion.matches) return null;
        const target = managedDocumentDuckTarget(detail);
        const layerResult = managedDocumentDuckLayer();
        const layer = layerResult.layer;
        if (activeDocumentDuck) {
          activeDocumentDuck.style.setProperty("--duck-x", `${target.x}px`);
          activeDocumentDuck.style.setProperty("--duck-y", `${target.y}px`);
          window.clearTimeout(activeDocumentDuckTimer);
          activeDocumentDuckTimer = window.setTimeout(stopManagedDocumentDuck, MANAGED_DOCUMENT_DUCK_DURATION_MS);
          return { reused: true, duration_ms: MANAGED_DOCUMENT_DUCK_DURATION_MS, target_name: target.target_name };
        }
        const scene = document.createElement("div");
        scene.className = "managed-document-duck-scene";
        scene.style.setProperty("--duck-x", `${target.x}px`);
        scene.style.setProperty("--duck-y", `${target.y}px`);
        scene.style.setProperty("--duck-loop", "18s");
        scene.innerHTML = `
          <span class="managed-document-duck-water"></span>
          <span class="managed-document-duck"></span>
        `;
        const splashCount = 8;
        for (let index = 0; index < splashCount; index += 1) {
          const splash = document.createElement("span");
          splash.className = "managed-document-duck-splash";
          splash.textContent = "PLEX";
          splash.style.setProperty("--splash-x", `${18 + Math.random() * 84}px`);
          splash.style.setProperty("--splash-y", `${34 + Math.random() * 24}px`);
          splash.style.setProperty("--splash-size", `${18 + Math.random() * 9}px`);
          splash.style.setProperty("--splash-delay", `${(index * 1.75) % 15}s`);
          splash.style.setProperty("--splash-drift", `${-48 + Math.random() * 96}px`);
          splash.style.setProperty("--splash-rot", `${-90 + Math.random() * 180}deg`);
          scene.appendChild(splash);
        }
        layer.appendChild(scene);
        activeDocumentDuck = scene;
        activeDocumentDuckTimer = window.setTimeout(stopManagedDocumentDuck, MANAGED_DOCUMENT_DUCK_DURATION_MS);
        return {
          reused: false,
          layer_created: layerResult.created,
          splash_count: splashCount,
          duration_ms: MANAGED_DOCUMENT_DUCK_DURATION_MS,
          target_name: target.target_name,
          create_ms: performance.now() - startedAt,
        };
      }

      function spawnPlexPetals(button) {
        const startedAt = performance.now();
        if (!canCelebratePlexPress(button)) return null;
        const rect = button.getBoundingClientRect();
        if (!rect.width || !rect.height) return null;

        const layerResult = plexPetalLayer();
        const layer = layerResult.layer;
        const count = Math.min(14, Math.max(7, Math.round(rect.width / 32)));
        let firstPetalMs = 0;
        for (let index = 0; index < count; index += 1) {
          const petal = document.createElement("span");
          const startX = rect.left + rect.width * (.18 + Math.random() * .64);
          const startY = rect.top + rect.height * (.5 + Math.random() * .22);
          const size = 28 + Math.random() * 14;
          petal.className = "plex-petal";
          petal.textContent = "PLEX";
          petal.style.setProperty("--plex-x", `${startX}px`);
          petal.style.setProperty("--plex-y", `${startY}px`);
          petal.style.setProperty("--plex-size", `${size}px`);
          petal.style.setProperty("--plex-drift", `${(Math.random() - .5) * 132}px`);
          petal.style.setProperty("--plex-drop", `${96 + Math.random() * 142}px`);
          petal.style.setProperty("--plex-start-rot", `${-28 + Math.random() * 56}deg`);
          petal.style.setProperty("--plex-rot", `${-150 + Math.random() * 300}deg`);
          petal.style.setProperty("--plex-duration", `${880 + Math.random() * 620}ms`);
          petal.style.setProperty("--plex-delay", `${Math.random() * 75}ms`);
          activePlexPetals += 1;
          petal.addEventListener("animationend", () => {
            activePlexPetals = Math.max(0, activePlexPetals - 1);
            petal.remove();
            if (activePlexPetals === 0 && layer.childElementCount === 0) layer.remove();
            if (index === count - 1) {
              reportUiPerf("plex-cleanup", { petal_count: count, active_petals: activePlexPetals });
            }
          }, { once: true });
          layer.appendChild(petal);
          if (!firstPetalMs) firstPetalMs = performance.now() - startedAt;
        }
        return {
          count,
          layer_created: layerResult.created,
          first_petal_ms: firstPetalMs,
          create_ms: performance.now() - startedAt,
        };
      }

      document.addEventListener("click", (event) => {
        if (event.isTrusted === false) return;
        const target = event.target instanceof Element ? event.target : event.target?.parentElement;
        const button = target?.closest(PLEX_BUTTON_SELECTOR);
        if (!button) return;
        const clickStartedAt = performance.now();
        const result = spawnPlexPetals(button);
        if (!result) return;
        reportUiPerf("plex-click", {
          button_text: String(button.textContent || "").trim().slice(0, 80),
          handler_ms: performance.now() - clickStartedAt,
          ...result,
        });
      }, { capture: true });

      window.addEventListener(MANAGED_DOCUMENT_CHANGE_EVENT, (event) => {
        const result = startManagedDocumentDuck(event.detail || {});
        if (!result) return;
        reportUiPerf("managed-document-duck", result);
      });

      window.eveVoiceManagedDocumentChanged = (detail = {}) => {
        const result = startManagedDocumentDuck(detail);
        if (result) reportUiPerf("managed-document-duck", result);
        return result;
      };

      const watchedDocuments = new WeakSet();
      function installManagedDocumentWatch(root = document) {
        root.querySelectorAll?.(MANAGED_DOCUMENT_WATCH_SELECTOR).forEach((node) => {
          if (watchedDocuments.has(node)) return;
          watchedDocuments.add(node);
          let pending = false;
          const observer = new MutationObserver(() => {
            if (pending) return;
            pending = true;
            window.requestAnimationFrame(() => {
              pending = false;
              const result = startManagedDocumentDuck({ target: node });
              if (result) reportUiPerf("managed-document-duck", { ...result, observer: true });
            });
          });
          observer.observe(node, { childList: true, subtree: true, characterData: true });
        });
      }
      installManagedDocumentWatch();
    })();
"""


def inject_plex_button_effect(markup: str) -> str:
    """Add the shared decorative PLEX button effect to one rendered HTML page."""
    if "plex-petal-layer" in markup:
        return markup
    with_style = markup.replace("  </style>", f"{PLEX_BUTTON_EFFECT_CSS}\n  </style>", 1)
    return with_style.replace("  <script>", f"  <script>\n{PLEX_BUTTON_EFFECT_SCRIPT}\n", 1)
