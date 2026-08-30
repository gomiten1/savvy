(function renderIncidentDetail() {
    'use strict';

    const data = window.SavvyDashboardData;
    const utils = window.SavvyDashboardUtils;
    const {
        escapeHTML,
        formatMoney,
        formatPercent,
        formatPercentagePoints,
        formatRiskScore,
        formatInteger,
        formatDuration,
        timestampHTML,
        updateRelativeTimes,
        formatAnchorCell,
        formatMetric,
        formatBaselineSource,
        formatDeclineCode,
        formatCostBasis,
        formatConfidence,
        formatStatus,
        formatEntity
    } = utils;

    const severityClass = { S1: 's1', S2: 's2', S3: 's3', Monitoring: 'monitoring' };

    function setFeedStatus(message, hasError = false) {
        const status = document.getElementById('feedStatus');
        if (!status) return;
        status.textContent = message;
        status.classList.toggle('system-status--error', hasError);
    }

    function getReportMeta(report) {
        const severity = data.getSeverity(report);
        return {
            severity,
            score: data.calculateRiskScore(report),
            meta: data.severityMeta[severity]
        };
    }

    function recordRow(label, value, modifier = '') {
        return `<div class="record-row"><dt class="record-key">${escapeHTML(label)}</dt><dd class="record-value ${modifier}">${value}</dd></div>`;
    }

    function severityBadge(report) {
        const details = getReportMeta(report);
        return `<span class="badge badge--${severityClass[details.severity]}">${escapeHTML(details.meta.label)}</span>`;
    }

    function confidenceBadge(report) {
        const type = report.confidence === 'insufficient_evidence' ? 'limited' : report.confidence;
        return `<span class="badge badge--${type}">${escapeHTML(formatConfidence(report.confidence))}</span>`;
    }

    function revisionHistory(reports, selected) {
        return reports
            .slice()
            .sort((a, b) => a.revision - b.revision)
            .map((report) => {
                const active = report.revision === selected.revision;
                const href = `incident-detail.html?id=${encodeURIComponent(report.incident_id)}&revision=${report.revision}`;
                return `<li class="revision-item">
                    <div class="revision-item__header">
                        <div class="revision-item__copy">
                            <h3 class="revision-title">Revision ${report.revision}</h3>
                            <p class="revision-meta">${escapeHTML(report.revision_summary || 'Report published.')}</p>
                        </div>
                        <div class="revision-item__badges">${severityBadge(report)} ${confidenceBadge(report)}</div>
                    </div>
                    <div class="revision-item__footer">
                        <div class="revision-item__time">${timestampHTML(report.published_at)}</div>
                        ${active ? '<span class="state-tag">Current view</span>' : `<a class="text-button" href="${href}">View revision ${report.revision}</a>`}
                    </div>
                </li>`;
            }).join('');
    }

    function evidenceList(evidence) {
        if (!evidence.length) return '<li class="empty-list">No supporting evidence has been published yet.</li>';
        return evidence.map((item) => `<li class="evidence-item"><p class="evidence-claim">${escapeHTML(item.claim)}</p><p class="evidence-support">${escapeHTML(item.support)}</p></li>`).join('');
    }

    function entityList(entities) {
        return entities.map((entity) => `<li class="entity-item"><span class="entity-item__name">${escapeHTML(formatEntity(entity))}</span><span class="entity-item__share">${formatPercent(entity.share_of_impact)} share of impact</span></li>`).join('');
    }

    function renderDetail(report, reports) {
        const meta = getReportMeta(report);
        const route = formatAnchorCell(report.anchor_cell);
        const isHistorical = report.revision !== reports[0].revision;
        const moneyHeading = report.status === 'open' ? 'Money at risk' : 'Final burn rate';
        const moneyDescription = report.status === 'open'
            ? `${formatMoney(report.cumulative_loss_usd)} reported loss.`
            : `${formatMoney(report.cumulative_loss_usd)} final reported loss.`;
        const repeatNotice = report.repeat_of_incident_id ? `<div class="callout"><h3 class="callout-title">Repeat incident</h3><p class="callout-copy">Matches <a class="text-button" href="incident-detail.html?id=${encodeURIComponent(report.repeat_of_incident_id)}">${escapeHTML(report.repeat_of_incident_id)}</a>.</p></div>` : '';
        const evidenceNotice = report.confidence === 'insufficient_evidence' ? `<div class="callout callout--limited"><h3 class="callout-title">Evidence limited</h3><p class="callout-copy">Cause unconfirmed. Severity is capped at S3.</p></div>` : '';
        const alternatives = report.alternatives_ruled_out.length
            ? `<ul class="plain-list">${report.alternatives_ruled_out.map((item) => `<li>${escapeHTML(item)}</li>`).join('')}</ul>`
            : '<div class="empty-list">None.</div>';
        const recommendedAction = report.recommended_action || 'No recommended action has been published.';

        return `
            <a class="breadcrumb" href="index.html" aria-label="Back to incident log">← Back to incident log</a>
            <article class="detail-hero" aria-labelledby="incident-title">
                <div class="detail-heading-row">
                    <div>
                        <p class="eyebrow">${isHistorical ? 'Previous version' : 'Current incident'}</p>
                        <h1 id="incident-title" class="detail-title">${escapeHTML(route)}</h1>
                        <div class="detail-subtitle"><span class="detail-id">${escapeHTML(report.incident_id)}</span><span>·</span><span>Revision ${report.revision} of ${reports.length}</span></div>
                    </div>
                    <div class="inline-stack"><button id="copyIncidentId" class="button button--secondary" type="button" data-incident-id="${escapeHTML(report.incident_id)}">Copy report ID</button><span id="copyStatus" class="sr-only" aria-live="polite"></span></div>
                </div>
                <div class="detail-badges">
                    <span class="badge badge--${report.status === 'open' ? 'open' : 'resolved'}">${escapeHTML(formatStatus(report.status))}</span>
                    ${severityBadge(report)}
                    ${confidenceBadge(report)}
                    ${isHistorical ? '<span class="state-tag">Historical view</span>' : '<span class="state-tag">Latest revision</span>'}
                </div>
                <div class="risk-callout">
                    <div>
                        <div class="risk-callout__money">${formatMoney(report.burn_rate_usd_hour)}<span class="sr-only"> per hour</span><span aria-hidden="true">/h</span></div>
                        <p class="risk-callout__label">${moneyHeading}</p>
                        <p class="risk-callout__subtext">${moneyDescription}</p>
                    </div>
                    <div class="risk-callout__severity">
                        ${severityBadge(report)}
                        <span class="risk-score">Risk score ${formatRiskScore(meta.score)}/100</span>
                        <span class="topbar-note">${escapeHTML(meta.meta.description)}</span>
                    </div>
                </div>
                <div class="exec-summary">
                    <p class="field-label">Summary</p>
                    <p class="exec-summary__copy">${escapeHTML(report.exec_one_liner)}</p>
                </div>
            </article>

            <section class="detail-overview" aria-label="Incident summary metrics">
                <article class="metric-card"><span class="metric-label">Conversion drop</span><strong class="metric-value">${formatPercentagePoints(report.drop_pp)}</strong><span class="topbar-note">${formatPercent(report.baseline_rate)} baseline → ${formatPercent(report.observed_rate)} observed</span></article>
                <article class="metric-card"><span class="metric-label">Detection latency</span><strong class="metric-value">${formatDuration(report.detection_latency_s)}</strong><span class="topbar-note">Onset to detection</span></article>
                <article class="metric-card"><span class="metric-label">Route impact</span><strong class="metric-value">${formatPercent(report.blast_radius)}</strong><span class="topbar-note">Route traffic</span></article>
                <article class="metric-card"><span class="metric-label">Affected attempts</span><strong class="metric-value">${formatInteger(report.affected_attempts)}</strong><span class="topbar-note">Incident window</span></article>
            </section>

            <div class="detail-layout">
                <div class="detail-column">
                    <section class="section-card" aria-labelledby="what-dropped-title">
                        <div class="section-card__heading"><h2 id="what-dropped-title">What dropped</h2></div>
                        <dl class="record-grid">
                            ${recordRow('Affected route (anchor cell)', escapeHTML(route))}
                            ${recordRow('Metric', escapeHTML(formatMetric(report.metric)))}
                            ${recordRow('Baseline rate', formatPercent(report.baseline_rate))}
                            ${recordRow('Observed rate', formatPercent(report.observed_rate))}
                            ${recordRow('Absolute drop', formatPercentagePoints(report.drop_pp))}
                            ${recordRow('Dominant decline code', report.dominant_decline_code ? escapeHTML(formatDeclineCode(report.dominant_decline_code)) : '<span class="record-value--muted">No dominant code identified</span>')}
                            ${recordRow('Baseline source', escapeHTML(formatBaselineSource(report.baseline_source)))}
                        </dl>
                    </section>

                    <section class="section-card" aria-labelledby="since-when-title">
                        <div class="section-card__heading"><h2 id="since-when-title">Since when</h2></div>
                        <dl class="record-grid">
                            ${recordRow('Actual onset', timestampHTML(report.onset_ts))}
                            ${recordRow('Detected at', timestampHTML(report.detected_at))}
                            ${recordRow('Detection latency', `${formatDuration(report.detection_latency_s)}<br><span class="record-value--muted">${formatInteger(report.detection_latency_s)} sec</span>`)}
                            ${recordRow('Resolved at', report.resolved_at ? timestampHTML(report.resolved_at) : '<span class="record-value--muted">Open</span>')}
                        </dl>
                    </section>

                    <section class="section-card" aria-labelledby="why-title">
                        <div class="section-card__heading"><h2 id="why-title">Explanation</h2></div>
                        ${evidenceNotice}
                        <div class="narrative">
                            <p class="field-label narrative-label">Details</p>
                            <p>${escapeHTML(report.ops_explanation)}</p>
                        </div>
                        <div class="callout">
                            <h3 class="callout-title">Confidence</h3>
                            <p class="callout-copy">${escapeHTML(formatConfidence(report.confidence))} · score ${formatRiskScore(meta.score)}/100 · ${escapeHTML(meta.meta.label)}</p>
                        </div>
                        <div class="narrative">
                            <p class="field-label narrative-label">Evidence</p>
                            <ul class="evidence-list">${evidenceList(report.evidence)}</ul>
                        </div>
                        <div class="narrative">
                            <p class="field-label narrative-label">Ruled out</p>
                            ${alternatives}
                        </div>
                        <div class="callout callout--recommendation">
                            <h3 class="callout-title">Recommended action</h3>
                            <p class="callout-copy">${escapeHTML(recommendedAction)}</p>
                        </div>
                    </section>
                </div>

                <aside class="detail-column" aria-label="Incident impact and audit data">
                    <section class="section-card" aria-labelledby="who-title">
                        <div class="section-card__heading"><h2 id="who-title">Who it affects</h2></div>
                        <ul class="entity-list">${entityList(report.affected_entities)}</ul>
                        <dl class="record-grid record-grid--spaced">
                            ${recordRow('Route impact (blast radius)', `${formatPercent(report.blast_radius)} of traffic on this route`)}
                            ${recordRow('Affected attempts', formatInteger(report.affected_attempts))}
                        </dl>
                    </section>

                    <section class="section-card" aria-labelledby="money-title">
                        <div class="section-card__heading"><h2 id="money-title">How much money</h2></div>
                        <dl class="record-grid">
                            ${recordRow('Burn rate', `${formatMoney(report.burn_rate_usd_hour)}/h`, 'record-value--money')}
                            ${recordRow('Cumulative loss', formatMoney(report.cumulative_loss_usd), 'record-value--money')}
                            ${recordRow('Cost basis', escapeHTML(formatCostBasis(report.cost_basis)))}
                        </dl>
                    </section>

                    ${repeatNotice}

                    <section class="section-card" aria-labelledby="report-record-title">
                        <div class="section-card__heading"><h2 id="report-record-title">Report</h2></div>
                        <dl class="record-grid">
                            ${recordRow('Incident ID', `<span class="detail-id">${escapeHTML(report.incident_id)}</span>`)}
                            ${recordRow('Revision', `${report.revision} of ${reports.length}`)}
                            ${recordRow('Published at', timestampHTML(report.published_at))}
                            ${recordRow('Status', escapeHTML(formatStatus(report.status)))}
                        </dl>
                    </section>

                    <section class="section-card" aria-labelledby="history-title">
                        <div class="section-card__heading"><h2 id="history-title">History</h2></div>
                        <ol class="revision-list">${revisionHistory(reports, report)}</ol>
                    </section>
                </aside>
            </div>`;
    }

    function renderError(message) {
        return `<a class="breadcrumb" href="index.html" aria-label="Back to incident log">← Back to incident log</a><section class="error-state" role="alert"><div class="error-state__mark" aria-hidden="true">!</div><h1>Incident report unavailable</h1><p>${escapeHTML(message)}</p><a class="button button--primary" href="index.html">Open incident log</a></section>`;
    }

    function bindCopyButton() {
        const button = document.getElementById('copyIncidentId');
        const status = document.getElementById('copyStatus');
        if (!button) return;
        button.addEventListener('click', async () => {
            const originalText = 'Copy report ID';
            try {
                if (!navigator.clipboard) throw new Error('Clipboard unavailable');
                button.disabled = true;
                button.textContent = 'Copying…';
                status.textContent = 'Copying report ID.';
                await navigator.clipboard.writeText(button.dataset.incidentId);
                button.textContent = 'Copied';
                status.textContent = 'Report ID copied.';
                window.setTimeout(() => {
                    button.textContent = originalText;
                    button.disabled = false;
                }, 1800);
            } catch (error) {
                button.textContent = 'Copy unavailable';
                button.disabled = true;
                status.textContent = 'Copying the report ID is unavailable in this browser.';
            }
        });
    }

    async function renderPage() {
        const container = document.getElementById('detailApp');
        const loading = document.getElementById('loadingState');
        const feed = await data.loadReports();
        if (feed.error) {
            setFeedStatus('Reporting feed unavailable', true);
            loading.classList.add('is-hidden');
            container.innerHTML = renderError(feed.error);
            container.classList.remove('is-hidden');
            container.removeAttribute('aria-busy');
            return;
        }
        const validationErrors = data.validateContract();
        const parameters = new URLSearchParams(window.location.search);
        const incidentId = parameters.get('id');
        const revisionParameter = parameters.get('revision');
        const requestedRevision = revisionParameter === null ? null : Number(revisionParameter);

        if (validationErrors.length) {
            setFeedStatus('Reporting feed needs attention', true);
            loading.classList.add('is-hidden');
            container.innerHTML = renderError(`The dashboard data failed validation: ${validationErrors.join(' ')}`);
            container.classList.remove('is-hidden');
            container.removeAttribute('aria-busy');
            return;
        }

        if (!incidentId) {
            setFeedStatus(feed.source === 'published' ? 'Reporting feed healthy' : 'Demo feed â€” no published reports yet');
            loading.classList.add('is-hidden');
            container.innerHTML = renderError('Choose an incident from the incident log to view its report.');
            container.classList.remove('is-hidden');
            container.removeAttribute('aria-busy');
            return;
        }

        if (revisionParameter !== null && (!Number.isInteger(requestedRevision) || requestedRevision < 1)) {
            loading.classList.add('is-hidden');
            container.innerHTML = renderError(`Revision ${revisionParameter} is not a valid positive revision number.`);
            container.classList.remove('is-hidden');
            container.removeAttribute('aria-busy');
            setFeedStatus('Reporting feed needs attention', true);
            return;
        }

        const reports = data.getReportsForIncident(incidentId);
        const report = requestedRevision !== null
            ? reports.find((item) => item.revision === requestedRevision)
            : reports[0];

        loading.classList.add('is-hidden');
        container.innerHTML = report ? renderDetail(report, reports) : renderError(`No report was found for ${incidentId}${requestedRevision ? ` revision ${requestedRevision}` : ''}.`);
        container.classList.remove('is-hidden');
        container.removeAttribute('aria-busy');
        setFeedStatus(
            report ? (feed.source === 'published' ? 'Reporting feed healthy' : 'Demo feed â€” no published reports yet') : 'Reporting feed needs attention',
            !report
        );
        updateRelativeTimes(container);
        bindCopyButton();
    }

    document.addEventListener('DOMContentLoaded', () => {
        window.setTimeout(() => { void renderPage(); }, 120);
        window.setInterval(() => updateRelativeTimes(document), 60000);
    });
}());
