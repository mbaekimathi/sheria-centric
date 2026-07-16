(function () {
    const DEFAULT_POLL_MS = 15000;
    const NOTIFICATIONS_POLL_MS = 4000;
    const MY_TASKS_POLL_MS = 8000;
    let pollTimer = null;
    let lastBadgeKey = '';
    let lastMyTasksKey = '';
    let lastNotificationsKey = '';
    let prevNotificationCount = null;
    let tickInFlight = false;

    function livePage() {
        return (document.body && document.body.dataset.livePage) || '';
    }

    function currentPollMs() {
        const page = livePage();
        if (page === 'notifications') return NOTIFICATIONS_POLL_MS;
        if (page === 'my_tasks') return MY_TASKS_POLL_MS;
        return DEFAULT_POLL_MS;
    }

    async function fetchJson(url) {
        const response = await fetch(url, {
            headers: { Accept: 'application/json' },
            credentials: 'same-origin',
            cache: 'no-store',
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

    function setLiveIconState(kind, count) {
        const value = Math.max(0, parseInt(count, 10) || 0);
        document.querySelectorAll(`[data-live-icon="${kind}"]`).forEach((el) => {
            el.classList.toggle('is-live', value > 0);
        });
    }

    function updateBadgeCounts(data) {
        const notificationBadges = document.querySelectorAll('[data-live-badge="notifications"]');
        const myTaskBadges = document.querySelectorAll('[data-live-badge="my-tasks"]');
        const reminderBadges = document.querySelectorAll('[data-live-badge="reminders"]');
        notificationBadges.forEach((el) => setBadgeCount(el, data.notification_badge_count));
        myTaskBadges.forEach((el) => setBadgeCount(el, data.my_task_badge_count));
        reminderBadges.forEach((el) => setBadgeCount(el, data.reminder_badge_count));
        setLiveIconState('notifications', data.notification_badge_count);
        setLiveIconState('my-tasks', data.my_task_badge_count);
        setLiveIconState('reminders', data.reminder_badge_count);
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
        // When already on the notifications page, the live list refresh is enough.
        if (livePage() === 'notifications') {
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
            if (!data.success) return false;

            const nextCount = parseInt(data.notification_badge_count, 10) || 0;
            const key = [
                data.my_task_badge_count,
                data.notification_badge_count,
                data.reminder_badge_count,
            ].join('|');

            const changed = key !== lastBadgeKey;
            if (changed) {
                await maybeAlertOnNewItems(prevNotificationCount, nextCount);
                prevNotificationCount = nextCount;
                lastBadgeKey = key;
                updateBadgeCounts(data);
            }
            return changed;
        } catch (err) {
            console.debug('Live badge refresh failed:', err);
            return false;
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

    async function refreshNotificationsPage(force) {
        if (typeof window.SheriaNotificationsRefresh !== 'function') return;
        try {
            const data = await fetchJson('/api/notifications/page');
            if (!data.success) return;
            const key = (data.notifications || []).map((item) => {
                return `${item.key || item.type}:${item.title}:${item.meta}`;
            }).join('|');
            if (!force && key === lastNotificationsKey) return;
            lastNotificationsKey = key;
            window.SheriaNotificationsRefresh(data);
        } catch (err) {
            console.debug('Live notifications refresh failed:', err);
        }
    }

    async function tick(options) {
        if (tickInFlight) return;
        tickInFlight = true;
        const force = !!(options && options.force);
        try {
            const badgesChanged = await refreshBadges();

            if (document.hidden) return;

            const page = livePage();
            if (page === 'my_tasks') {
                await refreshMyTasksPage();
            } else if (page === 'notifications') {
                // Refresh the list whenever badges change or on the normal live poll.
                await refreshNotificationsPage(force || badgesChanged);
            }
        } finally {
            tickInFlight = false;
        }
    }

    function stopPolling() {
        if (!pollTimer) return;
        clearInterval(pollTimer);
        pollTimer = null;
    }

    function startPolling() {
        stopPolling();
        tick({ force: true });
        pollTimer = setInterval(function () {
            tick();
        }, currentPollMs());
    }

    document.addEventListener('visibilitychange', function () {
        if (!document.hidden) {
            startPolling();
        }
    });

    document.addEventListener('DOMContentLoaded', startPolling);

    window.SheriaLiveUpdates = {
        refreshNow: function () { return tick({ force: true }); },
        resetMyTasksKey: function () { lastMyTasksKey = ''; },
        resetNotificationsKey: function () { lastNotificationsKey = ''; },
        showDeviceNotification: showDeviceNotification,
        showPhoneNotification: showDeviceNotification,
        startPolling: startPolling,
        stopPolling: stopPolling,
    };
})();
