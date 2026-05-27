/**
 * Extract signature/stamp ink from a scanned photo (not the whole picture).
 * Uses local background contrast (works on white, cream, or yellow paper).
 */
(function (global) {
    'use strict';

    function percentile(sortedCopy, p) {
        if (!sortedCopy.length) return 128;
        const idx = Math.min(sortedCopy.length - 1, Math.max(0, Math.floor((sortedCopy.length - 1) * p)));
        return sortedCopy[idx];
    }

    function boxBlur(src, width, height, radius) {
        const r = Math.max(1, radius);
        const out = new Float32Array(width * height);
        const tmp = new Float32Array(width * height);
        const win = 2 * r + 1;

        for (let y = 0; y < height; y++) {
            let sum = 0;
            for (let x = -r; x <= r; x++) {
                const cx = Math.min(width - 1, Math.max(0, x));
                sum += src[y * width + cx];
            }
            for (let x = 0; x < width; x++) {
                const addX = Math.min(width - 1, x + r + 1);
                const subX = Math.max(0, x - r);
                if (x > 0) {
                    sum += src[y * width + addX] - src[y * width + subX];
                }
                tmp[y * width + x] = sum / win;
            }
        }

        for (let x = 0; x < width; x++) {
            let sum = 0;
            for (let y = -r; y <= r; y++) {
                const cy = Math.min(height - 1, Math.max(0, y));
                sum += tmp[cy * width + x];
            }
            for (let y = 0; y < height; y++) {
                const addY = Math.min(height - 1, y + r + 1);
                const subY = Math.max(0, y - r);
                if (y > 0) {
                    sum += tmp[addY * width + x] - tmp[subY * width + x];
                }
                out[y * width + x] = sum / win;
            }
        }
        return out;
    }

    function estimatePaperLum(lum) {
        const sample = [];
        const step = Math.max(1, Math.floor(lum.length / 8000));
        for (let i = 0; i < lum.length; i += step) sample.push(lum[i]);
        sample.sort((a, b) => a - b);
        return percentile(sample, 0.88);
    }

    function buildInkMask(lum, width, height) {
        const localMean = boxBlur(lum, width, height, 14);
        const paperLum = estimatePaperLum(lum);

        const contrast = new Float32Array(width * height);
        const scores = [];
        for (let i = 0; i < lum.length; i++) {
            const localDelta = localMean[i] - lum[i];
            const globalDelta = paperLum - lum[i];
            contrast[i] = Math.max(localDelta, globalDelta * 0.85);
            if (contrast[i] > 8) scores.push(contrast[i]);
        }

        scores.sort((a, b) => a - b);
        let thresh = 22;
        if (scores.length > 50) {
            const p75 = percentile(scores, 0.75);
            const p25 = percentile(scores, 0.25);
            const spread = p75 - p25;
            thresh = Math.max(14, Math.min(55, p25 + spread * 0.35));
        }

        // Hysteresis thresholding:
        // - strong pixels definitely ink
        // - weak pixels included only if connected to strong
        const strongT = thresh;
        const weakT = Math.max(8, thresh * 0.62);

        const strong = new Uint8Array(width * height);
        const weak = new Uint8Array(width * height);
        let strongCount = 0;
        for (let i = 0; i < lum.length; i++) {
            const c = contrast[i];
            if (c >= strongT) {
                strong[i] = 1;
                strongCount++;
            } else if (c >= weakT) {
                weak[i] = 1;
            }
        }

        // Flood-fill from strong into weak to keep faint strokes connected to ink.
        const ink = new Uint8Array(width * height);
        const q = new Int32Array(width * height);
        let qh = 0, qt = 0;
        for (let i = 0; i < strong.length; i++) {
            if (strong[i]) {
                ink[i] = 1;
                q[qt++] = i;
            }
        }
        const n4 = [-1, 1, -width, width];
        while (qh < qt) {
            const idx = q[qh++];
            const x = idx % width;
            for (let k = 0; k < 4; k++) {
                const ni = idx + n4[k];
                if (ni < 0 || ni >= ink.length) continue;
                if (k === 0 && x === 0) continue;
                if (k === 1 && x === width - 1) continue;
                if (ink[ni]) continue;
                if (weak[ni]) {
                    ink[ni] = 1;
                    q[qt++] = ni;
                }
            }
        }

        // If nothing was strong (very light pen), fall back to weak-only mask.
        if (strongCount < width * height * 0.0006) {
            let cnt = 0;
            for (let i = 0; i < weak.length; i++) {
                if (weak[i]) {
                    ink[i] = 1;
                    cnt++;
                }
            }
            if (cnt < width * height * 0.0006) {
                // last resort: be more permissive
                const lastT = Math.max(10, thresh * 0.50);
                for (let i = 0; i < lum.length; i++) {
                    if (contrast[i] >= lastT) ink[i] = 1;
                }
            }
        }

        return { ink, contrast, thresh };
    }

    function morphOpen(ink, width, height) {
        const eroded = new Uint8Array(width * height);
        const out = new Uint8Array(width * height);
        for (let y = 1; y < height - 1; y++) {
            for (let x = 1; x < width - 1; x++) {
                const idx = y * width + x;
                if (
                    ink[idx] &&
                    ink[idx - 1] && ink[idx + 1] &&
                    ink[idx - width] && ink[idx + width]
                ) {
                    eroded[idx] = 1;
                }
            }
        }
        for (let y = 0; y < height; y++) {
            for (let x = 0; x < width; x++) {
                const idx = y * width + x;
                if (!eroded[idx]) continue;
                for (let dy = -1; dy <= 1; dy++) {
                    for (let dx = -1; dx <= 1; dx++) {
                        const nx = x + dx;
                        const ny = y + dy;
                        if (nx >= 0 && ny >= 0 && nx < width && ny < height) {
                            out[ny * width + nx] = 1;
                        }
                    }
                }
            }
        }
        return out;
    }

    function labelInkMask(ink, width, height) {
        const labels = new Int32Array(width * height);
        const sizes = [0];
        const centX = [];
        const centY = [];
        const borderTouch = [];
        let next = 1;
        const qx = [];
        const qy = [];
        const margin = Math.max(2, Math.round(Math.min(width, height) * 0.02));

        for (let y = 0; y < height; y++) {
            for (let x = 0; x < width; x++) {
                const idx = y * width + x;
                if (!ink[idx] || labels[idx]) continue;

                let count = 0;
                let sumX = 0;
                let sumY = 0;
                let border = 0;
                labels[idx] = next;
                qx.length = 0;
                qy.length = 0;
                qx.push(x);
                qy.push(y);
                let qi = 0;

                while (qi < qx.length) {
                    const cx = qx[qi];
                    const cy = qy[qi];
                    qi++;
                    count++;
                    sumX += cx;
                    sumY += cy;
                    if (
                        cx <= margin || cy <= margin ||
                        cx >= width - 1 - margin || cy >= height - 1 - margin
                    ) {
                        border++;
                    }

                    const neighbors = [
                        [cx - 1, cy], [cx + 1, cy], [cx, cy - 1], [cx, cy + 1],
                        [cx - 1, cy - 1], [cx + 1, cy - 1], [cx - 1, cy + 1], [cx + 1, cy + 1],
                    ];
                    for (let n = 0; n < 8; n++) {
                        const nx = neighbors[n][0];
                        const ny = neighbors[n][1];
                        if (nx < 0 || ny < 0 || nx >= width || ny >= height) continue;
                        const ni = ny * width + nx;
                        if (ink[ni] && !labels[ni]) {
                            labels[ni] = next;
                            qx.push(nx);
                            qy.push(ny);
                        }
                    }
                }

                sizes[next] = count;
                centX[next] = sumX / count;
                centY[next] = sumY / count;
                borderTouch[next] = border / count;
                next++;
            }
        }
        return { labels, sizes, centX, centY, borderTouch, count: next - 1 };
    }

    function boxesOverlap(a, b, gap) {
        return !(
            a.maxX + gap < b.minX ||
            b.maxX + gap < a.minX ||
            a.maxY + gap < b.minY ||
            b.maxY + gap < a.minY
        );
    }

    function selectInkLabels(labels, sizes, centX, centY, borderTouch, count, width, height, mode) {
        const minArea = Math.max(8, Math.round((width * height) * 0.00008));
        // Shadows can occupy a big chunk of the frame; don't even consider huge blobs.
        const maxArea = Math.round((width * height) * 0.26);
        const candidates = [];

        for (let id = 1; id <= count; id++) {
            const area = sizes[id];
            if (area < minArea || area > maxArea) continue;

            const box = bboxForLabel(labels, id, width, height);
            const fill = area / Math.max(1, box.bw * box.bh);
            const cx = centX[id];
            const cy = centY[id];
            const borderFrac = borderTouch[id];

            const inCorner =
                (cx < width * 0.22 && cy < height * 0.22) ||
                (cx > width * 0.78 && cy < height * 0.22) ||
                (cx < width * 0.22 && cy > height * 0.78) ||
                (cx > width * 0.78 && cy > height * 0.78);

            let reject = false;
            if (inCorner && area > minArea * 6 && borderFrac > 0.12) reject = true;
            // Strongly reject border-touching or filled regions (typical of shadows / desk edges).
            if (borderFrac > 0.18 && area > (width * height) * 0.006) reject = true;
            if (borderFrac > 0.35 && area > (width * height) * 0.002) reject = true;
            if (fill > 0.35 && area > (width * height) * 0.01) reject = true;
            if (fill > 0.55 && area > minArea * 10) reject = true;

            if (!reject) {
                const centerDist = Math.hypot((cx - width * 0.5) / width, (cy - height * 0.5) / height); // 0..~0.7
                const centerWeight = Math.max(0, 1.0 - centerDist * 1.35); // prefer center-ish strokes
                const strokeWeight = Math.max(0.05, 1.0 - Math.min(0.95, fill)); // prefer thin strokes
                const borderWeight = Math.max(0.05, 1.0 - Math.min(0.95, borderFrac * 1.4));
                const score = area * strokeWeight * borderWeight * (0.55 + 0.45 * centerWeight);
                candidates.push({
                    id,
                    area,
                    box,
                    cx,
                    cy,
                    fill,
                    borderFrac,
                    score,
                });
            }
        }

        if (!candidates.length) return new Set();

        // Sort by "stroke-likeness" score, not just raw area (prevents corner shadows winning).
        candidates.sort((a, b) => (b.score - a.score) || (b.area - a.area));

        const midX = width * 0.5;
        const midY = height * 0.5;
        const strokeLike = candidates.filter((c) => {
            const nearCenter =
                Math.abs(c.cx - midX) < width * 0.48 &&
                Math.abs(c.cy - midY) < height * 0.48;
            return nearCenter || c.area < (width * height) * 0.12;
        });
        const pool = strokeLike.length ? strokeLike : candidates;

        const keep = new Set();
        const seed = pool[0];
        keep.add(seed.id);

        // Signatures often have separated letters; allow wider merge.
        const mergeGap = mode === 'stamp' ? 56 : 120;
        const totalInkArea = pool.reduce((s, c) => s + c.area, 0);
        const areaFloor = Math.max(minArea * 2, totalInkArea * 0.008);

        let changed = true;
        while (changed) {
            changed = false;
            for (let i = 0; i < pool.length; i++) {
                const c = pool[i];
                if (keep.has(c.id)) continue;
                if (c.area < areaFloor) continue;

                for (const kid of keep) {
                    const other = pool.find((x) => x.id === kid);
                    if (!other) continue;
                    const gap = mergeGap + Math.max(other.box.bw, other.box.bh, c.box.bw, c.box.bh) * 0.22;
                    if (boxesOverlap(c.box, other.box, gap)) {
                        keep.add(c.id);
                        changed = true;
                        break;
                    }
                }
            }
        }

        // If we still only kept one cluster, include additional stroke-like clusters nearby
        // (common when a word breaks into two components).
        if (keep.size < 2 && pool.length > 1) {
            for (let i = 1; i < Math.min(pool.length, 12); i++) {
                const c = pool[i];
                if (c.area >= areaFloor) keep.add(c.id);
            }
        }

        return keep;
    }

    function bboxForLabel(labels, label, width, height) {
        let minX = width, minY = height, maxX = 0, maxY = 0;
        let count = 0;
        for (let y = 0; y < height; y++) {
            for (let x = 0; x < width; x++) {
                if (labels[y * width + x] === label) {
                    count++;
                    minX = Math.min(minX, x);
                    minY = Math.min(minY, y);
                    maxX = Math.max(maxX, x);
                    maxY = Math.max(maxY, y);
                }
            }
        }
        const bw = maxX - minX + 1;
        const bh = maxY - minY + 1;
        return { minX, minY, maxX, maxY, bw, bh, fill: count / Math.max(1, bw * bh) };
    }

    /**
     * @param {HTMLImageElement} img
     * @param {'signature'|'stamp'} mode
     * @returns {string} data URL PNG
     */
    function extractInkFromScan(img, mode) {
        mode = mode || 'signature';
        const maxAnalyze = 1200;
        let width = img.width;
        let height = img.height;
        if (width > maxAnalyze || height > maxAnalyze) {
            const r = Math.min(maxAnalyze / width, maxAnalyze / height);
            width = Math.round(width * r);
            height = Math.round(height * r);
        }

        const work = document.createElement('canvas');
        work.width = width;
        work.height = height;
        const wctx = work.getContext('2d', { willReadFrequently: true });
        wctx.drawImage(img, 0, 0, width, height);
        const imageData = wctx.getImageData(0, 0, width, height);
        const data = imageData.data;

        const lum = new Uint8Array(width * height);
        for (let i = 0, p = 0; i < data.length; i += 4, p++) {
            lum[p] = Math.round(0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2]);
        }

        let { ink, contrast } = buildInkMask(lum, width, height);
        // Light touch: opening helps speckle noise, but too much removes pen strokes.
        // Our implementation is 1px, so keep it but only after hysteresis.
        ink = morphOpen(ink, width, height);

        let { labels, sizes, centX, centY, borderTouch, count } = labelInkMask(ink, width, height);
        let keepLabels = selectInkLabels(
            labels, sizes, centX, centY, borderTouch, count, width, height, mode
        );

        if (keepLabels.size === 0) {
            const relaxed = new Uint8Array(width * height);
            for (let i = 0; i < contrast.length; i++) {
                if (contrast[i] >= 12) relaxed[i] = 1;
            }
            const relabeled = labelInkMask(relaxed, width, height);
            keepLabels = selectInkLabels(
                relabeled.labels,
                relabeled.sizes,
                relabeled.centX,
                relabeled.centY,
                relabeled.borderTouch,
                relabeled.count,
                width,
                height,
                mode
            );
            labels = relabeled.labels;
            sizes = relabeled.sizes;
            ink = relaxed;
        }

        let minX = width, minY = height, maxX = 0, maxY = 0;
        const kept = [];
        for (let y = 0; y < height; y++) {
            for (let x = 0; x < width; x++) {
                const idx = y * width + x;
                const lab = labels[idx];
                if (!ink[idx] || !keepLabels.has(lab)) continue;
                const score = contrast[idx];
                kept.push({ x, y, score });
                minX = Math.min(minX, x);
                minY = Math.min(minY, y);
                maxX = Math.max(maxX, x);
                maxY = Math.max(maxY, y);
            }
        }

        if (!kept.length) {
            const out = document.createElement('canvas');
            out.width = Math.min(width, 400);
            out.height = Math.min(height, 160);
            return out.toDataURL('image/png', 1.0);
        }

        const pad = 10;
        minX = Math.max(0, minX - pad);
        minY = Math.max(0, minY - pad);
        maxX = Math.min(width - 1, maxX + pad);
        maxY = Math.min(height - 1, maxY + pad);
        const outW = maxX - minX + 1;
        const outH = maxY - minY + 1;

        const maxOutW = mode === 'stamp' ? 320 : 400;
        const maxOutH = mode === 'stamp' ? 320 : 200;
        let finalW = outW;
        let finalH = outH;
        if (finalW > maxOutW || finalH > maxOutH) {
            const scale = Math.min(maxOutW / finalW, maxOutH / finalH);
            finalW = Math.round(finalW * scale);
            finalH = Math.round(finalH * scale);
        }

        const out = document.createElement('canvas');
        out.width = finalW;
        out.height = finalH;
        const octx = out.getContext('2d');
        const outData = octx.createImageData(finalW, finalH);
        const scaleX = outW / finalW;
        const scaleY = outH / finalH;

        let minScore = Infinity;
        let maxScore = 0;
        kept.forEach((p) => {
            minScore = Math.min(minScore, p.score);
            maxScore = Math.max(maxScore, p.score);
        });
        const range = Math.max(1, maxScore - minScore);

        kept.forEach((p) => {
            const nx = Math.round((p.x - minX) / scaleX);
            const ny = Math.round((p.y - minY) / scaleY);
            if (nx < 0 || ny < 0 || nx >= finalW || ny >= finalH) return;
            const idx = (ny * finalW + nx) * 4;
            const norm = (p.score - minScore) / range;
            const alpha = Math.round(100 + norm * 155);
            if (alpha > outData.data[idx + 3]) {
                outData.data[idx] = 0;
                outData.data[idx + 1] = 0;
                outData.data[idx + 2] = 0;
                outData.data[idx + 3] = Math.min(255, alpha);
            }
        });
        octx.putImageData(outData, 0, 0);
        return out.toDataURL('image/png', 1.0);
    }

    global.extractInkFromScan = extractInkFromScan;
})(typeof window !== 'undefined' ? window : globalThis);
