/**
 * Render a typed name as a black signature PNG (transparent background).
 */
(function (global) {
    'use strict';

    const SIGNATURE_FONTS = [
        { id: 'dancing', label: 'Classic script', family: 'Dancing Script' },
        { id: 'great-vibes', label: 'Elegant', family: 'Great Vibes' },
        { id: 'parisienne', label: 'Formal', family: 'Parisienne' },
        { id: 'sacramento', label: 'Casual', family: 'Sacramento' },
    ];

    const SIZE_PX = { small: 52, medium: 68, large: 84 };

    function getFontById(id) {
        return SIGNATURE_FONTS.find(function (f) { return f.id === id; }) || SIGNATURE_FONTS[0];
    }

    let fontsReadyPromise = null;

    function ensureSignatureFontsLoaded() {
        if (fontsReadyPromise) return fontsReadyPromise;
        if (!document.fonts || !document.fonts.load) {
            fontsReadyPromise = Promise.resolve();
            return fontsReadyPromise;
        }
        fontsReadyPromise = Promise.all(
            SIGNATURE_FONTS.map(function (f) {
                return document.fonts.load('48px "' + f.family + '"').catch(function () {});
            })
        ).then(function () {
            return document.fonts.ready;
        });
        return fontsReadyPromise;
    }

    /**
     * @param {string} text
     * @param {string} fontId
     * @param {'small'|'medium'|'large'} sizeKey
     * @returns {Promise<string>} PNG data URL or empty string
     */
    function renderTypedSignature(text, fontId, sizeKey) {
        text = (text || '').trim();
        if (!text) return Promise.resolve('');

        const font = getFontById(fontId);
        const sizePx = SIZE_PX[sizeKey] || SIZE_PX.medium;

        return ensureSignatureFontsLoaded().then(function () {
            const measure = document.createElement('canvas');
            const mctx = measure.getContext('2d');
            const fontSpec = sizePx + 'px "' + font.family + '", cursive';
            mctx.font = fontSpec;
            const metrics = mctx.measureText(text);
            const textW = Math.ceil(metrics.width);
            const textH = Math.ceil(sizePx * 1.2);
            const padX = Math.max(20, Math.round(sizePx * 0.35));
            const padY = Math.max(14, Math.round(sizePx * 0.25));

            const w = textW + padX * 2;
            const h = textH + padY * 2;

            const canvas = document.createElement('canvas');
            canvas.width = w;
            canvas.height = h;
            const ctx = canvas.getContext('2d');
            // White background (not transparent) — Google Docs treats transparent PNGs as black boxes.
            ctx.fillStyle = '#ffffff';
            ctx.fillRect(0, 0, w, h);
            ctx.font = fontSpec;
            ctx.fillStyle = '#000000';
            ctx.textBaseline = 'middle';
            ctx.textAlign = 'left';
            ctx.fillText(text, padX, h / 2);

            return canvas.toDataURL('image/png', 1.0);
        });
    }

    global.SIGNATURE_FONTS = SIGNATURE_FONTS;
    global.SIGNATURE_SIZE_PX = SIZE_PX;
    global.ensureSignatureFontsLoaded = ensureSignatureFontsLoaded;
    global.renderTypedSignature = renderTypedSignature;
})(typeof window !== 'undefined' ? window : globalThis);
