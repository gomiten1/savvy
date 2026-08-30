/*
 * Canonical incident_reports seed data and dashboard rules.
 *
 * The dashboard consumes the latest revision of each incident for its live
 * view. Detail views can also read every revision for audit and replay.
 */
(function attachDashboardData(global) {
    'use strict';

    const INCIDENT_REPORT_FIELDS = [
        'incident_id', 'revision', 'published_at',
        'anchor_cell', 'metric', 'baseline_rate', 'observed_rate', 'drop_pp',
        'dominant_decline_code', 'baseline_source',
        'onset_ts', 'detected_at', 'detection_latency_s', 'resolved_at', 'status',
        'affected_entities', 'blast_radius', 'affected_attempts',
        'burn_rate_usd_hour', 'cumulative_loss_usd', 'cost_basis',
        'exec_one_liner', 'ops_explanation', 'evidence', 'confidence',
        'recommended_action', 'alternatives_ruled_out'
    ];

    const seedReports = [
        {
            incident_id: 'INC-2026-08-001',
            revision: 1,
            published_at: '2026-08-29T14:07:41Z',
            anchor_cell: { provider: 'P2', country: 'BR' },
            metric: 'attempt_conversion_rate',
            baseline_rate: 0.87,
            observed_rate: 0.19,
            drop_pp: 68.0,
            dominant_decline_code: 'provider_timeout',
            baseline_source: 'hour_of_week',
            onset_ts: '2026-08-29T14:03:00Z',
            detected_at: '2026-08-29T14:06:10Z',
            detection_latency_s: 190,
            resolved_at: null,
            status: 'open',
            affected_entities: [
                { dimension: 'merchant', value: 'M1', share_of_impact: 0.61 },
                { dimension: 'merchant', value: 'M3', share_of_impact: 0.39 }
            ],
            blast_radius: 0.78,
            affected_attempts: 4120,
            burn_rate_usd_hour: 8500,
            cumulative_loss_usd: 1275,
            cost_basis: 'gross',
            exec_one_liner: '$8,500 per hour is at risk on Brazil card volume through Provider P2 since 14:03 UTC.',
            ops_explanation: 'Provider P2 began timing out on Brazilian card traffic at 14:03 UTC. Approval fell from 87% to 19%. Other providers in Brazil kept operating normally, and Provider P2 is healthy in Mexico and Colombia. This points to Provider P2’s Brazil route.',
            evidence: [
                { claim: 'The decline is confined to Provider P2 in Brazil.', support: 'P1 and P3 in Brazil remained between 86% and 88% during the same window.' },
                { claim: 'This is a provider issue rather than an issuing-bank issue.', support: 'Timeout errors from Provider P2 rose from 4% to 81% of observed declines.' },
                { claim: 'The problem is not country-wide.', support: 'Provider P2 approval in Mexico and Colombia remained at 88% and 86%.' }
            ],
            confidence: 'high',
            recommended_action: 'Route Brazil card traffic away from Provider P2 and open a severity-1 ticket with the provider citing the timeout spike from 14:03 UTC.',
            alternatives_ruled_out: [
                'Brazil-wide issue: other providers in Brazil are healthy.',
                'Issuing-bank outage: declines are spread across issuers rather than concentrated at one bank.',
                'Merchant-specific failure: M1 and M3 are affected in proportion to their volume.'
            ],
            revision_summary: 'Initial diagnosis published.'
        },
        {
            incident_id: 'INC-2026-08-002',
            revision: 1,
            published_at: '2026-08-29T09:18:00Z',
            anchor_cell: { bank: 'Caixa', merchant: 'Merchant C' },
            metric: 'attempt_conversion_rate',
            baseline_rate: 0.942,
            observed_rate: 0.888,
            drop_pp: 5.4,
            dominant_decline_code: 'issuer_unavailable',
            baseline_source: 'all_time',
            onset_ts: '2026-08-29T09:10:00Z',
            detected_at: '2026-08-29T09:14:00Z',
            detection_latency_s: 240,
            resolved_at: null,
            status: 'open',
            affected_entities: [
                { dimension: 'provider', value: 'P1', share_of_impact: 0.56 },
                { dimension: 'country', value: 'BR', share_of_impact: 0.44 }
            ],
            blast_radius: 0.46,
            affected_attempts: 2840,
            burn_rate_usd_hour: 3200,
            cumulative_loss_usd: 426,
            cost_basis: 'gross',
            exec_one_liner: '$3,200 per hour is at risk on Caixa payments for Merchant C since 09:10 UTC.',
            ops_explanation: 'Caixa is temporarily unavailable more often for Merchant C card payments. The incident remains open while Caixa investigates.',
            evidence: [
                { claim: 'The issue is concentrated at Caixa for Merchant C.', support: 'Temporary-unavailable errors account for 63% of failed attempts on this route.' }
            ],
            confidence: 'high',
            recommended_action: 'Ask Caixa for a status update, attach the route data, and prepare a fallback if the error rate continues.',
            alternatives_ruled_out: [
                'Provider-wide issue: other Caixa routes did not show the same failure rate.',
                'Merchant checkout change: Merchant C deployed no checkout changes during the incident window.'
            ],
            revision_summary: 'Initial diagnosis published.'
        },
        {
            incident_id: 'INC-2026-08-002',
            revision: 2,
            published_at: '2026-08-29T12:35:00Z',
            anchor_cell: { bank: 'Caixa', merchant: 'Merchant C' },
            metric: 'attempt_conversion_rate',
            baseline_rate: 0.942,
            observed_rate: 0.888,
            drop_pp: 5.4,
            dominant_decline_code: 'issuer_unavailable',
            baseline_source: 'all_time',
            onset_ts: '2026-08-29T09:10:00Z',
            detected_at: '2026-08-29T09:14:00Z',
            detection_latency_s: 240,
            resolved_at: '2026-08-29T12:30:00Z',
            status: 'resolved',
            affected_entities: [
                { dimension: 'provider', value: 'P1', share_of_impact: 0.56 },
                { dimension: 'country', value: 'BR', share_of_impact: 0.44 }
            ],
            blast_radius: 0.46,
            affected_attempts: 2840,
            burn_rate_usd_hour: 3200,
            cumulative_loss_usd: 2360,
            cost_basis: 'net_of_retry_recovery',
            exec_one_liner: '$3,200 per hour was at risk on Caixa payments for Merchant C from 09:10 UTC until the issuer recovered.',
            ops_explanation: 'Caixa was temporarily unavailable more often for Merchant C card payments. Caixa recovered at 12:30 UTC and approval returned to its expected range. Loss after retry recovery is excluded from the total.',
            evidence: [
                { claim: 'The issue was concentrated at Caixa for Merchant C.', support: 'Temporary-unavailable errors represented 63% of failed attempts on this route.' },
                { claim: 'The incident recovered.', support: 'Observed approval returned to 94.0% after 12:30 UTC.' }
            ],
            confidence: 'high',
            recommended_action: 'Keep the incident closed, attach the issuer recovery confirmation, and monitor the same cell for recurrence.',
            alternatives_ruled_out: [
                'Provider-wide issue: other Caixa routes did not show the same failure rate.',
                'Merchant checkout change: Merchant C deployed no checkout changes during the incident window.'
            ],
            revision_summary: 'Resolution report published.'
        },
        {
            incident_id: 'INC-2026-08-003',
            revision: 1,
            published_at: '2026-08-29T13:05:00Z',
            anchor_cell: { provider: 'P3', country: 'MX' },
            metric: 'attempt_conversion_rate',
            baseline_rate: 0.903,
            observed_rate: 0.869,
            drop_pp: 3.4,
            dominant_decline_code: null,
            baseline_source: 'hour_of_week',
            onset_ts: '2026-08-29T12:40:00Z',
            detected_at: '2026-08-29T13:02:00Z',
            detection_latency_s: 1320,
            resolved_at: null,
            status: 'open',
            affected_entities: [
                { dimension: 'merchant', value: 'M4', share_of_impact: 0.52 },
                { dimension: 'merchant', value: 'M6', share_of_impact: 0.48 }
            ],
            blast_radius: 0.72,
            affected_attempts: 1930,
            burn_rate_usd_hour: 3900,
            cumulative_loss_usd: 585,
            cost_basis: 'gross',
            exec_one_liner: '$3,900 per hour is at risk on Mexico traffic through Provider P3, but the diagnosis does not yet have enough evidence to name a cause.',
            ops_explanation: 'Approval fell 3.4 percentage points on the Provider P3 Mexico route. No error code or affected group gives a clear enough pattern to name the cause. The team should gather more data before changing traffic.',
            evidence: [
                { claim: 'The approval decline is unlikely to be normal variation.', support: 'The observed rate is outside the expected hour-of-week range.' },
                { claim: 'No root cause has been isolated.', support: 'Error codes and affected merchants do not show one clear pattern.' }
            ],
            confidence: 'insufficient_evidence',
            recommended_action: 'Keep monitoring and collect another 30 minutes of route and error-code data before changing traffic.',
            alternatives_ruled_out: [],
            revision_summary: 'Evidence-limited investigation published.'
        },
        {
            incident_id: 'INC-2026-08-004',
            revision: 1,
            published_at: '2026-08-29T15:50:00Z',
            anchor_cell: { bank: 'Caixa', merchant: 'Merchant C' },
            metric: 'attempt_conversion_rate',
            baseline_rate: 0.941,
            observed_rate: 0.891,
            drop_pp: 5.0,
            dominant_decline_code: 'issuer_unavailable',
            baseline_source: 'inherited_from_parent',
            onset_ts: '2026-08-29T15:40:00Z',
            detected_at: '2026-08-29T15:47:00Z',
            detection_latency_s: 420,
            resolved_at: null,
            status: 'open',
            affected_entities: [
                { dimension: 'provider', value: 'P1', share_of_impact: 0.58 },
                { dimension: 'country', value: 'BR', share_of_impact: 0.42 }
            ],
            blast_radius: 0.50,
            affected_attempts: 1960,
            burn_rate_usd_hour: 3300,
            cumulative_loss_usd: 825,
            cost_basis: 'net_of_retry_recovery',
            exec_one_liner: '$3,300 per hour is at risk again on Caixa payments for Merchant C, matching the route that recovered earlier today.',
            ops_explanation: 'The same Caixa and Merchant C payment route that recovered earlier is again temporarily unavailable. Its error pattern and affected-provider mix match INC-2026-08-002, so the dashboard marks this as a repeat incident rather than a separate failure.',
            evidence: [
                { claim: 'The same payment route has failed again.', support: 'The bank, merchant, error code, and provider mix match INC-2026-08-002.' },
                { claim: 'The new failure resembles the resolved one.', support: 'Temporary-unavailable errors account for 61% of failed attempts, close to the earlier 63%.' }
            ],
            confidence: 'high',
            recommended_action: 'Escalate the recurrence to Caixa with the earlier incident reference and prepare a temporary fallback route for Merchant C.',
            alternatives_ruled_out: [
                'A new merchant regression: Merchant C has not deployed since the resolved incident.',
                'A provider-wide outage: the issue remains concentrated in the Caixa and Merchant C cell.'
            ],
            repeat_of_incident_id: 'INC-2026-08-002',
            revision_summary: 'Repeat incident published.'
        },
        {
            incident_id: 'INC-2026-08-005',
            revision: 1,
            published_at: '2026-08-29T16:25:00Z',
            anchor_cell: { bank: 'Bradesco', merchant: 'Merchant B' },
            metric: 'attempt_conversion_rate',
            baseline_rate: 0.941,
            observed_rate: 0.902,
            drop_pp: 3.9,
            dominant_decline_code: 'card_expired',
            baseline_source: 'all_time',
            onset_ts: '2026-08-29T16:00:00Z',
            detected_at: '2026-08-29T16:20:00Z',
            detection_latency_s: 1200,
            resolved_at: null,
            status: 'open',
            affected_entities: [
                { dimension: 'provider', value: 'P3', share_of_impact: 0.67 },
                { dimension: 'country', value: 'BR', share_of_impact: 0.33 }
            ],
            blast_radius: 0.22,
            affected_attempts: 4200,
            burn_rate_usd_hour: 1400,
            cumulative_loss_usd: 350,
            cost_basis: 'gross',
            exec_one_liner: '$1,400 per hour is at risk on Merchant B payments through Bradesco; the first diagnosis is provisional.',
            ops_explanation: 'The first investigation found more card-expired errors after 16:00 UTC. It could not yet tell whether Merchant B changed its payment form or Bradesco changed its checks, so the report was published with medium confidence.',
            evidence: [
                { claim: 'Card-expired errors increased.', support: 'The error rose from 0.8% to 4.1% of Bradesco attempts.' }
            ],
            confidence: 'medium',
            recommended_action: 'Check Merchant B form-change logs and ask Bradesco whether validation rules changed at 16:00 UTC.',
            alternatives_ruled_out: [
                'Provider P3 platform outage: other Bradesco merchant routes remain healthy.'
            ],
            revision_summary: 'Initial diagnosis: merchant form change or issuer validation change.'
        },
        {
            incident_id: 'INC-2026-08-005',
            revision: 2,
            published_at: '2026-08-29T17:10:00Z',
            anchor_cell: { bank: 'Bradesco', merchant: 'Merchant B' },
            metric: 'attempt_conversion_rate',
            baseline_rate: 0.941,
            observed_rate: 0.892,
            drop_pp: 4.9,
            dominant_decline_code: 'card_expired',
            baseline_source: 'all_time',
            onset_ts: '2026-08-29T16:00:00Z',
            detected_at: '2026-08-29T16:20:00Z',
            detection_latency_s: 1200,
            resolved_at: null,
            status: 'open',
            affected_entities: [
                { dimension: 'provider', value: 'P3', share_of_impact: 0.67 },
                { dimension: 'country', value: 'BR', share_of_impact: 0.33 }
            ],
            blast_radius: 0.49,
            affected_attempts: 5100,
            burn_rate_usd_hour: 3200,
            cumulative_loss_usd: 2140,
            cost_basis: 'gross',
            exec_one_liner: '$3,200 per hour is at risk on Merchant B payments through Bradesco because the issuer changed card-expiration validation.',
            ops_explanation: 'Bradesco changed its card-expiration check at 16:00 UTC to require MM/YY rather than MM/YYYY. Merchant B had not updated its form. Test cards fail with a four-digit year and pass with a two-digit year, which rules out the earlier theory that Merchant B made a change.',
            evidence: [
                { claim: 'Card-expired errors rose materially.', support: 'The error increased from 0.8% to 6.1% of Bradesco attempts after 16:00 UTC.' },
                { claim: 'The earlier Merchant B change theory was ruled out.', support: 'Merchant B form code is unchanged and test cards reproduce the issue by expiration format.' },
                { claim: 'Bradesco changed its card-expiration check.', support: 'Bradesco documentation updated at 15:58 UTC and the failure starts at 16:00 UTC.' }
            ],
            confidence: 'high',
            recommended_action: 'Update Merchant B validation to accept both expiration formats and coordinate a temporary fix with P3 if the change cannot ship within four hours.',
            alternatives_ruled_out: [
                'Merchant deployment: form code has not changed since August 15.',
                'Provider P3 backend change: there were no related processing changes since August 28.'
            ],
            revision_summary: 'Root cause confirmed: Bradesco expiration-format validation changed.'
        }
    ];

    let reports = seedReports;

    const DEFAULT_FEED_URL = 'data/dashboard-reports.json';
    const feedState = {
        source: 'seed',
        error: null,
        url: DEFAULT_FEED_URL
    };

    function setReports(nextReports) {
        if (!Array.isArray(nextReports)) throw new TypeError('The reporting feed must contain a reports array.');
        reports = nextReports;
        return reports;
    }

    function getFeedState() {
        return { ...feedState };
    }

    async function loadReports({ url = DEFAULT_FEED_URL, fetchImpl = global.fetch } = {}) {
        if (typeof fetchImpl !== 'function') return getFeedState();

        try {
            const response = await fetchImpl(url, { cache: 'no-store' });
            // A static dashboard remains demoable before the workflow has
            // produced its first report. Any other failure must be visible
            // instead of silently presenting stale demo data as live data.
            if (response.status === 404) {
                setReports(seedReports);
                feedState.source = 'seed';
                feedState.error = null;
                feedState.url = url;
                return getFeedState();
            }
            if (!response.ok) throw new Error(`Reporting feed returned HTTP ${response.status}.`);

            const payload = await response.json();
            const nextReports = Array.isArray(payload) ? payload : payload && payload.reports;
            if (!Array.isArray(nextReports)) throw new Error('Reporting feed is missing its reports array.');

            setReports(nextReports);
            feedState.source = 'published';
            feedState.error = null;
            feedState.url = url;
        } catch (error) {
            feedState.source = 'error';
            feedState.error = error instanceof Error ? error.message : 'Reporting feed could not be loaded.';
            feedState.url = url;
        }
        return getFeedState();
    }

    const confidenceCaps = {
        high: 100,
        medium: 74,
        insufficient_evidence: 49
    };

    const severityMeta = {
        S1: { label: 'S1 · Critical', shortLabel: 'S1', description: 'Immediate 24/7 response', className: 'severity-s1' },
        S2: { label: 'S2 · Major', shortLabel: 'S2', description: 'Immediate 24/7 response', className: 'severity-s2' },
        S3: { label: 'S3 · Moderate', shortLabel: 'S3', description: 'Business-hours response', className: 'severity-s3' },
        Monitoring: { label: 'Monitoring', shortLabel: 'Monitor', description: 'Monitor and investigate', className: 'severity-monitoring' }
    };

    function calculateRiskScore(report) {
        const financial = Math.min(100, (report.burn_rate_usd_hour / 5000) * 100);
        const scope = Math.min(100, report.blast_radius * 100);
        const rawScore = (0.65 * financial) + (0.35 * scope);
        const cap = confidenceCaps[report.confidence];
        return Math.min(rawScore, cap);
    }

    function getSeverity(report) {
        const score = calculateRiskScore(report);
        if (score >= 90) return 'S1';
        if (score >= 70) return 'S2';
        if (score >= 40) return 'S3';
        return 'Monitoring';
    }

    function getReportsForIncident(incidentId) {
        return reports
            .filter((report) => report.incident_id === incidentId)
            .slice()
            .sort((a, b) => b.revision - a.revision);
    }

    function getLiveReports() {
        const latestByIncident = new Map();
        reports.forEach((report) => {
            const current = latestByIncident.get(report.incident_id);
            if (!current || report.revision > current.revision) latestByIncident.set(report.incident_id, report);
        });

        return Array.from(latestByIncident.values()).sort((a, b) => {
            const aOpen = a.status === 'open' ? 1 : 0;
            const bOpen = b.status === 'open' ? 1 : 0;
            if (aOpen !== bOpen) return bOpen - aOpen;
            return calculateRiskScore(b) - calculateRiskScore(a);
        });
    }

    function isNonEmptyString(value) {
        return typeof value === 'string' && value.trim().length > 0;
    }

    function anchorCellsMatch(first, second) {
        if (!first || !second || typeof first !== 'object' || typeof second !== 'object') return false;
        const firstKeys = Object.keys(first).sort();
        const secondKeys = Object.keys(second).sort();
        return firstKeys.length === secondKeys.length && firstKeys.every((key, index) => key === secondKeys[index] && first[key] === second[key]);
    }

    function validateContract() {
        const errors = [];
        const seenRevisions = new Set();
        const severityForConfidence = { insufficient_evidence: ['S1', 'S2'], medium: ['S1'] };

        reports.forEach((report) => {
            INCIDENT_REPORT_FIELDS.forEach((field) => {
                if (!(field in report)) errors.push(`${report.incident_id} r${report.revision} is missing ${field}`);
            });

            const revisionKey = `${report.incident_id}:${report.revision}`;
            if (seenRevisions.has(revisionKey)) errors.push(`Duplicate revision ${revisionKey}`);
            seenRevisions.add(revisionKey);

            if (!isNonEmptyString(report.incident_id)) errors.push(`${revisionKey} has invalid incident_id`);
            if (!['open', 'resolved'].includes(report.status)) errors.push(`${revisionKey} has invalid status`);
            if (!Object.prototype.hasOwnProperty.call(confidenceCaps, report.confidence)) errors.push(`${revisionKey} has invalid confidence`);
            if (!['gross', 'net_of_retry_recovery'].includes(report.cost_basis)) errors.push(`${revisionKey} has invalid cost_basis`);
            if (report.metric !== 'attempt_conversion_rate') errors.push(`${revisionKey} has invalid metric`);
            if (!['hour_of_week', 'all_time', 'inherited_from_parent', 'none'].includes(report.baseline_source)) errors.push(`${revisionKey} has invalid baseline_source`);
            if (!Number.isInteger(report.revision) || report.revision < 1) errors.push(`${revisionKey} has invalid revision`);
            ['baseline_rate', 'observed_rate', 'drop_pp', 'blast_radius', 'burn_rate_usd_hour', 'cumulative_loss_usd'].forEach((field) => {
                if (!Number.isFinite(report[field])) errors.push(`${revisionKey} has nonnumeric ${field}`);
            });
            ['detection_latency_s', 'affected_attempts'].forEach((field) => {
                if (!Number.isInteger(report[field]) || report[field] < 0) errors.push(`${revisionKey} has invalid ${field}`);
            });
            if (report.baseline_rate < 0 || report.baseline_rate > 1 || report.observed_rate < 0 || report.observed_rate > 1 || report.blast_radius < 0 || report.blast_radius > 1) errors.push(`${revisionKey} has an out-of-range rate or blast radius`);
            if (report.drop_pp < 0 || report.burn_rate_usd_hour < 0 || report.cumulative_loss_usd < 0) errors.push(`${revisionKey} has a negative drop or money value`);
            const isDimensionValue = (value) => value === null || isNonEmptyString(value);
            const anchorIsValid = report.anchor_cell && typeof report.anchor_cell === 'object' && !Array.isArray(report.anchor_cell) && Object.keys(report.anchor_cell).length > 0 && Object.entries(report.anchor_cell).every(([dimension, value]) => isNonEmptyString(dimension) && isDimensionValue(value));
            if (!anchorIsValid) errors.push(`${revisionKey} has invalid anchor_cell`);

            const affectedEntitiesAreValid = Array.isArray(report.affected_entities) && report.affected_entities.length > 0 && report.affected_entities.every((entity) => entity && typeof entity === 'object' && isNonEmptyString(entity.dimension) && isDimensionValue(entity.value) && Number.isFinite(entity.share_of_impact) && entity.share_of_impact >= 0 && entity.share_of_impact <= 1);
            if (!affectedEntitiesAreValid) {
                errors.push(`${revisionKey} has invalid affected_entities`);
            }
            if (affectedEntitiesAreValid && Math.abs(report.affected_entities.reduce((total, entity) => total + entity.share_of_impact, 0) - 1) > 0.0001) errors.push(`${revisionKey} affected-entity shares do not sum to 1`);
            if (!Array.isArray(report.evidence) || report.evidence.some((item) => !item || typeof item !== 'object' || !isNonEmptyString(item.claim) || !isNonEmptyString(item.support))) errors.push(`${revisionKey} has invalid evidence`);
            if (!Array.isArray(report.alternatives_ruled_out) || report.alternatives_ruled_out.some((item) => !isNonEmptyString(item))) errors.push(`${revisionKey} has invalid alternatives_ruled_out`);
            if (typeof report.exec_one_liner !== 'string' || !/^\$[\d,]+(?:\.\d+)?\s/.test(report.exec_one_liner)) errors.push(`${revisionKey} exec_one_liner is not money-first`);
            if (!isNonEmptyString(report.ops_explanation)) errors.push(`${revisionKey} has invalid ops_explanation`);
            if (report.recommended_action !== null && report.recommended_action !== undefined && !isNonEmptyString(report.recommended_action)) errors.push(`${revisionKey} has invalid recommended_action`);

            const onset = new Date(report.onset_ts);
            const detected = new Date(report.detected_at);
            const published = new Date(report.published_at);
            const resolved = report.resolved_at ? new Date(report.resolved_at) : null;
            if (![report.onset_ts, report.detected_at, report.published_at].every(isNonEmptyString) || (report.resolved_at && !isNonEmptyString(report.resolved_at))) errors.push(`${revisionKey} has a non-string timestamp`);
            if ([onset, detected, published, resolved].filter(Boolean).some((date) => Number.isNaN(date.getTime()))) errors.push(`${revisionKey} has an invalid timestamp`);
            if ((detected - onset) / 1000 !== report.detection_latency_s) errors.push(`${revisionKey} has inconsistent detection_latency_s`);
            if (published < detected) errors.push(`${revisionKey} was published before detection`);
            if (report.status === 'resolved' && !report.resolved_at) errors.push(`${revisionKey} is resolved without resolved_at`);
            if (report.status === 'open' && report.resolved_at) errors.push(`${revisionKey} is open with resolved_at`);
            if (report.status === 'resolved' && published < resolved) errors.push(`${revisionKey} was published before resolution`);
            if (report.status === 'resolved' && resolved < detected) errors.push(`${revisionKey} resolved before detection`);
            if (report.status === 'resolved' && !reports.some((prior) => prior.incident_id === report.incident_id && prior.revision < report.revision && prior.status === 'open')) errors.push(`${revisionKey} has no prior open revision before resolution`);

            const severity = getSeverity(report);
            if ((severityForConfidence[report.confidence] || []).includes(severity)) errors.push(`${revisionKey} breaks the confidence severity cap`);
        });

        Array.from(new Set(reports.map((report) => report.incident_id))).forEach((incidentId) => {
            const history = getReportsForIncident(incidentId).slice().sort((first, second) => first.revision - second.revision);
            history.forEach((report, index) => {
                const previous = history[index - 1];
                if (report.revision !== index + 1) errors.push(`${incidentId} revisions must be sequential from 1`);
                if (previous && new Date(report.published_at) <= new Date(previous.published_at)) errors.push(`${incidentId} revisions are not in publication order`);
            });
        });

        const live = getLiveReports();
        live.filter((report) => report.repeat_of_incident_id).forEach((repeat) => {
            const original = live.find((report) => report.incident_id === repeat.repeat_of_incident_id);
            if (!original || !anchorCellsMatch(original.anchor_cell, repeat.anchor_cell)) errors.push(`${repeat.incident_id} is not a repeat of the referenced anchor cell`);
        });

        return errors;
    }

    const dashboardData = {
        INCIDENT_REPORT_FIELDS,
        get reports() { return reports; },
        confidenceCaps,
        severityMeta,
        setReports,
        loadReports,
        getFeedState,
        calculateRiskScore,
        getSeverity,
        getReportsForIncident,
        getLiveReports,
        validateContract
    };

    global.SavvyDashboardData = dashboardData;
    if (typeof module !== 'undefined' && module.exports) module.exports = dashboardData;
}(typeof window !== 'undefined' ? window : globalThis));
