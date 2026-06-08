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
    @media (prefers-reduced-motion: reduce) {
      .plex-petal {
        display: none;
        animation: none;
      }
    }
"""


PLEX_BUTTON_EFFECT_SCRIPT = r"""
    (() => {
      const PLEX_BUTTON_SELECTOR = "button, .button-link, .actions a[href]";
      const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
      let activePlexPetals = 0;

      function plexPetalLayer() {
        let layer = document.querySelector(".plex-petal-layer");
        if (!layer) {
          layer = document.createElement("div");
          layer.className = "plex-petal-layer";
          layer.setAttribute("aria-hidden", "true");
          document.body.appendChild(layer);
        }
        return layer;
      }

      function canCelebratePlexPress(button) {
        if (!button || reducedMotion.matches || activePlexPetals > 84) return false;
        if (button.matches("[data-no-plex], [aria-disabled='true']")) return false;
        if (button.closest("[data-no-plex]")) return false;
        if ("disabled" in button && button.disabled) return false;
        return true;
      }

      function spawnPlexPetals(button) {
        if (!canCelebratePlexPress(button)) return;
        const rect = button.getBoundingClientRect();
        if (!rect.width || !rect.height) return;

        const layer = plexPetalLayer();
        const count = Math.min(14, Math.max(7, Math.round(rect.width / 32)));
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
          }, { once: true });
          layer.appendChild(petal);
        }
      }

      document.addEventListener("click", (event) => {
        if (event.isTrusted === false) return;
        const target = event.target instanceof Element ? event.target : event.target?.parentElement;
        const button = target?.closest(PLEX_BUTTON_SELECTOR);
        if (!button) return;
        spawnPlexPetals(button);
      }, { capture: true });
    })();
"""


def inject_plex_button_effect(markup: str) -> str:
    """Add the shared decorative PLEX button effect to one rendered HTML page."""
    if "plex-petal-layer" in markup:
        return markup
    with_style = markup.replace("  </style>", f"{PLEX_BUTTON_EFFECT_CSS}\n  </style>", 1)
    return with_style.replace("  <script>", f"  <script>\n{PLEX_BUTTON_EFFECT_SCRIPT}\n", 1)
