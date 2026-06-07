document.addEventListener('click', function (event) {
    if (event.target.classList.contains('add-allocation')) {
        const list = event.target.closest('td').querySelector('.allocation-list');
        const firstRow = list.querySelector('.allocation-row');
        if (!firstRow) return;

        const clone = firstRow.cloneNode(true);
        clone.querySelectorAll('select').forEach((select) => {
            select.selectedIndex = 0;
        });
        clone.querySelectorAll('input').forEach((input) => {
            input.value = '';
        });
        list.appendChild(clone);
        validateAllocationListForForm(list);
    }

    if (event.target.classList.contains('remove-allocation')) {
        const list = event.target.closest('.allocation-list');
        const rows = list.querySelectorAll('.allocation-row');
        if (rows.length > 1) {
            event.target.closest('.allocation-row').remove();
            validateAllocationListForForm(list);
        }
    }
});

document.addEventListener('input', function (event) {
    if (event.target.classList.contains('allocation-percent-input')) {
        const list = event.target.closest('.allocation-list');
        const form = event.target.closest('form');
        if (form) form.dataset.partialSaveConfirmed = 'false';
        syncAllocationRowFromPercent(event.target.closest('.allocation-row'));
        validateAllocationListForForm(list);
    }

    if (event.target.classList.contains('allocation-amount-input')) {
        const list = event.target.closest('.allocation-list');
        const form = event.target.closest('form');
        if (form) form.dataset.partialSaveConfirmed = 'false';
        syncAllocationRowFromAmount(event.target.closest('.allocation-row'));
        validateAllocationListForForm(list);
    }
});

document.addEventListener('change', function (event) {
    if (event.target.name && event.target.name.includes('-categoria')) {
        const list = event.target.closest('.allocation-list');
        const form = event.target.closest('form');
        if (form) form.dataset.partialSaveConfirmed = 'false';
        validateAllocationListForForm(list);
    }
});

document.addEventListener('submit', function (event) {
    const form = event.target;
    const lists = form.querySelectorAll('.allocation-list');
    if (!lists.length) return;

    const allowPartialSave = form.dataset.allowPartialSave === 'true';
    let hasInvalidAllocation = false;
    let categorizedCount = 0;
    let emptyCount = 0;

    lists.forEach((list) => {
        const state = validateAllocationList(list, { allowEmpty: allowPartialSave });
        if (!state.isValid) {
            hasInvalidAllocation = true;
        }
        if (state.hasCategorizedAllocation) {
            categorizedCount += 1;
        } else {
            emptyCount += 1;
        }
    });

    if (hasInvalidAllocation) {
        event.preventDefault();
        return;
    }

    if (allowPartialSave && categorizedCount === 0) {
        event.preventDefault();
        window.alert('Selecione categoria em pelo menos um lancamento antes de salvar.');
        return;
    }

    if (allowPartialSave && emptyCount > 0 && form.dataset.partialSaveConfirmed !== 'true') {
        event.preventDefault();
        const modalElement = document.getElementById('partialSaveModal');
        if (!modalElement || !window.bootstrap) return;
        const modal = bootstrap.Modal.getOrCreateInstance(modalElement);
        modal.show();
    }
});

document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('.allocation-list').forEach((list) => {
        const form = list.closest('form');
        list.querySelectorAll('.allocation-row').forEach(syncAllocationRowFromPercent);
        validateAllocationList(list, { allowEmpty: form && form.dataset.allowPartialSave === 'true' });
    });

    const confirmPartialSave = document.getElementById('confirmPartialSave');
    if (confirmPartialSave) {
        confirmPartialSave.addEventListener('click', function () {
            const form = document.querySelector('form[data-allocation-form]');
            if (!form) return;
            form.dataset.partialSaveConfirmed = 'true';
            form.requestSubmit();
        });
    }

    setupInitialBalancePanel();
});

function validateAllocationList(list, options = {}) {
    if (!list) return { isValid: true, hasCategorizedAllocation: false };

    const rows = list.querySelectorAll('.allocation-row');
    const warning = list.parentElement.querySelector('.allocation-warning');
    let total = 0;
    let hasCategorizedAllocation = false;
    let hasInvalidAllocation = false;

    rows.forEach((row) => {
        const category = row.querySelector('select');
        const percentage = row.querySelector('.allocation-percent-input');
        const percentageValue = parseDecimalInput(percentage.value);

        if (!category.value) {
            return;
        }

        hasCategorizedAllocation = true;
        if (!percentage.value || Number.isNaN(percentageValue) || percentageValue <= 0 || percentageValue > 100) {
            hasInvalidAllocation = true;
            return;
        }
        total += percentageValue;
    });

    const isEmptyValid = options.allowEmpty && !hasCategorizedAllocation;
    const isValid = isEmptyValid || (hasCategorizedAllocation && !hasInvalidAllocation && Math.abs(total - 100) < 0.01);
    warning.classList.toggle('d-none', isValid);
    return { isValid, hasCategorizedAllocation };
}

function validateAllocationListForForm(list) {
    const form = list.closest('form');
    return validateAllocationList(list, { allowEmpty: form && form.dataset.allowPartialSave === 'true' });
}

function syncAllocationRowFromPercent(row) {
    if (!row) return;
    const list = row.closest('.allocation-list');
    const percentInput = row.querySelector('.allocation-percent-input');
    const amountInput = row.querySelector('.allocation-amount-input');
    const total = getAllocationTotal(list);
    const percent = parseDecimalInput(percentInput.value);

    if (!amountInput || !total || Number.isNaN(percent)) {
        if (amountInput && !percentInput.value) amountInput.value = '';
        return;
    }

    amountInput.value = formatDecimalInput(total * percent / 100);
}

function syncAllocationRowFromAmount(row) {
    if (!row) return;
    const list = row.closest('.allocation-list');
    const percentInput = row.querySelector('.allocation-percent-input');
    const amountInput = row.querySelector('.allocation-amount-input');
    const total = getAllocationTotal(list);
    const amount = parseDecimalInput(amountInput.value);

    if (!percentInput || !total || Number.isNaN(amount)) {
        if (percentInput && !amountInput.value) percentInput.value = '';
        return;
    }

    percentInput.value = formatDecimalInput(amount / total * 100);
}

function getAllocationTotal(list) {
    if (!list) return 0;
    return parseDecimalInput(list.dataset.lancamentoValor || '0');
}

function parseDecimalInput(value) {
    if (!value) return NaN;
    let normalized = String(value).trim();
    if (normalized.includes(',')) {
        normalized = normalized.replace(/\./g, '').replace(',', '.');
    }
    return parseFloat(normalized);
}

function formatDecimalInput(value) {
    if (!Number.isFinite(value)) return '';
    return value.toFixed(2).replace('.', ',');
}

function setupInitialBalancePanel() {
    const accountSelect = document.querySelector('[data-balance-account-select]');
    const panel = document.querySelector('[data-initial-balance-panel]');
    const dataElement = document.getElementById('account-balances-data');
    if (!accountSelect || !panel || !dataElement) return;

    const balances = JSON.parse(dataElement.textContent || '{}');
    const valueElement = panel.querySelector('[data-initial-balance-value]');
    const dateElement = panel.querySelector('[data-initial-balance-date]');

    const updatePanel = () => {
        const accountData = balances[accountSelect.value];
        if (!accountData) {
            panel.hidden = true;
            return;
        }

        const amount = Number(accountData.saldo || 0);
        valueElement.textContent = formatCurrency(amount);
        valueElement.classList.toggle('text-danger', amount < 0);
        valueElement.classList.toggle('text-success', amount >= 0);
        dateElement.textContent = accountData.data ? `Data do saldo: ${accountData.data}` : 'Data do saldo nao informada';
        panel.hidden = false;
    };

    accountSelect.addEventListener('change', updatePanel);
    updatePanel();
}

function formatCurrency(amount) {
    return new Intl.NumberFormat('pt-BR', {
        style: 'currency',
        currency: 'BRL',
    }).format(amount);
}
