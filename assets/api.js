/**
 * Rabithole data layer
 * - SQLite API when server is running (python3 server.py)
 * - localStorage fallback when opened as plain files
 * - Auth token attached to write requests when logged in
 */
(function (global) {
  const API_BASE = '';
  const PRODUCT_KEY = 'rabithole_products';
  const POST_KEY = 'rabithole_blog_posts';
  const PROJECT_KEY = 'rabithole_projects';

  let useApi = null;

  function authHeaders() {
    const headers = { 'Content-Type': 'application/json' };
    const token = sessionStorage.getItem('rabithole_token');
    if (token && token !== 'local-fallback') {
      headers['Authorization'] = 'Bearer ' + token;
    }
    return headers;
  }

  async function probeApi() {
    if (useApi !== null) return useApi;
    try {
      const r = await fetch(API_BASE + '/api/products', { method: 'GET' });
      useApi = r.ok;
    } catch {
      useApi = false;
    }
    return useApi;
  }

  function lsGet(key) {
    try { return JSON.parse(localStorage.getItem(key) || '[]'); }
    catch { return []; }
  }
  function lsSet(key, data) {
    localStorage.setItem(key, JSON.stringify(data));
  }

  // ---------- Products ----------
  async function getProducts() {
    if (await probeApi()) {
      const r = await fetch(API_BASE + '/api/products');
      return r.json();
    }
    return lsGet(PRODUCT_KEY);
  }

  async function getProduct(id) {
    if (await probeApi()) {
      const r = await fetch(API_BASE + '/api/products/' + encodeURIComponent(id));
      if (!r.ok) return null;
      return r.json();
    }
    return lsGet(PRODUCT_KEY).find(p => p.id === id) || null;
  }

  async function saveProduct(product) {
    if (await probeApi()) {
      if (product._isEdit) {
        const r = await fetch(API_BASE + '/api/products/' + encodeURIComponent(product.id), {
          method: 'PUT',
          headers: authHeaders(),
          body: JSON.stringify(product),
        });
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || 'Save failed');
        return r.json();
      }
      const r = await fetch(API_BASE + '/api/products', {
        method: 'POST',
        headers: authHeaders(),
        body: JSON.stringify(product),
      });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || 'Save failed');
      return r.json();
    }
    let list = lsGet(PRODUCT_KEY);
    if (product._isEdit) {
      list = list.map(p => (p.id === product.id ? { ...product, updatedAt: Date.now() } : p));
    } else {
      if (!product.id) product.id = 'prod-' + Date.now();
      product.createdAt = product.createdAt || Date.now();
      list.unshift(product);
    }
    delete product._isEdit;
    lsSet(PRODUCT_KEY, list);
    return product;
  }

  async function uploadImage(file) {
    const token = sessionStorage.getItem('rabithole_token');
    const headers = { 'Content-Type': file.type };
    if (token && token !== 'local-fallback') {
      headers['Authorization'] = 'Bearer ' + token;
    }
    const r = await fetch(API_BASE + '/api/upload', {
      method: 'POST',
      headers,
      body: file,
    });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || 'Upload failed');
    return r.json();
  }

  async function uploadRequestPhoto(file) {
    const r = await fetch(API_BASE + '/api/requests/upload', {
      method: 'POST',
      headers: { 'Content-Type': file.type },
      body: file,
    });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || 'Upload failed');
    return r.json();
  }

  async function deleteProduct(id) {
    if (await probeApi()) {
      const r = await fetch(API_BASE + '/api/products/' + encodeURIComponent(id), {
        method: 'DELETE',
        headers: authHeaders(),
      });
      if (!r.ok) throw new Error('Delete failed');
      return true;
    }
    lsSet(PRODUCT_KEY, lsGet(PRODUCT_KEY).filter(p => p.id !== id));
    return true;
  }

  // ---------- Posts ----------
  async function getPosts() {
    if (await probeApi()) {
      const r = await fetch(API_BASE + '/api/posts');
      return r.json();
    }
    return lsGet(POST_KEY);
  }

  async function getPost(id) {
    if (await probeApi()) {
      const r = await fetch(API_BASE + '/api/posts/' + encodeURIComponent(id));
      if (!r.ok) return null;
      return r.json();
    }
    return lsGet(POST_KEY).find(p => p.id === id) || null;
  }

  async function savePost(post) {
    if (await probeApi()) {
      if (post._isEdit) {
        const r = await fetch(API_BASE + '/api/posts/' + encodeURIComponent(post.id), {
          method: 'PUT',
          headers: authHeaders(),
          body: JSON.stringify(post),
        });
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || 'Save failed');
        return r.json();
      }
      const r = await fetch(API_BASE + '/api/posts', {
        method: 'POST',
        headers: authHeaders(),
        body: JSON.stringify(post),
      });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || 'Save failed');
      return r.json();
    }
    let list = lsGet(POST_KEY);
    if (post._isEdit) {
      list = list.map(p => (p.id === post.id ? { ...post, updatedAt: Date.now() } : p));
    } else {
      if (!post.id) post.id = 'post-' + Date.now();
      post.createdAt = post.createdAt || Date.now();
      list.unshift(post);
    }
    delete post._isEdit;
    lsSet(POST_KEY, list);
    return post;
  }

  async function deletePost(id) {
    if (await probeApi()) {
      const r = await fetch(API_BASE + '/api/posts/' + encodeURIComponent(id), {
        method: 'DELETE',
        headers: authHeaders(),
      });
      if (!r.ok) throw new Error('Delete failed');
      return true;
    }
    lsSet(POST_KEY, lsGet(POST_KEY).filter(p => p.id !== id));
    return true;
  }

  // ---------- RC Projects ----------
  async function getProjects() {
    if (await probeApi()) {
      const r = await fetch(API_BASE + '/api/projects');
      return r.json();
    }
    return lsGet(PROJECT_KEY);
  }

  async function getProject(id) {
    if (await probeApi()) {
      const r = await fetch(API_BASE + '/api/projects/' + encodeURIComponent(id));
      if (!r.ok) return null;
      return r.json();
    }
    return lsGet(PROJECT_KEY).find(p => p.id === id) || null;
  }

  async function saveProject(project) {
    if (await probeApi()) {
      if (project._isEdit) {
        const r = await fetch(API_BASE + '/api/projects/' + encodeURIComponent(project.id), {
          method: 'PUT',
          headers: authHeaders(),
          body: JSON.stringify(project),
        });
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || 'Save failed');
        return r.json();
      }
      const r = await fetch(API_BASE + '/api/projects', {
        method: 'POST',
        headers: authHeaders(),
        body: JSON.stringify(project),
      });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || 'Save failed');
      return r.json();
    }
    let list = lsGet(PROJECT_KEY);
    if (project._isEdit) {
      list = list.map(p => (p.id === project.id ? { ...project, updatedAt: Date.now() } : p));
    } else {
      if (!project.id) project.id = 'project-' + Date.now();
      project.createdAt = project.createdAt || Date.now();
      list.unshift(project);
    }
    delete project._isEdit;
    lsSet(PROJECT_KEY, list);
    return project;
  }

  async function deleteProject(id) {
    if (await probeApi()) {
      const r = await fetch(API_BASE + '/api/projects/' + encodeURIComponent(id), {
        method: 'DELETE',
        headers: authHeaders(),
      });
      if (!r.ok) throw new Error('Delete failed');
      return true;
    }
    lsSet(PROJECT_KEY, lsGet(PROJECT_KEY).filter(p => p.id !== id));
    return true;
  }

  async function login(username, password) {
    const r = await fetch(API_BASE + '/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });
    if (!r.ok) {
      const data = await r.json().catch(() => ({}));
      throw new Error(data.error || 'Login failed');
    }
    return r.json();
  }

  async function updateAccount(payload) {
    const r = await fetch(API_BASE + '/api/me', {
      method: 'PUT',
      headers: authHeaders(),
      body: JSON.stringify(payload),
    });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || 'Update failed');
    return r.json();
  }

  async function getMe() {
    const r = await fetch(API_BASE + '/api/me', { headers: authHeaders() });
    if (!r.ok) return null;
    return (await r.json()).user;
  }

  async function logout() {
    const token = sessionStorage.getItem('rabithole_token');
    if (token && token !== 'local-fallback') {
      try {
        await fetch(API_BASE + '/api/logout', {
          method: 'POST',
          headers: authHeaders(),
        });
      } catch (_) { /* ignore */ }
    }
    sessionStorage.removeItem('rabithole_auth');
    sessionStorage.removeItem('rabithole_token');
    sessionStorage.removeItem('rabithole_user');
    sessionStorage.removeItem('rabithole_display');
  }

  async function storageMode() {
    return (await probeApi()) ? 'database' : 'localStorage';
  }

  // ---------- Store status (pause/resume all sales) ----------
  async function getStoreStatus() {
    const r = await fetch(API_BASE + '/api/store-status');
    if (!r.ok) return { salesPaused: false };
    return r.json();
  }

  async function updateStoreStatus(salesPaused) {
    const r = await fetch(API_BASE + '/api/store-status', {
      method: 'PUT',
      headers: authHeaders(),
      body: JSON.stringify({ salesPaused }),
    });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || 'Could not update store status');
    return r.json();
  }

  // ---------- Custom work requests ----------
  async function submitRequest(request) {
    const r = await fetch(API_BASE + '/api/requests', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || 'Could not submit request');
    return r.json();
  }

  async function getRequests() {
    const r = await fetch(API_BASE + '/api/requests', { headers: authHeaders() });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || 'Could not load requests');
    return r.json();
  }

  async function updateRequestStatus(id, status) {
    const r = await fetch(API_BASE + '/api/requests/' + encodeURIComponent(id), {
      method: 'PUT',
      headers: authHeaders(),
      body: JSON.stringify({ status }),
    });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || 'Could not update request');
    return r.json();
  }

  async function deleteRequest(id) {
    const r = await fetch(API_BASE + '/api/requests/' + encodeURIComponent(id), {
      method: 'DELETE',
      headers: authHeaders(),
    });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || 'Could not delete request');
    return true;
  }

  // ---------- PayPal checkout ----------
  async function getPaypalConfig() {
    const r = await fetch(API_BASE + '/api/paypal/config');
    if (!r.ok) return { clientId: '', currency: 'USD', configured: false };
    return r.json();
  }

  async function createCartOrder(items) {
    const r = await fetch(API_BASE + '/api/paypal/orders', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ items }),
    });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || 'Could not start checkout');
    return r.json();
  }

  async function capturePaypalOrder(orderId) {
    const r = await fetch(API_BASE + '/api/paypal/orders/' + encodeURIComponent(orderId) + '/capture', {
      method: 'POST',
    });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || 'Payment could not be captured');
    return r.json();
  }

  async function getOrders() {
    const r = await fetch(API_BASE + '/api/orders', { headers: authHeaders() });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || 'Could not load orders');
    return r.json();
  }

  async function updateOrderStatus(paypalOrderId, fulfillmentStatus) {
    const r = await fetch(API_BASE + '/api/orders/group/' + encodeURIComponent(paypalOrderId), {
      method: 'PUT',
      headers: authHeaders(),
      body: JSON.stringify({ fulfillmentStatus }),
    });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || 'Could not update order');
    return r.json();
  }

  async function updateOrderTracking(paypalOrderId, trackingNumber) {
    const r = await fetch(API_BASE + '/api/orders/group/' + encodeURIComponent(paypalOrderId), {
      method: 'PUT',
      headers: authHeaders(),
      body: JSON.stringify({ trackingNumber }),
    });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || 'Could not update order');
    return r.json();
  }

  async function deleteOrder(paypalOrderId) {
    const r = await fetch(API_BASE + '/api/orders/group/' + encodeURIComponent(paypalOrderId), {
      method: 'DELETE',
      headers: authHeaders(),
    });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || 'Could not delete order');
    return true;
  }

  global.RabitholeDB = {
    getProducts,
    getProduct,
    saveProduct,
    uploadImage,
    uploadRequestPhoto,
    deleteProduct,
    getPosts,
    getPost,
    savePost,
    deletePost,
    getProjects,
    getProject,
    saveProject,
    deleteProject,
    login,
    logout,
    updateAccount,
    getMe,
    storageMode,
    probeApi,
    getPaypalConfig,
    getStoreStatus,
    submitRequest,
    getRequests,
    updateRequestStatus,
    deleteRequest,
    updateStoreStatus,
    createCartOrder,
    capturePaypalOrder,
    getOrders,
    updateOrderStatus,
    updateOrderTracking,
    deleteOrder,
  };
})(window);
