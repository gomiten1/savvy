(function injectionControls() {
    'use strict';

    const methodsByCountry = {
        MX: [['card', 'Card'], ['oxxo', 'OXXO'], ['wallet', 'Wallet']],
        BR: [['card', 'Card'], ['pix', 'PIX'], ['boleto', 'Boleto']],
        CO: [['card', 'Card'], ['pse', 'PSE'], ['wallet', 'Wallet']]
    };
    const banksByCountry = {
        MX: [['bbva', 'BBVA'], ['santander', 'Santander'], ['banorte', 'Banorte']],
        BR: [['itau', 'Itaú'], ['bradesco', 'Bradesco'], ['nubank', 'Nubank']],
        CO: [['bancolombia', 'Bancolombia'], ['davivienda', 'Davivienda'], ['bbva', 'BBVA']]
    };

    function fill(select, options, firstOption) {
        select.innerHTML = [firstOption, ...options.map(([value, label]) => `<option value="${value}">${label}</option>`)].join('');
    }

    document.addEventListener('DOMContentLoaded', () => {
        const form = document.getElementById('injectionForm');
        if (!form) return;
        const provider = document.getElementById('injectionProvider');
        const country = document.getElementById('injectionCountry');
        const method = document.getElementById('injectionMethod');
        const bank = document.getElementById('injectionBank');
        const submit = document.getElementById('injectionSubmit');
        const status = document.getElementById('injectionStatus');

        function syncRouteOptions() {
            fill(method, methodsByCountry[country.value], '<option value="">Choose a method</option>');
            const canTargetBank = provider.value === 'mercadopago';
            fill(bank, banksByCountry[country.value], '<option value="">All banks</option>');
            bank.disabled = !canTargetBank;
            if (!canTargetBank) bank.value = '';
        }

        provider.addEventListener('change', syncRouteOptions);
        country.addEventListener('change', syncRouteOptions);
        syncRouteOptions();

        form.addEventListener('submit', async (event) => {
            event.preventDefault();
            submit.disabled = true;
            status.className = 'injection-status';
            status.textContent = 'Queueing incident…';
            const values = new FormData(form);
            const payload = {
                provider: values.get('provider'),
                country: values.get('country'),
                payment_method: values.get('payment_method'),
                issuing_bank: values.get('issuing_bank') || null,
                approval_rate_multiplier: Number(values.get('approval_rate_multiplier')),
                duration_minutes: Number(values.get('duration_minutes')),
                dominant_decline_code: values.get('dominant_decline_code') || null
            };
            try {
                const response = await fetch('/api/injections', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
                const result = await response.json();
                if (!response.ok) throw new Error(result.error || 'The injection could not be queued.');
                status.classList.add('injection-status--success');
                status.textContent = 'Queued. Detection should pick it up shortly.';
            } catch (error) {
                status.classList.add('injection-status--error');
                status.textContent = error instanceof Error ? error.message : 'The injection could not be queued.';
            } finally {
                submit.disabled = false;
            }
        });
    });
}());
