/*
 * SHERIA CENTRIC — Web Push for device notifications (works when app is closed).
 */
(function () {
  "use strict";

  const state = {
    publicKey: null,
    subscription: null,
    supported: false,
    bannerShown: false,
  };

  function isSupported() {
    return (
      "serviceWorker" in navigator &&
      "PushManager" in window &&
      "Notification" in window
    );
  }

  function isEmployeeSession() {
    return !!(document.body && document.body.dataset.employeeId);
  }

  function canRegisterServiceWorker() {
    return (
      location.protocol === "https:" ||
      location.hostname === "localhost" ||
      location.hostname === "127.0.0.1"
    );
  }

  function urlBase64ToUint8Array(base64String) {
    const raw = String(base64String || "").trim().replace(/\s+/g, "");
    if (raw.length < 80) {
      throw new Error(
        "Push public key is invalid on the server. Ask IT Support to generate or import VAPID keys in System Settings → Notifications."
      );
    }
    const padding = "=".repeat((4 - (raw.length % 4)) % 4);
    const base64 = (raw + padding).replace(/-/g, "+").replace(/_/g, "/");
    let rawData;
    try {
      rawData = atob(base64);
    } catch (err) {
      throw new Error(
        "Push public key is invalid on the server. Ask IT Support to generate or import VAPID keys in System Settings → Notifications."
      );
    }
    if (rawData.length !== 65 || rawData.charCodeAt(0) !== 0x04) {
      throw new Error(
        "Push public key is invalid on the server. Ask IT Support to generate or import VAPID keys in System Settings → Notifications."
      );
    }
    const outputArray = new Uint8Array(rawData.length);
    for (let i = 0; i < rawData.length; ++i) {
      outputArray[i] = rawData.charCodeAt(i);
    }
    return outputArray;
  }

  async function fetchJson(url, options) {
    const opts = options || {};
    const headers = Object.assign({ Accept: "application/json" }, opts.headers || {});
    if (opts.body && !headers["Content-Type"]) {
      headers["Content-Type"] = "application/json";
    }
    const res = await fetch(url, {
      method: opts.method || "GET",
      credentials: "same-origin",
      headers: headers,
      body: opts.body,
    });
    const text = await res.text();
    let data = null;
    const contentType = (res.headers.get("content-type") || "").toLowerCase();
    if (text && (contentType.includes("json") || text.trim().startsWith("{"))) {
      try {
        data = JSON.parse(text);
      } catch (err) {
        data = null;
      }
    }
    if (!data) {
      const snippet = text.replace(/\s+/g, " ").trim().slice(0, 100);
      if (res.status === 401) {
        throw new Error("Your session expired. Refresh the page and log in again.");
      }
      if (snippet.toLowerCase().startsWith("<!doctype") || snippet.startsWith("<html")) {
        throw new Error("Server returned a web page instead of data. Refresh and try again.");
      }
      throw new Error(snippet || "Request failed (" + res.status + ")");
    }
    if (!res.ok) {
      throw new Error(data.error || "Request failed (" + res.status + ")");
    }
    return data;
  }

  async function fetchPublicKey() {
    const data = await fetchJson("/api/push/vapid-public-key");
    if (!data.success || !data.publicKey) {
      throw new Error(data.error || "Push is not configured on the server.");
    }
    state.publicKey = data.publicKey;
    return data.publicKey;
  }

  async function ensureServiceWorker() {
    if (!isSupported()) {
      throw new Error("This browser does not support push notifications.");
    }
    if (!canRegisterServiceWorker()) {
      throw new Error("Push notifications require HTTPS or localhost.");
    }
    let registration = await navigator.serviceWorker.getRegistration("/");
    if (!registration) {
      registration = await navigator.serviceWorker.register("/sw.js", { scope: "/" });
    }
    return navigator.serviceWorker.ready;
  }

  async function getRegistration() {
    return ensureServiceWorker();
  }

  async function getLocalEndpoint() {
    try {
      if (!isSupported() || !canRegisterServiceWorker()) return null;
      const reg = await navigator.serviceWorker.getRegistration("/");
      if (!reg) return null;
      const sub = await reg.pushManager.getSubscription();
      return sub ? sub.endpoint : null;
    } catch (err) {
      return null;
    }
  }

  async function getStatus() {
    const endpoint = await getLocalEndpoint();
    const qs = endpoint ? "?endpoint=" + encodeURIComponent(endpoint) : "";
    return fetchJson("/api/push/status" + qs);
  }

  async function saveSubscription(subscription) {
    const json = subscription.toJSON();
    const data = await fetchJson("/api/push/subscribe", {
      method: "POST",
      body: JSON.stringify(json),
    });
    if (!data.success) {
      throw new Error(data.error || "Could not save push subscription.");
    }
    state.subscription = subscription;
    return data;
  }

  async function removeSubscription(endpoint) {
    const data = await fetchJson("/api/push/unsubscribe", {
      method: "POST",
      body: JSON.stringify({ endpoint: endpoint || null }),
    });
    if (!data.success) {
      throw new Error(data.error || "Could not remove push subscription.");
    }
    state.subscription = null;
    return data;
  }

  async function subscribe() {
    if (!isSupported()) {
      throw new Error("This browser does not support push notifications.");
    }
    const permission = await Notification.requestPermission();
    if (permission !== "granted") {
      throw new Error("Notification permission was denied. Enable it in your browser or device settings.");
    }
    const publicKey = state.publicKey || (await fetchPublicKey());
    const reg = await getRegistration();
    let subscription = await reg.pushManager.getSubscription();
    if (!subscription) {
      subscription = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(publicKey),
      });
    }
    await saveSubscription(subscription);
    hideEnableBanner();
    return subscription;
  }

  async function unsubscribe() {
    const reg = await getRegistration();
    const subscription = await reg.pushManager.getSubscription();
    if (subscription) {
      const endpoint = subscription.endpoint;
      await subscription.unsubscribe();
      await removeSubscription(endpoint);
    } else {
      await removeSubscription(null);
    }
    hideEnableBanner();
  }

  async function setEnabled(enabled) {
    if (enabled) {
      await subscribe();
      return { enabled: true };
    }
    await unsubscribe();
    return { enabled: false };
  }

  async function sendTest() {
    return fetchJson("/api/push/test", {
      method: "POST",
      body: "{}",
    });
  }

  async function syncIfGranted() {
    if (!isSupported() || Notification.permission !== "granted") {
      return null;
    }
    try {
      const status = await getStatus();
      if (!status.success || !status.configured) {
        return null;
      }
      if (!status.session_enabled && !status.device_subscribed) {
        return null;
      }
      state.publicKey = state.publicKey || (await fetchPublicKey());
      const reg = await getRegistration();
      let subscription = await reg.pushManager.getSubscription();
      if (!subscription) {
        subscription = await reg.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: urlBase64ToUint8Array(state.publicKey),
        });
      }
      if (!status.device_subscribed) {
        await saveSubscription(subscription);
      }
      state.subscription = subscription;
      hideEnableBanner();
      return subscription;
    } catch (err) {
      console.warn("[push] sync skipped:", err);
      return null;
    }
  }

  function permissionLabel() {
    if (!isSupported()) return "unsupported";
    return Notification.permission;
  }

  function hideEnableBanner() {
    const banner = document.getElementById("push-enable-banner");
    if (banner) banner.remove();
    state.bannerShown = false;
  }

  async function maybeShowEnableBanner() {
    if (!isEmployeeSession() || !isSupported() || state.bannerShown) return;
    if (!canRegisterServiceWorker()) return;
    if (Notification.permission === "granted") return;
    if (sessionStorage.getItem("push-banner-dismissed") === "1") return;

    let status = null;
    try {
      status = await getStatus();
    } catch (err) {
      return;
    }
    if (!status || !status.success || !status.configured || status.enabled) return;

    const banner = document.createElement("div");
    banner.id = "push-enable-banner";
    banner.className = "fixed bottom-4 left-4 right-4 sm:left-auto sm:right-6 sm:max-w-md z-[130] rounded-2xl border border-indigo-200 bg-white shadow-2xl p-4 sm:p-5";
    banner.innerHTML = `
      <div class="flex items-start gap-3">
        <span class="inline-flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-xl bg-indigo-600 text-white">
          <i class="fas fa-bell"></i>
        </span>
        <div class="min-w-0 flex-1">
          <p class="text-sm font-bold text-gray-900">Enable device alerts</p>
          <p class="text-xs text-gray-600 mt-1 leading-relaxed">Get task and notification pop-ups on this browser — desktop, tablet, or phone — even when the app is closed.</p>
          <div class="mt-3 flex flex-wrap gap-2">
            <button type="button" id="push-enable-banner-btn" class="inline-flex items-center gap-2 px-3 py-2 rounded-lg bg-indigo-600 text-white text-xs font-semibold hover:bg-indigo-700 transition-colors">
              <i class="fas fa-bell"></i> Enable alerts
            </button>
            <button type="button" id="push-enable-banner-dismiss" class="inline-flex items-center px-3 py-2 rounded-lg border border-gray-200 text-gray-600 text-xs font-semibold hover:bg-gray-50 transition-colors">
              Not now
            </button>
          </div>
          <p id="push-enable-banner-error" class="hidden mt-2 text-xs font-medium text-red-600"></p>
        </div>
      </div>
    `;
    document.body.appendChild(banner);
    state.bannerShown = true;

    const enableBtn = banner.querySelector("#push-enable-banner-btn");
    const dismissBtn = banner.querySelector("#push-enable-banner-dismiss");
    const errorEl = banner.querySelector("#push-enable-banner-error");

    enableBtn.addEventListener("click", async function () {
      enableBtn.disabled = true;
      errorEl.classList.add("hidden");
      try {
        await setEnabled(true);
        const showAlert =
          window.SheriaLiveUpdates &&
          typeof window.SheriaLiveUpdates.showDeviceNotification === "function"
            ? window.SheriaLiveUpdates.showDeviceNotification
            : window.SheriaLiveUpdates && typeof window.SheriaLiveUpdates.showPhoneNotification === "function"
              ? window.SheriaLiveUpdates.showPhoneNotification
              : null;
        if (showAlert) {
          await showAlert(
            "Device alerts enabled",
            "You will now receive task and notification pop-ups on this device.",
            "/notifications"
          );
        }
      } catch (err) {
        errorEl.textContent = err.message || "Could not enable device alerts.";
        errorEl.classList.remove("hidden");
        enableBtn.disabled = false;
      }
    });

    dismissBtn.addEventListener("click", function () {
      sessionStorage.setItem("push-banner-dismissed", "1");
      hideEnableBanner();
    });
  }

  window.PushNotifications = {
    isSupported,
    permissionLabel,
    getStatus,
    subscribe,
    unsubscribe,
    setEnabled,
    sendTest,
    syncIfGranted,
    maybeShowEnableBanner,
    hideEnableBanner,
    ensureServiceWorker,
  };

  document.addEventListener("DOMContentLoaded", function () {
    state.supported = isSupported();
    if (!state.supported || !isEmployeeSession()) return;
    syncIfGranted().finally(function () {
      setTimeout(maybeShowEnableBanner, 2500);
    });
  });
})();
