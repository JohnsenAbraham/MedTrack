/**
 * MedTrack Client-Side Helper
 * Zero inline scripts / zero inline event handlers (Strict CSP script-src 'self' compliant).
 * Provides CSRF token extraction, date constraints, auto-dismissing alerts,
 * and unobtrusive event listeners for filtering, demo fill, auto-submit, and confirmations.
 */

// Helper to retrieve the CSRF token from the meta tag
function getCsrfToken() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  return meta ? meta.getAttribute('content') : '';
}

document.addEventListener('DOMContentLoaded', function () {
  // 1. Set appointment date input min constraint to today
  const dateInput = document.getElementById('appointment_date');
  if (dateInput) {
    const today = new Date().toISOString().split('T')[0];
    dateInput.setAttribute('min', today);
  }

  // 2. Auto-dismiss flash alert messages after 5 seconds
  const alerts = document.querySelectorAll('.alert');
  alerts.forEach(function (alert) {
    setTimeout(function () {
      alert.style.transition = 'opacity 0.4s ease, transform 0.4s ease';
      alert.style.opacity = '0';
      alert.style.transform = 'translateY(-6px)';
      setTimeout(function () {
        if (alert.parentNode) {
          alert.remove();
        }
      }, 400);
    }, 5000);
  });

  // 3. Status Filter Buttons on Appointments Table (data-filter-status)
  const filterContainer = document.querySelector('.status-filter-group');
  if (filterContainer) {
    filterContainer.addEventListener('click', function (e) {
      const btn = e.target.closest('[data-filter-status]');
      if (!btn) return;
      const status = btn.getAttribute('data-filter-status').toUpperCase();

      // Update active state
      const buttons = filterContainer.querySelectorAll('[data-filter-status]');
      buttons.forEach(function (b) { b.classList.remove('active'); });
      btn.classList.add('active');

      const table = document.getElementById('appointmentsTable');
      if (!table) return;
      const tbody = table.querySelector('tbody');
      if (!tbody) return;
      const rows = tbody.querySelectorAll('tr');

      rows.forEach(function (row) {
        if (status === 'ALL') {
          row.style.display = '';
        } else {
          const text = (row.textContent || row.innerText).toUpperCase();
          row.style.display = text.indexOf(status) > -1 ? '' : 'none';
        }
      });
    });
  }

  // 4. Instant Search Filter on Patient Roster (#patientSearchInput)
  const patientSearchInput = document.getElementById('patientSearchInput');
  if (patientSearchInput) {
    patientSearchInput.addEventListener('input', function () {
      const filter = this.value.toLowerCase().trim();
      const table = document.getElementById('patientsTable');
      if (!table) return;
      const tbody = table.querySelector('tbody');
      if (!tbody) return;
      const rows = tbody.querySelectorAll('tr');
      let visibleCount = 0;

      rows.forEach(function (row) {
        const text = (row.textContent || row.innerText).toLowerCase();
        if (text.indexOf(filter) > -1) {
          row.style.display = '';
          visibleCount++;
        } else {
          row.style.display = 'none';
        }
      });

      const countBadge = document.getElementById('patientCountBadge');
      if (countBadge) {
        countBadge.textContent = visibleCount + ' Matching Patient' + (visibleCount !== 1 ? 's' : '');
      }
    });
  }

  // 5. Quick Demo Sign In Autofill ([data-demo-email])
  document.addEventListener('click', function (e) {
    const demoBtn = e.target.closest('[data-demo-email]');
    if (!demoBtn) return;
    const email = demoBtn.getAttribute('data-demo-email');
    const pwd = demoBtn.getAttribute('data-demo-password');
    const emailInput = document.getElementById('email');
    const pwdInput = document.getElementById('password');
    if (emailInput && email) emailInput.value = email;
    if (pwdInput && pwd) pwdInput.value = pwd;
  });

  // 6. Auto-submit on select dropdowns (select[data-auto-submit="true"])
  document.addEventListener('change', function (e) {
    if (e.target && e.target.matches('select[data-auto-submit="true"]')) {
      if (e.target.form) {
        e.target.form.submit();
      }
    }
  });

  // 7. Unobtrusive confirmation handler ([data-confirm])
  document.addEventListener('submit', function (e) {
    const form = e.target;
    const confirmMsg = form.getAttribute('data-confirm');
    if (confirmMsg) {
      if (!window.confirm(confirmMsg)) {
        e.preventDefault();
        e.stopPropagation();
      }
    }
  });

  document.addEventListener('click', function (e) {
    const btn = e.target.closest('button[data-confirm], a[data-confirm]');
    if (!btn) return;
    const confirmMsg = btn.getAttribute('data-confirm');
    if (confirmMsg) {
      if (!window.confirm(confirmMsg)) {
        e.preventDefault();
        e.stopPropagation();
      }
    }
  });
});
