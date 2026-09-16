/**
 * MedTrack Client-Side Helper
 * Handles datepicker min constraints, alert auto-dismiss, and appointment actions.
 */

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
      setTimeout(() => alert.remove(), 400);
    }, 5000);
  });
});
