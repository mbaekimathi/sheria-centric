/*
 * SHERIA CENTRIC — PWA bootstrap
 *
 * Responsibilities
 * ----------------
 * 1. Register the service worker (with full-site scope).
 * 2. Detect whether the app is already installed / running in standalone
 *    mode and, if so, hide every install prompt and remember it.
 * 3. Capture the browser's `beforeinstallprompt` event so we can show an
 *    in-page install card on our own schedule (Chrome / Edge / Android).
 * 4. Provide a graceful fallback "How to install" tip for Safari/iOS, where
 *    the platform never fires beforeinstallprompt.
 * 5. Expose a global `window.PWA.promptInstall()` so any button in the app
 *    (e.g. a Settings page or a CTA in the header) can trigger the prompt.
 */

(function () {
  "use strict";

  const STORAGE = {
    DISMISSED_UNTIL: "pwa.dismissUntil",
    INSTALLED: "pwa.installed"
  };

  /* Re-show the dismissed banner after this many days. */
  const DISMISS_DAYS = 7;

  /* Delay between page load and showing the auto-banner, in ms. Keeps the
     prompt from competing with the page's first paint. */
  const BANNER_DELAY_MS = 4500;

  const state = {
    deferredPrompt: null,
    isStandalone: false,
    isIOS: false,
    isInstalled: false,
    bannerEl: null
  };

  /* --------------------------------------------------------------------- *
   *  Detection helpers
   * --------------------------------------------------------------------- */

  function detectStandalone() {
    const mq = window.matchMedia && window.matchMedia("(display-mode: standalone)").matches;
    const iosStandalone = window.navigator.standalone === true;
    return Boolean(mq || iosStandalone);
  }

  function detectIOS() {
    const ua = window.navigator.userAgent || "";
    const isIOSUA = /iPad|iPhone|iPod/.test(ua) && !window.MSStream;
    const isIPadOS = ua.includes("Mac") && navigator.maxTouchPoints > 1;
    return isIOSUA || isIPadOS;
  }

  function readDismissUntil() {
    try {
      const v = parseInt(localStorage.getItem(STORAGE.DISMISSED_UNTIL) || "0", 10);
      return Number.isFinite(v) ? v : 0;
    } catch (e) {
      return 0;
    }
  }

  function setDismissUntil(ms) {
    try { localStorage.setItem(STORAGE.DISMISSED_UNTIL, String(ms)); } catch (e) {}
  }

  function markInstalled() {
    state.isInstalled = true;
    try { localStorage.setItem(STORAGE.INSTALLED, "1"); } catch (e) {}
  }

  function readInstalled() {
    try { return localStorage.getItem(STORAGE.INSTALLED) === "1"; } catch (e) { return false; }
  }

  /* --------------------------------------------------------------------- *
   *  Banner UI
   * --------------------------------------------------------------------- */

  function injectStyles() {
    if (document.getElementById("pwa-install-styles")) return;
    const css = `
      .pwa-install-banner {
        position: fixed;
        z-index: 2147483640;
        left: 50%;
        bottom: 22px;
        transform: translate(-50%, 24px);
        opacity: 0;
        transition: transform .35s ease, opacity .35s ease;
        max-width: 460px;
        width: calc(100% - 32px);
        background: linear-gradient(135deg, #1E1A4E 0%, #2c2480 50%, #6C5CE7 100%);
        color: #f5f3ff;
        border: 1px solid rgba(255,255,255,0.18);
        border-radius: 18px;
        padding: 14px 16px;
        box-shadow: 0 22px 50px rgba(8, 5, 30, 0.4);
        display: grid;
        grid-template-columns: 56px 1fr auto;
        gap: 12px;
        align-items: center;
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      }
      .pwa-install-banner.is-open {
        opacity: 1;
        transform: translate(-50%, 0);
      }
      .pwa-install-banner .pwa-icon {
        width: 56px;
        height: 56px;
        border-radius: 14px;
        background: rgba(255,255,255,0.1);
        border: 1px solid rgba(255,255,255,0.22);
        display: grid;
        place-items: center;
        overflow: hidden;
      }
      .pwa-install-banner .pwa-icon img {
        width: 44px;
        height: 44px;
      }
      .pwa-install-banner .pwa-copy {
        line-height: 1.35;
        min-width: 0;
      }
      .pwa-install-banner .pwa-title {
        font-weight: 700;
        font-size: 0.95rem;
        letter-spacing: -0.01em;
        display: block;
      }
      .pwa-install-banner .pwa-sub {
        font-size: 0.78rem;
        color: rgba(245, 243, 255, 0.78);
        display: block;
        margin-top: 2px;
      }
      .pwa-install-banner .pwa-actions {
        display: flex;
        align-items: center;
        gap: 8px;
      }
      .pwa-install-banner button {
        appearance: none;
        border: 0;
        cursor: pointer;
        font-family: inherit;
        font-weight: 600;
        font-size: 0.82rem;
        padding: 9px 14px;
        border-radius: 10px;
        transition: transform .15s ease, background .15s ease, border-color .15s ease;
        white-space: nowrap;
      }
      .pwa-install-banner .pwa-install {
        background: #ffffff;
        color: #1E1A4E;
      }
      .pwa-install-banner .pwa-install:hover {
        transform: translateY(-1px);
        background: #efeaff;
      }
      .pwa-install-banner .pwa-dismiss {
        background: transparent;
        color: #f5f3ff;
        border: 1px solid rgba(255,255,255,0.32);
        padding: 8px 10px;
      }
      .pwa-install-banner .pwa-dismiss:hover {
        border-color: rgba(255,255,255,0.65);
      }
      @media (max-width: 520px) {
        .pwa-install-banner {
          grid-template-columns: 44px 1fr;
          grid-template-rows: auto auto;
          padding: 12px 14px;
          gap: 10px 12px;
          bottom: 12px;
        }
        .pwa-install-banner .pwa-icon { width: 44px; height: 44px; border-radius: 12px; }
        .pwa-install-banner .pwa-icon img { width: 34px; height: 34px; }
        .pwa-install-banner .pwa-actions {
          grid-column: 1 / -1;
          justify-content: flex-end;
        }
      }

      .pwa-ios-tip {
        position: fixed;
        z-index: 2147483640;
        left: 50%;
        bottom: 18px;
        transform: translate(-50%, 24px);
        opacity: 0;
        transition: transform .35s ease, opacity .35s ease;
        max-width: 380px;
        width: calc(100% - 32px);
        background: rgba(20, 16, 60, 0.96);
        color: #f5f3ff;
        border: 1px solid rgba(255,255,255,0.18);
        border-radius: 16px;
        padding: 14px 16px 12px;
        box-shadow: 0 22px 50px rgba(8, 5, 30, 0.4);
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      }
      .pwa-ios-tip.is-open {
        opacity: 1;
        transform: translate(-50%, 0);
      }
      .pwa-ios-tip strong { color: #fff; }
      .pwa-ios-tip .pwa-ios-row {
        display: flex; align-items: center; gap: 10px; margin-top: 8px;
        font-size: 0.85rem; color: rgba(245,243,255,0.88);
      }
      .pwa-ios-tip svg { width: 18px; height: 18px; flex: 0 0 18px; }
      .pwa-ios-tip .pwa-ios-close {
        position: absolute; top: 8px; right: 10px;
        background: transparent; color: #f5f3ff;
        border: 0; cursor: pointer; font-size: 1.1rem; line-height: 1;
      }
    `;
    const style = document.createElement("style");
    style.id = "pwa-install-styles";
    style.appendChild(document.createTextNode(css));
    document.head.appendChild(style);
  }

  function buildBanner() {
    if (state.bannerEl) return state.bannerEl;
    injectStyles();

    const wrap = document.createElement("aside");
    wrap.className = "pwa-install-banner";
    wrap.setAttribute("role", "dialog");
    wrap.setAttribute("aria-live", "polite");
    wrap.setAttribute("aria-label", "Install SHERIA CENTRIC as an app");
    wrap.innerHTML = `
      <div class="pwa-icon" aria-hidden="true">
        <img src="/static/icon.svg" alt="">
      </div>
      <div class="pwa-copy">
        <span class="pwa-title">Install SHERIA CENTRIC</span>
        <span class="pwa-sub">Faster access, full-screen, works offline. One click to install.</span>
      </div>
      <div class="pwa-actions">
        <button type="button" class="pwa-dismiss" aria-label="Dismiss install prompt">Later</button>
        <button type="button" class="pwa-install" aria-label="Install the app now">Install</button>
      </div>
    `;
    wrap.querySelector(".pwa-install").addEventListener("click", () => {
      promptInstall();
    });
    wrap.querySelector(".pwa-dismiss").addEventListener("click", () => {
      dismissBanner(DISMISS_DAYS);
    });
    document.body.appendChild(wrap);
    state.bannerEl = wrap;
    return wrap;
  }

  function showBanner() {
    if (state.isStandalone || state.isInstalled) return;
    if (Date.now() < readDismissUntil()) return;
    if (!state.deferredPrompt) return;
    const el = buildBanner();
    requestAnimationFrame(() => el.classList.add("is-open"));
  }

  function hideBanner() {
    if (!state.bannerEl) return;
    state.bannerEl.classList.remove("is-open");
    setTimeout(() => {
      if (state.bannerEl && state.bannerEl.parentNode) {
        state.bannerEl.parentNode.removeChild(state.bannerEl);
      }
      state.bannerEl = null;
    }, 380);
  }

  function dismissBanner(days) {
    const until = Date.now() + days * 24 * 60 * 60 * 1000;
    setDismissUntil(until);
    hideBanner();
  }

  /* --------------------------------------------------------------------- *
   *  iOS / Safari fallback tip
   * --------------------------------------------------------------------- */

  function showIOSTipIfNeeded() {
    if (state.isStandalone || state.isInstalled) return;
    if (!state.isIOS) return;
    if (Date.now() < readDismissUntil()) return;
    injectStyles();

    const tip = document.createElement("aside");
    tip.className = "pwa-ios-tip";
    tip.setAttribute("role", "dialog");
    tip.setAttribute("aria-label", "How to install SHERIA CENTRIC on iOS");
    tip.innerHTML = `
      <button type="button" class="pwa-ios-close" aria-label="Dismiss">&times;</button>
      <div><strong>Install SHERIA CENTRIC</strong></div>
      <div class="pwa-ios-row">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M12 3v12"/><path d="M7 8l5-5 5 5"/><path d="M5 21h14"/>
        </svg>
        <span>Tap <strong>Share</strong> in Safari</span>
      </div>
      <div class="pwa-ios-row">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <rect x="3" y="3" width="18" height="18" rx="3"/><path d="M12 8v8"/><path d="M8 12h8"/>
        </svg>
        <span>Choose <strong>Add to Home Screen</strong></span>
      </div>
    `;
    tip.querySelector(".pwa-ios-close").addEventListener("click", () => {
      dismissBanner(DISMISS_DAYS);
      tip.classList.remove("is-open");
      setTimeout(() => tip.remove(), 380);
    });
    document.body.appendChild(tip);
    requestAnimationFrame(() => tip.classList.add("is-open"));

    state.bannerEl = tip;
  }

  /* --------------------------------------------------------------------- *
   *  Install action
   * --------------------------------------------------------------------- */

  async function promptInstall() {
    if (!state.deferredPrompt) {
      if (state.isIOS) {
        showIOSTipIfNeeded();
      }
      return;
    }
    const evt = state.deferredPrompt;
    state.deferredPrompt = null;
    try {
      evt.prompt();
      const choice = await evt.userChoice;
      if (choice && choice.outcome === "accepted") {
        markInstalled();
        hideBanner();
      } else {
        dismissBanner(1);
      }
    } catch (err) {
      console.warn("[PWA] install prompt error:", err);
    }
  }

  /* --------------------------------------------------------------------- *
   *  Service worker registration
   * --------------------------------------------------------------------- */

  function registerSW() {
    if (!("serviceWorker" in navigator)) return;
    if (location.protocol !== "https:" && location.hostname !== "localhost" && location.hostname !== "127.0.0.1") {
      return;
    }
    window.addEventListener("load", () => {
      navigator.serviceWorker
        .register("/sw.js", { scope: "/" })
        .catch((err) => console.warn("[PWA] SW registration failed:", err));
    });
  }

  /* --------------------------------------------------------------------- *
   *  Bootstrap
   * --------------------------------------------------------------------- */

  function init() {
    state.isStandalone = detectStandalone();
    state.isIOS = detectIOS();
    state.isInstalled = readInstalled() || state.isStandalone;

    registerSW();

    window.addEventListener("beforeinstallprompt", (e) => {
      e.preventDefault();
      state.deferredPrompt = e;
      setTimeout(showBanner, BANNER_DELAY_MS);
    });

    window.addEventListener("appinstalled", () => {
      markInstalled();
      hideBanner();
      state.deferredPrompt = null;
    });

    if (state.isIOS && !state.isStandalone && !state.isInstalled) {
      setTimeout(showIOSTipIfNeeded, BANNER_DELAY_MS);
    }
  }

  window.PWA = {
    promptInstall,
    isStandalone: () => state.isStandalone,
    isInstalled: () => state.isInstalled,
    isIOS: () => state.isIOS
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
