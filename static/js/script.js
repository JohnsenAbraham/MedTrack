/**
 * MedTrack Client-Side Interactions
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
    // Add close button handler
    const closeBtn = alert.querySelector('.alert-close');
    if (closeBtn) {
      closeBtn.addEventListener('click', function () {
        alert.style.opacity = '0';
        alert.style.transform = 'translateY(-10px)';
        setTimeout(() => alert.remove(), 250);
      });
    }

    // Auto dismiss after 6 seconds
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
      const confirmed = confirm('Are you sure you want to cancel this scheduled appointment? This will update your patient record and dispatch an AWS notification.');
      if (!confirmed) {
        e.preventDefault();
      }
    });
  });
});
