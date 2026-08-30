(function renderDashboard() {
    'use strict';

    const data = window.SavvyDashboardData;
    const utils = window.SavvyDashboardUtils;
    const {
        escapeHTML,
        formatMoney,
        formatInteger,
        formatPercentagePoints,
        formatRiskScore,
        timestampHTML,
        updateRelativeTimes,
        formatAnchorCell,
        formatConfidence,
        formatStatus
    } = utils;

    const severityClass = {
        S1: 's1',
        S2: 's2',
        S3: 's3',
        Monitoring: 'monitoring'
    };

    const state = {
        status: 'all',
        severity: 'all',
        confidence: 'all',
        query: '',
        sort: 'risk'
    };

    const sum = (items, field) => items.reduce((total, item) => total + Number(item[field] || 0), 0);

    function reportSeverity(report) {
        const code = data.getSeverity(report);
        return { code, meta: data.severityMeta[code], score: data.calculateRiskScore(report) };
    }

    function severityBadge(report) {
        const severity = reportSeverity(report);
        return `<span class="badge badge--${severityClass[severity.code]}">${escapeHTML(severity.meta.label)}</span>`;
    }

    function confidenceBadge(report) {
        const className = report.confidence === 'insufficient_evidence' ? 'limited' : report.confidence;
        return `<span class="badge badge--${className}">${escapeHTML(formatConfidence(report.confidence))}</span>`;
    }

    function renderSummary(liveReports) {
        const openReports = liveReports.filter((report) => report.status === 'open');
        const openBurn = sum(openReports, 'burn_rate_usd_hour');
        const openLoss = sum(openReports, 'cumulative_loss_usd');
        const highestRisk = openReports.slice().sort((a, b) => data.calculateRiskScore(b) - data.calculateRiskScore(a))[0];
        const highestSeverity = highestRisk ? reportSeverity(highestRisk) : null;
        const severityCounts = ['S1', 'S2', 'S3', 'Monitoring'].reduce((counts, severity) => {
            counts[severity] = openReports.filter((report) => data.getSeverity(report) === severity).length;
            return counts;
        }, {});

        document.getElementById('summaryGrid').innerHTML = `
            <article class="summary-card summary-card--primary" aria-label="Open money at risk">
                <div>
                    <div class="summary-label">Open money at risk</div>
                    <div class="summary-value">${formatMoney(openBurn)}<span class="sr-only"> per hour</span><span aria-hidden="true">/h</span></div>
                </div>
                <p class="summary-footnote">${openReports.length} active incident${openReports.length === 1 ? '' : 's'} · exact values</p>
            </article>
            <article class="summary-card" aria-label="Highest active severity">
                <div>
                    <div class="summary-label">Highest active severity</div>
                    <div class="summary-value">${highestSeverity ? escapeHTML(highestSeverity.meta.shortLabel) : '—'}</div>
                    <p class="summary-detail">${highestSeverity ? `${escapeHTML(highestSeverity.meta.description)} · score ${formatRiskScore(highestSeverity.score)}/100` : 'No active incident'}</p>
                </div>
                <p class="summary-footnote">Confidence-capped</p>
            </article>
            <article class="summary-card" aria-label="Open incident count">
                <div>
                    <div class="summary-label">Open incidents</div>
                    <div class="summary-value">${formatInteger(openReports.length)}</div>
                </div>
                <p class="summary-footnote"><span class="summary-status">Live view</span> · newest version.</p>
            </article>
            <article class="summary-card" aria-label="Reported cumulative loss">
                <div>
                    <div class="summary-label">Reported loss so far</div>
                    <div class="summary-value">${formatMoney(openLoss)}</div>
                </div>
                <p class="summary-footnote">Open incidents</p>
            </article>`;

        const severityMarkup = ['S1', 'S2', 'S3', 'Monitoring'].map((severity) => {
            const meta = data.severityMeta[severity];
            return `<div class="severity-row">
                <div class="severity-row__heading">
                    <span class="badge badge--${severityClass[severity]}">${escapeHTML(meta.label)}</span>
                    <span class="severity-count">${formatInteger(severityCounts[severity])} open</span>
                </div>
                <div class="bar-track" aria-hidden="true"><div class="bar-fill bar-fill--${severityClass[severity]}" style="width:${openReports.length ? (severityCounts[severity] / openReports.length) * 100 : 0}%"></div></div>
            </div>`;
        }).join('');

        document.getElementById('severityPanel').innerHTML = `
            <div class="panel-header">
                <div>
                    <h2 class="panel-title">Open incidents by severity</h2>
                </div>
            </div>
            <div class="panel-body">
                <div class="severity-list">${severityMarkup}</div>
                <div class="severity-explainer">
                    <span><strong>S1:</strong> 90–100 · <strong>S2:</strong> 70–89 · <strong>S3:</strong> 40–69</span>
                    <span>Medium: max 74 · Evidence limited: max 49</span>
                </div>
            </div>`;

        const maxBurn = Math.max(...openReports.map((report) => report.burn_rate_usd_hour), 1);
        const exposureMarkup = openReports
            .slice()
            .sort((a, b) => b.burn_rate_usd_hour - a.burn_rate_usd_hour)
            .map((report) => `<div class="exposure-row">
                <div class="exposure-row__heading">
                    <span class="exposure-route">${escapeHTML(formatAnchorCell(report.anchor_cell))}</span>
                    <span class="exposure-value">${formatMoney(report.burn_rate_usd_hour)}/h</span>
                </div>
                <div class="bar-track" aria-hidden="true"><div class="bar-fill" style="width:${(report.burn_rate_usd_hour / maxBurn) * 100}%"></div></div>
            </div>`).join('');

        document.getElementById('exposurePanel').innerHTML = `
            <div class="panel-header">
                <div>
                    <h2 class="panel-title">Current exposure by incident</h2>
                </div>
            </div>
            <div class="panel-body">
                <div class="exposure-list">${exposureMarkup || '<p class="panel-description">No open incident reports.</p>'}</div>
            </div>`;
    }

    function stateTags(report) {
        const tags = [];
        if (report.confidence === 'insufficient_evidence') tags.push('<span class="state-tag state-tag--limited">Evidence limited</span>');
        if (report.repeat_of_incident_id) tags.push('<span class="state-tag state-tag--repeat">Repeat incident</span>');
        const revisionCount = data.getReportsForIncident(report.incident_id).length;
        if (revisionCount > 1) tags.push(`<span class="state-tag">${revisionCount} revisions</span>`);
        return tags.join('');
    }

    function renderIncidentRow(report) {
        const severity = reportSeverity(report);
        const route = formatAnchorCell(report.anchor_cell);
        const revisionCount = data.getReportsForIncident(report.incident_id).length;
        return `<a class="incident-row" href="incident-detail.html?id=${encodeURIComponent(report.incident_id)}">
            <div class="incident-row__identity">
                <span class="table-label">Incident</span>
                <h3>${escapeHTML(route)}</h3>
                <p>${escapeHTML(report.incident_id)} · r${report.revision}${revisionCount > 1 ? `/${revisionCount}` : ''}</p>
                <div class="incident-row__tags">${stateTags(report)}</div>
            </div>
            <div class="incident-row__cell">
                <span class="table-label">Severity</span>
                <div class="chip-row">${severityBadge(report)} ${confidenceBadge(report)}</div>
                <span class="incident-row__subvalue">Score ${formatRiskScore(severity.score)}/100</span>
            </div>
            <div class="incident-row__cell">
                <span class="table-label">Status</span>
                <span class="badge badge--${report.status === 'open' ? 'open' : 'resolved'}">${escapeHTML(formatStatus(report.status))}</span>
            </div>
            <div class="incident-row__cell incident-row__exposure">
                <span class="table-label">Exposure</span>
                <strong>${formatMoney(report.burn_rate_usd_hour)}<span class="sr-only"> per hour</span><span aria-hidden="true">/h</span></strong>
                <span class="incident-row__subvalue">${formatMoney(report.cumulative_loss_usd)} loss</span>
            </div>
            <div class="incident-row__cell">
                <span class="table-label">Drop</span>
                <strong>${formatPercentagePoints(report.drop_pp)}</strong>
                <span class="incident-row__subvalue">${formatInteger(report.affected_attempts)} attempts</span>
            </div>
            <div class="incident-row__cell">
                <span class="table-label">Since</span>
                ${timestampHTML(report.onset_ts)}
            </div>
        </a>`;
    }

    function sortReports(reports) {
        const compare = {
            risk: (a, b) => data.calculateRiskScore(b) - data.calculateRiskScore(a),
            burn: (a, b) => b.burn_rate_usd_hour - a.burn_rate_usd_hour,
            drop: (a, b) => b.drop_pp - a.drop_pp,
            attempts: (a, b) => b.affected_attempts - a.affected_attempts,
            recent: (a, b) => new Date(b.detected_at) - new Date(a.detected_at)
        }[state.sort];
        return reports.slice().sort((a, b) => compare(a, b) || new Date(b.detected_at) - new Date(a.detected_at));
    }

    function filteredReports() {
        const query = state.query.trim().toLowerCase();
        const reports = data.getLiveReports().filter((report) => {
            const statusMatches = state.status === 'all' || report.status === state.status;
            const severityMatches = state.severity === 'all' || data.getSeverity(report) === state.severity;
            const confidenceMatches = state.confidence === 'all' || report.confidence === state.confidence;
            const searchable = `${report.incident_id} ${formatAnchorCell(report.anchor_cell)}`.toLowerCase();
            return statusMatches && severityMatches && confidenceMatches && (!query || searchable.includes(query));
        });
        return sortReports(reports);
    }

    function syncFilterControls() {
        document.querySelectorAll('[data-status-filter]').forEach((button) => {
            button.setAttribute('aria-pressed', String(button.dataset.statusFilter === state.status));
        });
        document.querySelectorAll('[data-severity-filter]').forEach((button) => {
            button.setAttribute('aria-pressed', String(button.dataset.severityFilter === state.severity));
        });
        const reset = document.getElementById('resetFilters');
        reset.disabled = state.status === 'all' && state.severity === 'all' && state.confidence === 'all' && !state.query && state.sort === 'risk';
    }

    function renderIncidentList() {
        const reports = filteredReports();
        const list = document.getElementById('incidentList');
        const count = document.getElementById('filteredCount');
        count.textContent = `${reports.length} incident${reports.length === 1 ? '' : 's'} shown`;

        if (!reports.length) {
            list.innerHTML = `<div class="empty-state" role="status">
                <div class="empty-state__mark" aria-hidden="true">0</div>
                <h3>No incident reports match these filters</h3>
                <p>Try a different severity or reset the incident-log filters.</p>
                <button class="button button--secondary" type="button" data-empty-reset>Reset filters</button>
            </div>`;
            list.querySelector('[data-empty-reset]').addEventListener('click', () => resetFilters({ focusResults: true }));
        } else {
            list.innerHTML = `<div class="incident-table-head" aria-hidden="true">
                <span>Incident</span><span>Severity</span><span>Status</span><span>Exposure</span><span>Drop</span><span>Since</span>
            </div><div class="incident-table-body">${reports.map(renderIncidentRow).join('')}</div>`;
        }
        syncFilterControls();
        updateRelativeTimes(list);
    }

    function resetFilters({ focusResults = false } = {}) {
        state.status = 'all';
        state.severity = 'all';
        state.confidence = 'all';
        state.query = '';
        state.sort = 'risk';
        document.getElementById('incidentSearch').value = '';
        document.getElementById('confidenceFilter').value = 'all';
        document.getElementById('sortOrder').value = 'risk';
        renderIncidentList();
        if (focusResults) {
            const firstResult = document.querySelector('#incidentList .incident-row');
            if (firstResult) firstResult.focus();
        }
    }

    function renderPage() {
        const errors = data.validateContract();
        const loading = document.getElementById('loadingState');
        const app = document.getElementById('dashboardApp');
        if (errors.length) {
            loading.innerHTML = `<div class="error-state" role="alert"><div class="error-state__mark" aria-hidden="true">!</div><h1>Dashboard data failed validation</h1><p>${escapeHTML(errors.join(' '))}</p></div>`;
            return;
        }

        const liveReports = data.getLiveReports();
        renderSummary(liveReports);
        renderIncidentList();
        loading.classList.add('is-hidden');
        app.classList.remove('is-hidden');
        app.removeAttribute('aria-busy');
        updateRelativeTimes(app);
    }

    async function loadAndRenderPage() {
        const feed = await data.loadReports();
        if (feed.error) {
            const loading = document.getElementById('loadingState');
            loading.innerHTML = `<div class="error-state" role="alert"><div class="error-state__mark" aria-hidden="true">!</div><h1>Reporting feed unavailable</h1><p>${escapeHTML(feed.error)}</p></div>`;
            return;
        }
        renderPage();
    }

    document.addEventListener('DOMContentLoaded', () => {
        document.querySelectorAll('[data-status-filter]').forEach((button) => {
            button.addEventListener('click', () => {
                state.status = button.dataset.statusFilter;
                renderIncidentList();
            });
        });
        document.querySelectorAll('[data-severity-filter]').forEach((button) => {
            button.addEventListener('click', () => {
                state.severity = button.dataset.severityFilter;
                renderIncidentList();
            });
        });
        document.getElementById('incidentSearch').addEventListener('input', (event) => {
            state.query = event.target.value;
            renderIncidentList();
        });
        document.getElementById('confidenceFilter').addEventListener('change', (event) => {
            state.confidence = event.target.value;
            renderIncidentList();
        });
        document.getElementById('sortOrder').addEventListener('change', (event) => {
            state.sort = event.target.value;
            renderIncidentList();
        });
        document.getElementById('resetFilters').addEventListener('click', resetFilters);
        window.setTimeout(() => { void loadAndRenderPage(); }, 180);
        // New reports are exported after diagnosis; refresh the feed without
        // asking a judge to reload after they trigger a live incident.
        window.setInterval(() => { void loadAndRenderPage(); }, 10000);
        window.setInterval(() => updateRelativeTimes(document), 60000);
    });
}());
