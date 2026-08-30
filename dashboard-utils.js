(function attachDashboardUtilities(global) {
    'use strict';

    const absoluteTime = new Intl.DateTimeFormat('en-US', {
        month: 'short',
        day: 'numeric',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        timeZone: 'UTC',
        timeZoneName: 'short'
    });

    function escapeHTML(value) {
        return String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function formatMoney(value) {
        const numericValue = Number(value);
        const fractionDigits = Number.isInteger(numericValue) ? 0 : 2;
        return new Intl.NumberFormat('en-US', {
            style: 'currency',
            currency: 'USD',
            minimumFractionDigits: fractionDigits,
            maximumFractionDigits: fractionDigits
        }).format(numericValue);
    }

    function formatPercent(value, fractionDigits = 1) {
        return `${(Number(value) * 100).toFixed(fractionDigits)}%`;
    }

    function formatPercentagePoints(value) {
        return `${Number(value).toFixed(1)} pp`;
    }

    function formatRiskScore(value) {
        const truncated = Math.floor((Number(value) + Number.EPSILON) * 100) / 100;
        const display = truncated.toFixed(2);
        return display.replace(/\.00$/, '').replace(/(\.\d)0$/, '$1');
    }

    function formatInteger(value) {
        return new Intl.NumberFormat('en-US').format(Number(value));
    }

    function formatDuration(seconds) {
        const safeSeconds = Math.max(0, Number(seconds));
        const hours = Math.floor(safeSeconds / 3600);
        const minutes = Math.floor((safeSeconds % 3600) / 60);
        const remainingSeconds = Math.round(safeSeconds % 60);
        if (hours) return `${hours} h ${minutes} min`;
        if (minutes) return remainingSeconds ? `${minutes} min ${remainingSeconds} sec` : `${minutes} min`;
        return `${remainingSeconds} sec`;
    }

    function formatRelativeTime(isoTime, now = new Date()) {
        const difference = new Date(now).getTime() - new Date(isoTime).getTime();
        const direction = difference >= 0 ? 'ago' : 'from now';
        const absolute = Math.abs(difference);
        const minutes = Math.floor(absolute / 60000);
        const hours = Math.floor(absolute / 3600000);
        const days = Math.floor(absolute / 86400000);
        if (absolute < 60000) return direction === 'ago' ? 'just now' : 'in under a minute';
        if (days >= 1) return `${days} day${days === 1 ? '' : 's'} ${direction}`;
        if (hours >= 1) return `${hours} h ${direction}`;
        return `${minutes} min ${direction}`;
    }

    function formatAbsoluteTime(isoTime) {
        return absoluteTime.format(new Date(isoTime));
    }

    function timestampHTML(isoTime) {
        if (!isoTime) return '<span class="record-value--muted">Not resolved</span>';
        const safeISO = escapeHTML(isoTime);
        return `<span class="timestamp" data-relative-time="${safeISO}"><time datetime="${safeISO}">${escapeHTML(formatRelativeTime(isoTime))}</time><span>${escapeHTML(formatAbsoluteTime(isoTime))}</span></span>`;
    }

    function updateRelativeTimes(root = document) {
        root.querySelectorAll('[data-relative-time]').forEach((element) => {
            const time = element.querySelector('time');
            if (time) time.textContent = formatRelativeTime(element.dataset.relativeTime);
        });
    }

    function humanize(value) {
        return String(value ?? '')
            .split('_')
            .map((part) => part ? part.charAt(0).toUpperCase() + part.slice(1) : '')
            .join(' ');
    }

    function formatAnchorCell(anchorCell) {
        return Object.entries(anchorCell || {})
            .map(([dimension, value]) => `${humanize(dimension)} ${value}`)
            .join(' × ');
    }

    function formatMetric(metric) {
        const labels = {
            attempt_conversion_rate: 'Attempt conversion rate'
        };
        return labels[metric] || humanize(metric);
    }

    function formatBaselineSource(source) {
        const labels = {
            hour_of_week: 'Hour-of-week baseline',
            all_time: 'All-time baseline',
            inherited_from_parent: 'Inherited from parent baseline',
            none: 'No baseline available'
        };
        return labels[source] || humanize(source);
    }

    function formatDeclineCode(code) {
        const labels = {
            provider_timeout: 'Provider timeout',
            issuer_unavailable: 'Issuer unavailable',
            card_expired: 'Card expired'
        };
        return labels[code] || humanize(code);
    }

    function formatCostBasis(costBasis) {
        const labels = {
            gross: 'Gross loss (before retry recovery)',
            net_of_retry_recovery: 'Net loss (after retry recovery)'
        };
        return labels[costBasis] || humanize(costBasis);
    }

    function formatConfidence(confidence) {
        const labels = {
            high: 'High confidence',
            medium: 'Medium confidence',
            insufficient_evidence: 'Insufficient evidence'
        };
        return labels[confidence] || humanize(confidence);
    }

    function formatStatus(status) {
        return status === 'resolved' ? 'Resolved' : 'Open';
    }

    function formatEntity(entity) {
        return `${humanize(entity.dimension)} · ${entity.value}`;
    }

    global.SavvyDashboardUtils = {
        escapeHTML,
        formatMoney,
        formatPercent,
        formatPercentagePoints,
        formatRiskScore,
        formatInteger,
        formatDuration,
        formatRelativeTime,
        formatAbsoluteTime,
        timestampHTML,
        updateRelativeTimes,
        humanize,
        formatAnchorCell,
        formatMetric,
        formatBaselineSource,
        formatDeclineCode,
        formatCostBasis,
        formatConfidence,
        formatStatus,
        formatEntity
    };
}(typeof window !== 'undefined' ? window : globalThis));
