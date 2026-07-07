(function () {
    const POLL_MS = 15000;
    let pollTimer = null;
    let lastBadgeKey = '';
    let lastMyTasksKey = '';
    let lastNotificationsKey = '';
    let prevNotificationCount = null;

    function livePage() {
        return (document.body && document.body.dataset.livePage) || '';
    }

    async function fetchJson(url) {
        const response = await fetch(url, {
            headers: { Accept: 'application/json' },
            credentials: 'same-origin',
        });
        return response.json();
    }

    function setBadgeCount(el, count) {
        if (!el) return;
        const value = Math.max(0, parseInt(count, 10) || 0);
        if (value > 0) {
            el.textContent = value;
            el.classList.remove('hidden');
        } else {
            el.textContent = '0';
            el.classList.add('hidden');
        }
    }

    function updateBadgeCounts(data) {
        const notificationBadges = document.querySelectorAll('[data-live-badge="notifications"]');
        const myTaskBadges = document.querySelectorAll('[data-live-badge="my-tasks"]');
        notificationBadges.forEach((el) => setBadgeCount(el, data.notification_badge_count));
        myTaskBadges.forEach((el) => setBadgeCount(el, data.my_task_badge_count));
    }

    async function showDeviceNotification(title, body, url) {
        if (!('Notification' in window) || Notification.permission !== 'granted') {
            return;
        }

        const options = {
            body: body || 'Open the app to review your updates.',
            icon: '/static/icon-192.png',
            badge: '/static/icon-192.png',
            tag: 'sheria-workspace-alert',
            renotify: true,
            vibrate: [120, 60, 120],
            data: { url: url || '/notifications' },
        };

        try {
            if ('serviceWorker' in navigator) {
                const reg = await navigator.serviceWorker.ready;
                if (reg && reg.showNotification) {
                    await reg.showNotification(title || 'SHERIA CENTRIC', options);
                    return;
                }
            }
            const note = new Notification(title || 'SHERIA CENTRIC', options);
            note.onclick = function () {
                window.focus();
                window.location.href = options.data.url;
            };
        } catch (err) {
            console.debug('Local notification failed:', err);
        }
    }

    async function maybeAlertOnNewItems(previousCount, nextCount) {
        if (previousCount === null || nextCount <= previousCount) {
            return;
        }
        try {
            const data = await fetchJson('/api/notifications');
            if (!data.success || !data.notifications || !data.notifications.length) {
                await showDeviceNotification(
                    'SHERIA CENTRIC',
                    `You have ${nextCount} workspace update${nextCount === 1 ? '' : 's'}.`,
                    '/notifications'
                );
                return;
            }
            const lead = data.notifications[0];
            await showDeviceNotification(
                lead.title || 'Workspace update',
                lead.meta || lead.subtitle || 'Open the app to review.',
                lead.link || '/notifications'
            );
        } catch (err) {
            console.debug('Alert on new items failed:', err);
        }
    }

    async function refreshBadges() {
        try {
            const data = await fetchJson('/api/employee/badge-counts');
            if (!data.success) return;

            const nextCount = parseInt(data.notification_badge_count, 10) || 0;
            const key = [
                data.my_task_badge_count,
                data.notification_badge_count,
            ].join('|');

            if (key !== lastBadgeKey) {
                await maybeAlertOnNewItems(prevNotificationCount, nextCount);
                prevNotificationCount = nextCount;
                lastBadgeKey = key;
                updateBadgeCounts(data);
            }
        } catch (err) {
            console.debug('Live badge refresh failed:', err);
        }
    }

    async function refreshMyTasksPage() {
        if (typeof window.SheriaMyTasksRefresh !== 'function') return;
        try {
            const data = await fetchJson('/api/my_tasks');
            if (!data.success) return;
            const key = (data.tasks || []).map((task) => {
                const id = task.is_session ? `s${task.id}` : task.id;
                return `${id}:${task.task_status}`;
            }).join('|');
            if (key === lastMyTasksKey) return;
            lastMyTasksKey = key;
            window.SheriaMyTasksRefresh(data);
        } catch (err) {
            console.debug('Live my tasks refresh failed:', err);
        }
    }

    async function refreshNotificationsPage() {
        if (typeof window.SheriaNotificationsRefresh !== 'function') return;
        try {
            const data = await fetchJson('/api/notifications');
            if (!data.success) return;
            const key = (data.notifications || []).map((item) => {
                return `${item.type}:${item.title}:${item.meta}`;
            }).join('|');
            if (key === lastNotificationsKey) return;
            lastNotificationsKey = key;
            window.SheriaNotificationsRefresh(data);
        } catch (err) {
            console.debug('Live notifications refresh failed:', err);
        }
    }

    async function tick() {
        await refreshBadges();

        if (document.hidden) return;

        const page = livePage();
        if (page === 'my_tasks') {
            await refreshMyTasksPage();
        } else if (page === 'notifications') {
            await refreshNotificationsPage();
        }
    }

    function startPolling() {
        if (pollTimer) return;
        tick();
        pollTimer = setInterval(tick, POLL_MS);
    }

    document.addEventListener('visibilitychange', function () {
        if (!document.hidden) tick();
    });

    document.addEventListener('DOMContentLoaded', startPolling);

    window.SheriaLiveUpdates = {
        refreshNow: tick,
        resetMyTasksKey: function () { lastMyTasksKey = ''; },
        resetNotificationsKey: function () { lastNotificationsKey = ''; },
        showDeviceNotification: showDeviceNotification,
        showPhoneNotification: showDeviceNotification,
    };
})();
