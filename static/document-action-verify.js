(function (global) {
    'use strict';

    function createDocumentActionVerify(config) {
        var apiBase = config.apiBase;
        var taskId = config.taskId || null;
        var downloadUrlBuilder = config.downloadUrlBuilder;
        var deleteUrlBuilder = config.deleteUrlBuilder;
        var onDeleteSuccess = config.onDeleteSuccess || function () { global.location.reload(); };

        var pendingAction = null;
        var codeSent = false;

        function el(id) { return document.getElementById(id); }

        function buildPayload() {
            if (!pendingAction) return null;
            var p = {
                action: pendingAction.action,
                drive_file_id: pendingAction.fileId
            };
            if (taskId) p.task_id = taskId;
            return p;
        }

        function showError(msg) {
            var errorEl = el('docActionVerifyError');
            if (!errorEl) return;
            errorEl.textContent = msg;
            errorEl.classList.remove('hidden');
        }

        function clearError() {
            var errorEl = el('docActionVerifyError');
            if (!errorEl) return;
            errorEl.textContent = '';
            errorEl.classList.add('hidden');
        }

        function setSendStatus(msg) {
            var statusEl = el('docActionVerifySendStatus');
            if (!statusEl) return;
            statusEl.textContent = msg;
            statusEl.classList.remove('hidden');
        }

        function open(action, fileId, label) {
            pendingAction = { action: action, fileId: fileId, label: label || 'Document' };
            codeSent = false;

            var isDelete = action === 'delete';
            var titleEl = el('docActionVerifyTitle');
            if (titleEl) {
                titleEl.innerHTML = '<i class="fas fa-shield-alt mr-2"></i>' + (isDelete ? 'Verify to delete' : 'Verify to download');
            }
            var actionTextEl = el('docActionVerifyActionText');
            if (actionTextEl) {
                actionTextEl.textContent =
                    'A verification code will be sent to your registered email. Enter it below to ' +
                    (isDelete ? 'permanently delete' : 'download') + ':';
            }
            var nameEl = el('docActionVerifyDocName');
            if (nameEl) nameEl.textContent = pendingAction.label;
            var codeInput = el('docActionVerifyCode');
            if (codeInput) codeInput.value = '';
            var statusEl = el('docActionVerifySendStatus');
            if (statusEl) statusEl.classList.add('hidden');
            clearError();

            var confirmBtn = el('docActionVerifyConfirmBtn');
            if (confirmBtn) {
                confirmBtn.disabled = false;
                confirmBtn.classList.remove('opacity-60', 'cursor-not-allowed');
            }
            var confirmLabel = el('docActionVerifyConfirmLabel');
            if (confirmLabel) confirmLabel.textContent = isDelete ? 'Delete' : 'Download';

            var modal = el('docActionVerifyModal');
            if (modal) {
                modal.classList.remove('hidden');
                document.body.style.overflow = 'hidden';
            }

            sendCode();
        }

        function close() {
            var modal = el('docActionVerifyModal');
            if (modal) modal.classList.add('hidden');
            document.body.style.overflow = '';
            pendingAction = null;
            codeSent = false;
        }

        function sendCode() {
            var payload = buildPayload();
            if (!payload) return;

            var resendBtn = el('docActionVerifyResendBtn');
            if (resendBtn) resendBtn.disabled = true;
            clearError();

            fetch(apiBase + '/action/send-code', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            })
                .then(function (res) { return res.json().then(function (data) { return { ok: res.ok, data: data }; }); })
                .then(function (result) {
                    if (!result.ok || !result.data.success) {
                        showError((result.data && result.data.error) || 'Could not send verification code.');
                        return;
                    }
                    codeSent = true;
                    setSendStatus(result.data.message || 'Verification code sent.');
                })
                .catch(function () {
                    showError('Network error. Please try again.');
                })
                .finally(function () {
                    if (resendBtn) resendBtn.disabled = false;
                });
        }

        function confirm() {
            if (!pendingAction) return;

            var codeInput = el('docActionVerifyCode');
            var code = (codeInput && codeInput.value || '').trim();
            if (!/^\d{6}$/.test(code)) {
                showError('Please enter the 6-digit verification code.');
                return;
            }
            if (!codeSent) {
                showError('Request a verification code first.');
                return;
            }

            var confirmBtn = el('docActionVerifyConfirmBtn');
            if (confirmBtn) {
                confirmBtn.disabled = true;
                confirmBtn.classList.add('opacity-60', 'cursor-not-allowed');
            }
            clearError();

            var payload = buildPayload();
            payload.code = code;

            fetch(apiBase + '/action/verify-code', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            })
                .then(function (res) { return res.json().then(function (data) { return { ok: res.ok, data: data }; }); })
                .then(function (result) {
                    if (!result.ok || !result.data.success) {
                        showError((result.data && result.data.error) || 'Verification failed.');
                        if (confirmBtn) {
                            confirmBtn.disabled = false;
                            confirmBtn.classList.remove('opacity-60', 'cursor-not-allowed');
                        }
                        return;
                    }

                    var token = result.data.action_token;
                    if (pendingAction.action === 'download') {
                        var downloadUrl = downloadUrlBuilder(pendingAction.fileId, token);
                        close();
                        global.location.href = downloadUrl;
                        return;
                    }

                    var deletePayload = Object.assign({}, buildPayload(), { token: token });
                    fetch(deleteUrlBuilder(pendingAction.fileId), {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(deletePayload)
                    })
                        .then(function (res) { return res.json().then(function (data) { return { ok: res.ok, data: data }; }); })
                        .then(function (delResult) {
                            if (!delResult.ok || !delResult.data.success) {
                                showError((delResult.data && delResult.data.error) || 'Delete failed.');
                                if (confirmBtn) {
                                    confirmBtn.disabled = false;
                                    confirmBtn.classList.remove('opacity-60', 'cursor-not-allowed');
                                }
                                return;
                            }
                            close();
                            onDeleteSuccess(delResult.data);
                        })
                        .catch(function () {
                            showError('Network error during delete.');
                            if (confirmBtn) {
                                confirmBtn.disabled = false;
                                confirmBtn.classList.remove('opacity-60', 'cursor-not-allowed');
                            }
                        });
                })
                .catch(function () {
                    showError('Network error. Please try again.');
                    if (confirmBtn) {
                        confirmBtn.disabled = false;
                        confirmBtn.classList.remove('opacity-60', 'cursor-not-allowed');
                    }
                });
        }

        global.closeDocActionVerifyModal = close;
        global.sendDocActionVerificationCode = sendCode;
        global.confirmDocActionVerified = confirm;

        return { open: open, close: close };
    }

    global.createDocumentActionVerify = createDocumentActionVerify;
})(window);
