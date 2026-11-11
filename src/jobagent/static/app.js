// Empty numeric filters must be omitted instead of submitting an invalid number.
for (const form of document.querySelectorAll('form[method="get"]')) {
  form.addEventListener('submit', () => {
    for (const field of form.querySelectorAll('input[type="number"]')) {
      if (!field.value) field.disabled = true;
    }
  });
}
// Refresh only the activity view while work is running; do not disturb import forms.
if (document.querySelector('[data-state="queued"], [data-state="running"]')) {
  setTimeout(async () => {
    try {
      const response = await fetch('/api/tasks');
      if (response.ok) window.location.reload();
    } catch (_) { /* Retry on the next manual refresh if the server has stopped. */ }
  }, 3000);
}
