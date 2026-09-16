/**
 * MedTrack Client-Side Interactions & Clinical Workspaces
 */

document.addEventListener('DOMContentLoaded', function () {
  // 1. Mobile Menu Toggle
  const mobileToggle = document.getElementById('mobileNavToggle');
  const navLinks = document.getElementById('navLinks');

  if (mobileToggle && navLinks) {
    mobileToggle.addEventListener('click', function () {
      navLinks.classList.toggle('open');
      const isExpanded = navLinks.classList.contains('open');
      mobileToggle.setAttribute('aria-expanded', isExpanded);
    });
  }

  // 2. Auto-dismiss Flash Alerts
  const alerts = document.querySelectorAll('.alert');
  alerts.forEach(function (alert) {
    const closeBtn = alert.querySelector('.alert-close');
    if (closeBtn) {
      closeBtn.addEventListener('click', function () {
        alert.style.opacity = '0';
        alert.style.transform = 'translateY(-10px)';
        setTimeout(() => alert.remove(), 250);
      });
    }

    setTimeout(function () {
      if (alert && alert.parentElement) {
        alert.style.transition = 'opacity 0.4s ease, transform 0.4s ease';
        alert.style.opacity = '0';
        alert.style.transform = 'translateY(-10px)';
        setTimeout(() => alert.remove(), 400);
      }
    }, 6000);
  });

  // 3. Appointment Datepicker Min Date Constraint
  const appointmentDateInput = document.getElementById('appointmentDate');
  if (appointmentDateInput) {
    const today = new Date().toISOString().split('T')[0];
    appointmentDateInput.setAttribute('min', today);
  }

  // 4. Client-side Form Validation for Registration
  const registerForm = document.getElementById('registerForm');
  if (registerForm) {
    registerForm.addEventListener('submit', function (event) {
      const password = document.getElementById('password').value;
      const confirmPassword = document.getElementById('confirm_password').value;

      if (password !== confirmPassword) {
        event.preventDefault();
        alert('Passwords do not match. Please ensure both passwords are identical.');
        document.getElementById('confirm_password').focus();
      }
    });
  }

  // 5. Appointment Cancellation Confirmation
  const cancelForms = document.querySelectorAll('.cancel-appointment-form');
  cancelForms.forEach(function (form) {
    form.addEventListener('submit', function (e) {
      const confirmed = confirm('Are you sure you want to cancel this scheduled appointment? This will update your patient record and dispatch an AWS SNS notification.');
      if (!confirmed) {
        e.preventDefault();
      }
    });
  });

  // 6. Password Visibility Toggles
  const toggleButtons = document.querySelectorAll('.password-toggle-btn');
  toggleButtons.forEach(function (btn) {
    btn.addEventListener('click', function () {
      const targetId = btn.getAttribute('data-target');
      const input = document.getElementById(targetId);
      if (input) {
        if (input.type === 'password') {
          input.type = 'text';
          btn.textContent = '🙈';
          btn.setAttribute('aria-label', 'Hide password');
        } else {
          input.type = 'password';
          btn.textContent = '👁️';
          btn.setAttribute('aria-label', 'Show password');
        }
      }
    });
  });

  // 7. Dynamic Prescription Builder (Clinical Consultation)
  const addRxBtn = document.getElementById('btnAddRxItem');
  const rxContainer = document.getElementById('rxItemsContainer');

  if (addRxBtn && rxContainer) {
    addRxBtn.addEventListener('click', function () {
      const row = document.createElement('div');
      row.className = 'rx-item-row';
      row.innerHTML = `
        <div>
          <input type="text" name="med_name[]" class="form-control" placeholder="e.g. Lisinopril" required>
        </div>
        <div>
          <input type="text" name="med_dosage[]" class="form-control" placeholder="e.g. 10 mg" required>
        </div>
        <div>
          <select name="med_frequency[]" class="form-control">
            <option value="Once daily">Once daily</option>
            <option value="Twice daily">Twice daily</option>
            <option value="Three times daily">Three times daily</option>
            <option value="As needed (PRN)">As needed (PRN)</option>
            <option value="At bedtime">At bedtime</option>
          </select>
        </div>
        <div>
          <input type="text" name="med_duration[]" class="form-control" placeholder="e.g. 30 days">
        </div>
        <div>
          <input type="text" name="med_notes[]" class="form-control" placeholder="e.g. Take with food in morning">
        </div>
        <div>
          <button type="button" class="btn-remove-row" title="Remove medication">&times;</button>
        </div>
      `;
      rxContainer.appendChild(row);

      row.querySelector('.btn-remove-row').addEventListener('click', function () {
        row.remove();
      });
    });

    // Attach listener to existing remove buttons
    document.querySelectorAll('.btn-remove-row').forEach(function (btn) {
      btn.addEventListener('click', function () {
        const row = btn.closest('.rx-item-row');
        if (row) row.remove();
      });
    });
  }

  // 8. Real-Time Vitals Range Analysis
  const bpInput = document.getElementById('inputBP');
  const spo2Input = document.getElementById('inputSpO2');
  const hrInput = document.getElementById('inputHR');

  function analyzeVitals() {
    if (spo2Input) {
      const val = parseInt(spo2Input.value, 10);
      const tag = document.getElementById('spo2Tag');
      if (tag && !isNaN(val)) {
        if (val < 92) {
          tag.textContent = 'Critical Hypoxia';
          tag.style.color = '#ef4444';
        } else if (val < 95) {
          tag.textContent = 'Low Normal';
          tag.style.color = '#f59e0b';
        } else {
          tag.textContent = 'Optimal (95-100%)';
          tag.style.color = '#10b981';
        }
      }
    }

    if (hrInput) {
      const hr = parseInt(hrInput.value, 10);
      const tag = document.getElementById('hrTag');
      if (tag && !isNaN(hr)) {
        if (hr > 100) {
          tag.textContent = 'Tachycardia (>100 bpm)';
          tag.style.color = '#f59e0b';
        } else if (hr < 60) {
          tag.textContent = 'Bradycardia (<60 bpm)';
          tag.style.color = '#f59e0b';
        } else {
          tag.textContent = 'Normal Sinus (60-100)';
          tag.style.color = '#10b981';
        }
      }
    }
  }

  if (spo2Input) spo2Input.addEventListener('input', analyzeVitals);
  if (hrInput) hrInput.addEventListener('input', analyzeVitals);

  // 9. Quick ICD-10 Search/Selection Filler
  const icdSelect = document.getElementById('icd10Select');
  const diagInput = document.getElementById('diagnosisInput');

  if (icdSelect && diagInput) {
    icdSelect.addEventListener('change', function () {
      const selectedOption = icdSelect.options[icdSelect.selectedIndex];
      const desc = selectedOption.getAttribute('data-desc');
      if (desc && !diagInput.value.includes(desc)) {
        diagInput.value = (diagInput.value ? diagInput.value + '; ' : '') + desc;
      }
    });
  }

  // 10. Table Search/Filter Helper
  const tableSearch = document.getElementById('queueTableSearch');
  if (tableSearch) {
    tableSearch.addEventListener('keyup', function () {
      const query = tableSearch.value.toLowerCase();
      const rows = document.querySelectorAll('.filterable-table tbody tr');
      rows.forEach(function (row) {
        const text = row.textContent.toLowerCase();
        row.style.display = text.includes(query) ? '' : 'none';
      });
    });
  }
});
