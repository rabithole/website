/**
 * Rabithole shopping cart
 * - Cart state lives in localStorage (shared across tabs/pages)
 * - Injects a cart button into .nav-links and a slide-out drawer into <body>
 * - Checkout is a single PayPal payment for the whole cart (see server.py
 *   POST /api/paypal/orders + /api/paypal/orders/<id>/capture)
 */
(function () {
  const CART_KEY = 'rabithole_cart';

  function getCart() {
    try { return JSON.parse(localStorage.getItem(CART_KEY) || '[]'); }
    catch { return []; }
  }

  function saveCart(items) {
    localStorage.setItem(CART_KEY, JSON.stringify(items));
    updateBadge();
    if (drawerOpen) renderDrawerBody();
  }

  function cartCount(items) {
    return (items || getCart()).reduce((sum, i) => sum + i.quantity, 0);
  }

  function addToCart(productId, quantity) {
    quantity = Math.max(1, parseInt(quantity, 10) || 1);
    const items = getCart();
    const existing = items.find(i => i.productId === productId);
    if (existing) existing.quantity += quantity;
    else items.push({ productId, quantity });
    saveCart(items);
    openDrawer();
  }

  function setQuantity(productId, quantity) {
    quantity = parseInt(quantity, 10) || 0;
    let items = getCart();
    if (quantity <= 0) {
      items = items.filter(i => i.productId !== productId);
    } else {
      const existing = items.find(i => i.productId === productId);
      if (existing) existing.quantity = quantity;
    }
    saveCart(items);
  }

  function removeFromCart(productId) {
    saveCart(getCart().filter(i => i.productId !== productId));
  }

  function clearCart() {
    saveCart([]);
  }

  function formatMoney(n) {
    return '$' + Number(n || 0).toFixed(2);
  }

  // ---------- Styles ----------
  function injectStyles() {
    if (document.getElementById('cartStyles')) return;
    const style = document.createElement('style');
    style.id = 'cartStyles';
    style.textContent = `
      .cart-toggle-btn {
        position: relative; background: transparent; border: 1px solid var(--border);
        color: var(--text); border-radius: 999px; width: 38px; height: 38px;
        font-size: 1.05rem; cursor: pointer; display: inline-flex; align-items: center;
        justify-content: center; line-height: 1;
      }
      .cart-toggle-btn:hover { border-color: var(--orange); }
      .cart-badge {
        position: absolute; top: -6px; right: -6px; background: var(--orange);
        color: #07080a; font-size: 0.68rem; font-weight: 700; min-width: 17px; height: 17px;
        border-radius: 999px; align-items: center; justify-content: center; padding: 0 3px;
        display: none;
      }
      .cart-overlay {
        position: fixed; inset: 0; background: rgba(0,0,0,0.55); opacity: 0;
        pointer-events: none; transition: opacity 0.2s; z-index: 300;
      }
      .cart-overlay.open { opacity: 1; pointer-events: auto; }
      .cart-drawer {
        position: fixed; top: 0; right: 0; height: 100vh; width: min(400px, 100vw);
        background: var(--bg-card); border-left: 1px solid var(--border);
        display: flex; flex-direction: column; transform: translateX(100%);
        transition: transform 0.25s ease; z-index: 301; font-family: 'Inter', system-ui, sans-serif;
        color: var(--text);
      }
      .cart-drawer.open { transform: translateX(0); }
      .cart-drawer-header {
        display: flex; align-items: center; justify-content: space-between;
        padding: 1.1rem 1.3rem; border-bottom: 1px solid var(--border);
      }
      .cart-drawer-header h2 { font-family: 'Orbitron', sans-serif; font-size: 1.15rem; margin: 0; }
      .cart-close-btn {
        background: transparent; border: none; color: var(--text-muted); font-size: 1.1rem;
        cursor: pointer; padding: 0.2rem 0.4rem;
      }
      .cart-close-btn:hover { color: var(--text); }
      .cart-drawer-body { flex: 1; overflow-y: auto; padding: 0.6rem 1.3rem; }
      .cart-empty { color: var(--text-muted); text-align: center; padding: 3rem 1rem; font-size: 0.92rem; }
      .cart-row {
        display: flex; gap: 0.8rem; padding: 0.9rem 0; border-bottom: 1px solid var(--border);
        align-items: flex-start;
      }
      .cart-row-img { width: 56px; height: 56px; object-fit: cover; border-radius: 8px; flex-shrink: 0; background: var(--bg); }
      .cart-row-info { flex: 1; min-width: 0; }
      .cart-row-name { font-weight: 600; font-size: 0.9rem; margin-bottom: 0.15rem; }
      .cart-row-price { color: var(--text-muted); font-size: 0.8rem; }
      .cart-row-warn { color: #ff8a6b; font-size: 0.78rem; }
      .cart-row-qty { display: flex; align-items: center; gap: 0.5rem; margin-top: 0.4rem; }
      .qty-btn {
        width: 22px; height: 22px; border-radius: 6px; border: 1px solid var(--border);
        background: transparent; color: var(--text); cursor: pointer; font-size: 0.85rem; line-height: 1;
      }
      .qty-btn:hover { border-color: var(--orange); color: var(--orange); }
      .cart-row-right { display: flex; flex-direction: column; align-items: flex-end; gap: 0.5rem; }
      .cart-row-total { font-size: 0.88rem; font-weight: 600; }
      .cart-remove { background: none; border: none; color: var(--text-muted); font-size: 0.75rem; cursor: pointer; text-decoration: underline; padding: 0; }
      .cart-remove:hover { color: #ff6b6b; }
      .cart-drawer-footer { border-top: 1px solid var(--border); padding: 1rem 1.3rem 1.3rem; }
      .cart-subtotal-row {
        display: flex; justify-content: space-between; font-weight: 700; margin-bottom: 0.8rem; font-size: 1rem;
      }
      .cart-msg { font-size: 0.85rem; margin-top: 0.6rem; text-align: center; }
      .cart-msg.info { color: var(--text-muted); }
      .cart-msg.success { color: #5ddea0; }
      .cart-msg.error { color: #ff8a8a; }
    `;
    document.head.appendChild(style);
  }

  // ---------- Markup ----------
  function injectMarkup() {
    const navLinks = document.querySelector('.nav-links');
    if (navLinks && !document.getElementById('cartToggleBtn')) {
      const btn = document.createElement('button');
      btn.id = 'cartToggleBtn';
      btn.className = 'cart-toggle-btn';
      btn.setAttribute('aria-label', 'Open cart');
      btn.innerHTML = '🛒<span id="cartBadge" class="cart-badge">0</span>';
      btn.addEventListener('click', openDrawer);
      if (navLinks.tagName === 'UL') {
        const li = document.createElement('li');
        li.appendChild(btn);
        navLinks.appendChild(li);
      } else {
        navLinks.appendChild(btn);
      }
    }

    if (!document.getElementById('cartDrawer')) {
      const overlay = document.createElement('div');
      overlay.id = 'cartOverlay';
      overlay.className = 'cart-overlay';
      overlay.addEventListener('click', closeDrawer);

      const drawer = document.createElement('aside');
      drawer.id = 'cartDrawer';
      drawer.className = 'cart-drawer';
      drawer.innerHTML = `
        <div class="cart-drawer-header">
          <h2>Your Cart</h2>
          <button id="cartCloseBtn" class="cart-close-btn" aria-label="Close cart">✕</button>
        </div>
        <div id="cartDrawerBody" class="cart-drawer-body"></div>
        <div id="cartDrawerFooter" class="cart-drawer-footer" style="display:none;">
          <div class="cart-subtotal-row"><span>Subtotal</span><span id="cartSubtotal">$0.00</span></div>
          <div id="cartBlockMsg" class="cart-msg error" style="display:none;">Remove unavailable items to check out.</div>
          <div id="cartPaypalButtons"></div>
          <div id="cartCheckoutMsg" class="cart-msg"></div>
        </div>
      `;

      document.body.appendChild(overlay);
      document.body.appendChild(drawer);
      document.getElementById('cartCloseBtn').addEventListener('click', closeDrawer);
    }
  }

  // ---------- Drawer rendering ----------
  let productsCache = null;
  async function loadProducts() {
    if (productsCache) return productsCache;
    try {
      const r = await fetch('/api/products');
      productsCache = r.ok ? await r.json() : [];
    } catch {
      productsCache = [];
    }
    return productsCache;
  }

  async function renderDrawerBody() {
    const items = getCart();
    const bodyEl = document.getElementById('cartDrawerBody');
    const footerEl = document.getElementById('cartDrawerFooter');
    if (!bodyEl) return;

    if (items.length === 0) {
      bodyEl.innerHTML = '<div class="cart-empty">Your cart is empty.</div>';
      footerEl.style.display = 'none';
      return;
    }

    const products = await loadProducts();
    let subtotal = 0;
    let hasBlocking = false;

    bodyEl.innerHTML = items.map(item => {
      const p = products.find(x => x.id === item.productId);
      if (!p) {
        hasBlocking = true;
        return `<div class="cart-row">
          <div class="cart-row-info">
            <div class="cart-row-name">Item no longer available</div>
          </div>
          <button class="cart-remove" data-remove="${item.productId}">Remove</button>
        </div>`;
      }
      const soldOut = p.status === 'Sold Out';
      if (soldOut) hasBlocking = true;
      else subtotal += p.price * item.quantity;
      return `
        <div class="cart-row">
          <img class="cart-row-img" src="${p.image}" alt="">
          <div class="cart-row-info">
            <div class="cart-row-name">${p.name}</div>
            ${soldOut
              ? '<div class="cart-row-warn">Sold out — remove to check out</div>'
              : `<div class="cart-row-price">${formatMoney(p.price)} each</div>`}
            <div class="cart-row-qty">
              <button class="qty-btn" data-qty-dec="${item.productId}">−</button>
              <span>${item.quantity}</span>
              <button class="qty-btn" data-qty-inc="${item.productId}">+</button>
            </div>
          </div>
          <div class="cart-row-right">
            <div class="cart-row-total">${soldOut ? '' : formatMoney(p.price * item.quantity)}</div>
            <button class="cart-remove" data-remove="${item.productId}">Remove</button>
          </div>
        </div>`;
    }).join('');

    footerEl.style.display = 'block';
    document.getElementById('cartSubtotal').textContent = formatMoney(subtotal);

    const ppContainer = document.getElementById('cartPaypalButtons');
    const blockMsg = document.getElementById('cartBlockMsg');
    if (hasBlocking) {
      ppContainer.style.display = 'none';
      blockMsg.style.display = 'block';
    } else {
      ppContainer.style.display = 'block';
      blockMsg.style.display = 'none';
      ensurePaypalButtons();
    }

    bodyEl.querySelectorAll('[data-remove]').forEach(btn =>
      btn.addEventListener('click', () => removeFromCart(btn.dataset.remove)));
    bodyEl.querySelectorAll('[data-qty-inc]').forEach(btn =>
      btn.addEventListener('click', () => {
        const it = getCart().find(i => i.productId === btn.dataset.qtyInc);
        if (it) setQuantity(it.productId, it.quantity + 1);
      }));
    bodyEl.querySelectorAll('[data-qty-dec]').forEach(btn =>
      btn.addEventListener('click', () => {
        const it = getCart().find(i => i.productId === btn.dataset.qtyDec);
        if (it) setQuantity(it.productId, it.quantity - 1);
      }));
  }

  function updateBadge() {
    const badge = document.getElementById('cartBadge');
    if (!badge) return;
    const count = cartCount();
    badge.textContent = count;
    badge.style.display = count > 0 ? 'flex' : 'none';
  }

  // ---------- Drawer open/close ----------
  let drawerOpen = false;
  function openDrawer() {
    drawerOpen = true;
    document.getElementById('cartOverlay').classList.add('open');
    document.getElementById('cartDrawer').classList.add('open');
    productsCache = null; // refresh prices/status on every open
    renderDrawerBody();
  }
  function closeDrawer() {
    drawerOpen = false;
    document.getElementById('cartOverlay').classList.remove('open');
    document.getElementById('cartDrawer').classList.remove('open');
  }

  // ---------- PayPal checkout ----------
  let paypalSdkPromise = null;
  function loadPaypalSdk(clientId, currency) {
    if (paypalSdkPromise) return paypalSdkPromise;
    paypalSdkPromise = new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = `https://www.paypal.com/sdk/js?client-id=${encodeURIComponent(clientId)}&currency=${encodeURIComponent(currency)}&intent=capture`;
      script.onload = () => resolve(window.paypal);
      script.onerror = () => reject(new Error('Failed to load PayPal SDK'));
      document.head.appendChild(script);
    });
    return paypalSdkPromise;
  }

  let paypalButtonsRendered = false;
  async function ensurePaypalButtons() {
    if (paypalButtonsRendered) return;
    if (!window.RabitholeDB) return;
    const msg = document.getElementById('cartCheckoutMsg');

    const config = await RabitholeDB.getPaypalConfig();
    if (!config.configured) {
      msg.className = 'cart-msg info';
      msg.textContent = 'Online payment isn’t set up yet — email hello@rabithole.com to order.';
      return;
    }

    try {
      const paypal = await loadPaypalSdk(config.clientId, config.currency);
      paypalButtonsRendered = true;
      paypal.Buttons({
        style: { layout: 'vertical', color: 'gold', shape: 'rect', label: 'paypal' },
        createOrder: async () => {
          msg.textContent = '';
          const items = getCart().map(i => ({ productId: i.productId, quantity: i.quantity }));
          const order = await RabitholeDB.createCartOrder(items);
          return order.id;
        },
        onApprove: async (data) => {
          msg.className = 'cart-msg info';
          msg.textContent = 'Finishing up your payment…';
          try {
            const result = await RabitholeDB.capturePaypalOrder(data.orderID);
            if (result.status === 'COMPLETED') {
              clearCart();
              msg.className = 'cart-msg success';
              msg.textContent = 'Payment successful! A receipt was sent by PayPal to your email.';
            } else {
              msg.className = 'cart-msg error';
              msg.textContent = `Payment status: ${result.status}. Contact hello@rabithole.com if this seems wrong.`;
            }
          } catch (err) {
            msg.className = 'cart-msg error';
            msg.textContent = err.message || 'Payment could not be completed.';
          }
        },
        onError: (err) => {
          msg.className = 'cart-msg error';
          msg.textContent = 'PayPal checkout error. Please try again or email hello@rabithole.com.';
          console.error('PayPal error:', err);
        },
        onCancel: () => {
          msg.className = 'cart-msg info';
          msg.textContent = 'Checkout cancelled.';
        },
      }).render('#cartPaypalButtons');
    } catch (err) {
      msg.className = 'cart-msg error';
      msg.textContent = 'Could not load PayPal checkout.';
      console.error(err);
    }
  }

  // ---------- Init ----------
  function init() {
    injectStyles();
    injectMarkup();
    updateBadge();
    window.addEventListener('storage', (e) => {
      if (e.key === CART_KEY) {
        updateBadge();
        if (drawerOpen) renderDrawerBody();
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  window.RabitholeCart = {
    add: addToCart,
    remove: removeFromCart,
    setQuantity,
    clear: clearCart,
    open: openDrawer,
    close: closeDrawer,
    count: cartCount,
    getCart,
  };
})();
