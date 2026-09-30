// Demo behaviour: volatile element ids/classes, optional promo modal, page-specific widgets.
(function () {
  const params = new URLSearchParams(location.search);
  // Deterministic when ?seed=N is given, random otherwise.
  let state = params.has('seed') ? (parseInt(params.get('seed'), 10) || 1) : Math.floor(Math.random() * 1e9) + 1;
  const rand = () => { state = (state * 1664525 + 1013904223) % 4294967296; return state / 4294967296; };
  const suffix = () => 'r' + (100 + Math.floor(rand() * 899)) + Math.floor(rand() * 10);

  document.addEventListener('DOMContentLoaded', () => {
    // Intentionally changing elements: ids and classes change on every load.
    document.querySelectorAll('[data-volatile]').forEach((el) => {
      el.id = el.getAttribute('data-volatile') + '-' + suffix();
      el.classList.add('c' + suffix());
    });
    // ?variant=b renames the pricing link ("Plans") but keeps its href.
    if (params.get('variant') === 'b') {
      document.querySelectorAll('a[href$="pricing.html"]').forEach((a) => { if (a.closest('nav')) a.textContent = 'Plans'; });
    }
    // ?promo=1 shows a blocking modal that must be dismissed first.
    if (params.get('promo') === '1') {
      const wrap = document.createElement('div');
      wrap.className = 'modal-backdrop';
      wrap.setAttribute('role', 'dialog');
      wrap.setAttribute('aria-modal', 'true');
      wrap.innerHTML = '<div class="modal"><h2>Spring sale</h2><p>Sign up for our newsletter for a discount.</p><button type="button" data-close>Close</button></div>';
      wrap.querySelector('[data-close]').addEventListener('click', () => wrap.remove());
      document.body.appendChild(wrap);
    }
    const init = window.pageInit;
    if (typeof init === 'function') init();
  });
})();
