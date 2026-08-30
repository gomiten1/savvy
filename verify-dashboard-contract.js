/* Run with: node verify-dashboard-contract.js */
'use strict';

const fs = require('fs');
const path = require('path');
const data = require('./dashboard-data.js');
require('./dashboard-utils.js');

const utils = globalThis.SavvyDashboardUtils;
const root = __dirname;
const failures = [];

function check(condition, message) {
    if (!condition) failures.push(message);
}

function read(file) {
    return fs.readFileSync(path.join(root, file), 'utf8');
}

function sum(items, field) {
    return items.reduce((total, item) => total + Number(item[field] || 0), 0);
}

const errors = data.validateContract();
check(errors.length === 0, `Seed-schema validation failed: ${errors.join('; ')}`);

data.reports.forEach((report) => {
    data.INCIDENT_REPORT_FIELDS.forEach((field) => {
        check(Object.prototype.hasOwnProperty.call(report, field), `${report.incident_id} r${report.revision} lacks ${field}`);
    });
    check(!Object.prototype.hasOwnProperty.call(report, 'severity'), `${report.incident_id} r${report.revision} contains a manually supplied severity`);
});

const live = data.getLiveReports();
const open = live.filter((report) => report.status === 'open');
const report005 = live.find((report) => report.incident_id === 'INC-2026-08-005');
const resolved = live.find((report) => report.status === 'resolved');
const limitedEvidence = live.find((report) => report.confidence === 'insufficient_evidence');
const repeat = live.find((report) => report.repeat_of_incident_id);

check(live.length === 5, `Live view should contain five incidents, found ${live.length}`);
check(report005 && report005.revision === 2, 'Live view did not select the highest revision for INC-2026-08-005');
check(data.getReportsForIncident('INC-2026-08-005').length === 2, 'INC-2026-08-005 does not expose two report revisions');
check(open.some((report) => report.confidence === 'high' && report.burn_rate_usd_hour >= 5000 && report.anchor_cell.provider && report.anchor_cell.country), 'Missing open, high-confidence provider × country state');
check(Boolean(resolved && resolved.resolved_at && resolved.cumulative_loss_usd > 0 && resolved.anchor_cell.bank && resolved.anchor_cell.merchant), 'Missing resolved bank × merchant state');
check(Boolean(limitedEvidence && !limitedEvidence.dominant_decline_code && limitedEvidence.alternatives_ruled_out.length === 0), 'Missing evidence-limited state with no named root cause and empty alternatives');
check(Boolean(repeat && repeat.repeat_of_incident_id), 'Missing repeat incident state');
if (repeat) {
    const original = live.find((report) => report.incident_id === repeat.repeat_of_incident_id);
    check(Boolean(original && JSON.stringify(original.anchor_cell) === JSON.stringify(repeat.anchor_cell)), 'Repeat incident does not reuse the referenced anchor cell');
}

function capFixture(confidence) {
    return {
        ...data.reports[0],
        confidence,
        burn_rate_usd_hour: 5000,
        blast_radius: 1
    };
}

const high = capFixture('high');
const medium = capFixture('medium');
const insufficient = capFixture('insufficient_evidence');
check(data.calculateRiskScore(high) === 100 && data.getSeverity(high) === 'S1', 'High confidence should permit S1 at a raw score of 100');
check(data.calculateRiskScore(medium) === 74 && data.getSeverity(medium) === 'S2', 'Medium confidence must cap at 74 and cannot become S1');
check(data.calculateRiskScore(insufficient) === 49 && data.getSeverity(insufficient) === 'S3', 'Insufficient evidence must cap at 49 and cannot become S1 or S2');

const openBurn = sum(open, 'burn_rate_usd_hour');
const openLoss = sum(open, 'cumulative_loss_usd');
check(openBurn === 18900, `Unexpected open-burn total: ${openBurn}`);
check(openLoss === 4825, `Unexpected open cumulative-loss total: ${openLoss}`);
check(utils.formatMoney(openBurn) === '$18,900', 'Summary money formatting should preserve the exact open-burn total');
check(utils.formatMoney(openLoss) === '$4,825', 'Summary loss formatting should preserve the exact open-loss total');
check(utils.formatMoney(1275.5) === '$1,275.50', 'USD cents must not be silently rounded away');
check(utils.formatMoney(0.51 + 0.51) === '$1.02', 'Currency aggregation must preserve cents between cards and summaries');
check(utils.formatAbsoluteTime(live[0].onset_ts).endsWith('UTC'), 'Absolute timestamps must explicitly render in UTC');
const thresholdFixture = { ...data.reports[0], burn_rate_usd_hour: 4192.307692307692, blast_radius: 1, confidence: 'high' };
check(data.getSeverity(thresholdFixture) === 'S2' && utils.formatRiskScore(data.calculateRiskScore(thresholdFixture)) === '89.5', 'Risk-score display must not round an S2 score into the S1 threshold');
check(utils.formatRiskScore(89.999) === '89.99', 'Risk-score display must not round an S2 score into S1 at a boundary');

const mutableReport = data.reports[0];
const originalBurn = mutableReport.burn_rate_usd_hour;
const originalShare = mutableReport.affected_entities[0].share_of_impact;
const originalDrop = mutableReport.drop_pp;
mutableReport.burn_rate_usd_hour = 'not-a-number';
check(data.validateContract().some((error) => error.includes('nonnumeric burn_rate_usd_hour')), 'Schema validation must reject a nonnumeric burn rate');
mutableReport.burn_rate_usd_hour = originalBurn;
mutableReport.affected_entities[0].share_of_impact = 'not-a-number';
check(data.validateContract().some((error) => error.includes('invalid affected_entities')), 'Schema validation must reject a nonnumeric share_of_impact');
mutableReport.affected_entities[0].share_of_impact = originalShare;
mutableReport.burn_rate_usd_hour = 0.335;
check(!data.validateContract().some((error) => error.includes('burn_rate_usd_hour is not cent-precise')), 'Runtime validation must retain deterministic money values without imposing presentation rounding');
mutableReport.burn_rate_usd_hour = originalBurn;
mutableReport.drop_pp = -1;
check(data.validateContract().some((error) => error.includes('negative drop or money value')), 'Schema validation must reject a negative absolute drop');
mutableReport.drop_pp = originalDrop;
check(data.validateContract().length === 0, 'Schema validation did not recover after test mutations');

const index = read('index.html');
const detail = read('incident-detail.html');
const indexScript = read('dashboard-index.js');
const detailScript = read('dashboard-detail.js');
const css = read('dashboard.css');

['dashboard-data.js', 'dashboard-utils.js', 'dashboard.css'].forEach((asset) => {
    check(index.includes(asset), `Dashboard page does not load ${asset}`);
    check(detail.includes(asset), `Detail page does not load ${asset}`);
});

[
    'Incident ID', 'Revision', 'Published at', 'Affected route (anchor cell)', 'Metric', 'Baseline rate',
    'Observed rate', 'Absolute drop', 'Dominant decline code', 'Baseline source',
    'Actual onset', 'Detected at', 'Detection latency', 'Resolved at', 'Status',
    'Route impact (blast radius)', 'Affected attempts', 'Burn rate', 'Cumulative loss', 'Cost basis',
    'Summary', 'Details', 'Evidence', 'Recommended action',
    'Ruled out', 'Confidence', 'History'
].forEach((label) => check(detailScript.includes(label), `Detail renderer does not expose the ${label} field or section`));

check(index.indexOf('id="summaryGrid"') < index.indexOf('id="incident-log"'), 'Dashboard layout must show aggregate summaries before the incident log');
check(indexScript.includes('formatMoney(report.burn_rate_usd_hour)'), 'Incident list rows must use the shared money formatter');
check(indexScript.includes("sum(openReports, 'burn_rate_usd_hour')"), 'Summary must aggregate the current open incident reports directly');
check(detailScript.includes('data.getSeverity(report)') && indexScript.includes('data.getSeverity(report)'), 'Both views must derive severity from the shared confidence-capped rule');
check(!/onclick=|onchange=|oninput=/.test(index + detail + indexScript + detailScript), 'Interactive behavior must not depend on inline event handlers');
check(!indexScript.includes('aria-label="Open details for'), 'Incident-card links must expose their full visible content to assistive technology');
check(!/id="(?:exposurePanel|severityPanel)"[^>]*aria-live/.test(index), 'Static aggregate panels should not be noisy live regions');
check(detailScript.includes("button.textContent = 'Copying…'") && detailScript.includes("status.textContent = 'Report ID copied.'"), 'Copy control must expose a loading and success state');
check(detailScript.includes('is not a valid positive revision number'), 'Invalid revision URLs must render an error instead of silently selecting the latest report');
check(index.includes('lang="en"') && detail.includes('lang="en"'), 'Both dashboard pages must declare English');
check(!/[áéíóúñÁÉÍÓÚÑ]/.test(index + detail + indexScript + detailScript), 'Dashboard UI copy contains non-English accented text');
check(!/id="incidentList"[^>]*aria-live/.test(index), 'The full incident list should not be a live region when timestamps refresh');
check(indexScript.includes('resetFilters({ focusResults: true })') && indexScript.includes("document.querySelector('#incidentList .incident-row')"), 'Resetting an empty filter result must return keyboard focus to a result');
['incidentSearch', 'confidenceFilter', 'sortOrder', 'renderIncidentRow', 'sortReports'].forEach((token) => {
    check(index.includes(token) || indexScript.includes(token), `Incident log is missing ${token}`);
});
check(index.includes('<style>#loadingState { display: none !important; }</style>') && detail.includes('<style>#loadingState { display: none !important; }</style>'), 'No-JavaScript pages must hide the loading skeleton');
check(!/min-width:\s*2px/.test(css), 'Zero-count chart bars must not be given a visible minimum width');
check((css.match(/linear-gradient/g) || []).length === 1 && css.includes('.skeleton'), 'The only gradient must be the purposeful loading skeleton');
check((css.match(/box-shadow/g) || []).length <= 4, 'Ordinary cards must not rely on decorative shadows');
check(detailScript.indexOf('exec-summary') < detailScript.indexOf('detail-overview'), 'The money-first executive summary must appear before the detailed incident sections');
check(detailScript.includes('Recommended action') && !detailScript.includes('Execute recommended action'), 'Recommended actions must remain copy, never executable controls');
['--space-1: 4px', '--space-2: 8px', '--space-4: 16px', '--space-5: 24px', '--space-6: 32px', "font-family: 'Montserrat'", "font-family: 'Roboto'", ':focus-visible', ':disabled', 'prefers-reduced-motion', '.empty-state', '.loading-shell'].forEach((token) => {
    check(css.includes(token), `Shared UI system is missing ${token}`);
});

async function verifyPublishedFeedLoading() {
    const seedReports = data.reports;
    const publishedReports = JSON.parse(JSON.stringify(seedReports.slice(0, 2)));
    const published = await data.loadReports({
        url: 'data/dashboard-reports.json',
        fetchImpl: async () => ({
            ok: true,
            status: 200,
            json: async () => ({ schema_version: 2, reports: publishedReports })
        })
    });
    check(published.source === 'published' && !published.error, 'Dashboard did not mark a published reporting feed as healthy');
    check(data.reports === publishedReports, 'Dashboard did not replace seed data with the published reporting feed');

    const fallback = await data.loadReports({
        url: 'data/dashboard-reports.json',
        fetchImpl: async () => ({ ok: false, status: 404 })
    });
    check(fallback.source === 'seed' && !fallback.error, 'Dashboard did not fall back to demo data when no published feed exists');
    check(data.reports === seedReports, 'Dashboard did not restore seed data after a missing feed');
}

verifyPublishedFeedLoading().then(() => {
    if (failures.length) {
        console.error(`Dashboard contract verification failed (${failures.length}):`);
        failures.forEach((failure) => console.error(`- ${failure}`));
        process.exitCode = 1;
    } else {
        console.log('PASS: dashboard contract, published-feed loading, seed states, confidence caps, aggregation, and UI surface checks all passed.');
    }
}).catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
