/*
 * SHERIA CENTRIC — Web Push for phone notifications (works when app is closed).
 */
(function () {
  "use strict";

  const state = {
    publicKey: null,
    subscription: null,
    supported: false,
  };

  function isSupported() {
    return (
      "serviceWorker" in navigator &&
      "PushManager" in window &&
      "Notification" in window
    );
  }

  function urlBase64ToUint8Array(base64String) {
    const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
    const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
    const rawData = atob(base64);
    const outputArray = new Uint8Array(rawData.length);
    for (let i = 0; i < rawData.length; ++i) {
      outputArray[i] = rawData.charCodeAt(i);
    }
    return outputArray;
  }

  async function fetchPublicKey() {
    const res = await fetch("/api/push/vapid-public-key", { credentials: "same-origin" });
    const data = await res.json();
    if (!data.success || !data.publicKey) {
      throw new Error(data.error || "Push is not configured on the server.");
    }
    state.publicKey = data.publicKey;
    return data.publicKey;
  }

  async function getRegistration() {
    const reg = await navigator.serviceWorker.ready;
    return reg;
  }

  async function getStatus() {
    const res = await fetch("/api/push/status", { credentials: "same-origin" });
    return res.json();
  }

  async function saveSubscription(subscription) {
    const json = subscription.toJSON();
    const res = await fetch("/api/push/subscribe", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(json),
    });
    const data = await res.json();
    if (!data.success) {
      throw new Error(data.error || "Could not save push subscription.");
    }
    state.subscription = subscription;
    return data;
  }

  async function removeSubscription(endpoint) {
    const res = await fetch("/api/push/unsubscribe", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ endpoint: endpoint || null }),
    });
    const data = await res.json();
    if (!data.success) {
      throw new Error(data.error || "Could not remove push subscription.");
    }
    state.subscription = null;
    return data;
  }

  async function subscribe() {
    if (!isSupported()) {
      throw new Error("This browser does not support phone push notifications.");
    }
    const permission = await Notification.requestPermission();
    if (permission !== "granted") {
      throw new Error("Notification permission was denied. Enable it in browser settings.");
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
  }

  async function sendTest() {
    const res = await fetch("/api/push/test", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    const data = await res.json();
    if (!data.success) {
      throw new Error(data.error || "Test notification failed.");
    }
    return data;
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
      state.publicKey = state.publicKey || (await fetchPublicKey());
      const reg = await getRegistration();
      let subscription = await reg.pushManager.getSubscription();
      if (!subscription) {
        subscription = await reg.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: urlBase64ToUint8Array(state.publicKey),
        });
      }
      if (!status.subscribed) {
        await saveSubscription(subscription);
      }
      state.subscription = subscription;
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

  window.PushNotifications = {
    isSupported,
    permissionLabel,
    getStatus,
    subscribe,
    unsubscribe,
    sendTest,
    syncIfGranted,
  };

  document.addEventListener("DOMContentLoaded", function () {
    state.supported = isSupported();
    if (!state.supported) return;
    syncIfGranted();
  });
})();
