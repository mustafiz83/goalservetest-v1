// Injects shared nav into pages and marks current active link
document.addEventListener('DOMContentLoaded', async () => {
  const container = document.getElementById('top-nav-container');
  if (!container) return;

  try {
    const resp = await fetch('/static/includes/topnav.html', { cache: 'no-store' });
    if (!resp.ok) throw new Error('Nav include not found');
    const html = await resp.text();
    container.innerHTML = html;
  } catch (err) {
    // fallback markup if include cannot be loaded
    container.innerHTML = `
      <nav class="top-nav">
        <ul>
          <li><a href="/" class="nav-link" data-path="/">Home</a></li>
          <li><a href="/live" class="nav-link" data-path="/live">Live</a></li>
          <li><a href="/" class="nav-link" data-path="/">HeatMap</a></li>
        </ul>
      </nav>
    `;
  }

  // mark active link
  const pathname = window.location.pathname.replace(/\/+$/, '') || '/';
  document.querySelectorAll('.top-nav .nav-link').forEach(a => {
    const target = (a.getAttribute('data-path') || a.getAttribute('href') || '/').replace(/\/+$/, '') || '/';
    if (target === pathname) a.classList.add('active');
  });
});