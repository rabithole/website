#!/usr/bin/env python3
"""
Rabithole local server — SQLite persistence + auth.

Usage:
    python3 server.py
    Open http://localhost:8080

API:
    POST /api/login              {username, password}
    POST /api/logout             (Authorization: Bearer <token>)
    GET  /api/me                 (Authorization: Bearer <token>)
    GET/POST /api/products
    GET/PUT/DELETE /api/products/<id>
    GET/POST /api/posts
    GET/PUT/DELETE /api/posts/<id>
    GET/POST /api/projects
    GET/PUT/DELETE /api/projects/<id>
    GET  /api/paypal/config              public PayPal client id / currency
    POST /api/paypal/orders               {items: [{productId, quantity}]}  -> create PayPal order for a cart
                                           (requests the buyer's shipping address on file)
    POST /api/paypal/orders/<id>/capture  -> capture an approved order
    GET  /api/orders                                  (Authorization: Bearer <token>) owner order
                                                       history, grouped by PayPal order — one entry
                                                       per shipment/checkout, with an "items" array
    PUT  /api/orders/group/<paypalOrderId>            (Authorization: Bearer <token>) update
                                                       fulfillment status and/or tracking number for
                                                       every line item in that order (they ship together)
    DELETE /api/orders/group/<paypalOrderId>          (Authorization: Bearer <token>) delete every
                                                       line item belonging to that order
    POST /api/upload                      (Authorization: Bearer <token>) upload a product image
                                           (raw image bytes as the body, Content-Type: image/png|jpeg|webp|gif)
    GET  /api/store-status                public — {salesPaused: bool}
    PUT  /api/store-status                (Authorization: Bearer <token>) {salesPaused: bool} —
                                           pause/resume all checkout site-wide
    POST /api/requests                    public — {name, email, serviceType, description,
                                           budget, timeline, photo} submit a custom work request
    POST /api/requests/upload             public — upload a reference photo for a work request
                                           (raw image bytes as the body, Content-Type: image/png|jpeg|webp|gif)
    GET  /api/requests                    (Authorization: Bearer <token>) list all requests
    PUT  /api/requests/<id>                (Authorization: Bearer <token>) update status
    DELETE /api/requests/<id>              (Authorization: Bearer <token>) delete a request

Default owner account (seeded in DB):
    username: admin
    password: rabithole

PayPal setup:
    Edit paypal_config.json with your Client ID and Secret from
    https://developer.paypal.com/dashboard/applications (use a Sandbox app
    to test with no real money first). Leave mode as "sandbox" until you're
    ready to accept real payments, then switch to "live" with live keys.
"""

import base64
import hashlib
import json
import posixpath
import secrets
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "db" / "rabithole.db"
PAYPAL_CONFIG_PATH = ROOT / "paypal_config.json"
UPLOADS_DIR = ROOT / "assets" / "uploads"
PORT = 8080
SESSION_DAYS = 7
FULFILLMENT_STATUSES = {"Pending", "Shipped", "Delivered", "Cancelled"}
REQUEST_STATUSES = {"New", "In Progress", "Completed", "Declined"}
MAX_UPLOAD_BYTES = 8 * 1024 * 1024  # 8 MB
UPLOAD_CONTENT_TYPES = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
}

# Everything the static file handler is allowed to serve. Anything else under
# ROOT (server.py, manage_users.py, paypal_config.json, db/, .claude/, ...) is
# server-side-only and must never be reachable by a plain GET.
ALLOWED_STATIC_FILES = {
    "/",
    "/index.html",
    "/products.html",
    "/blog.html",
    "/projects.html",
    "/contact.html",
    "/login.html",
    "/admin.html",
    "/orders.html",
    "/account.html",
    "/add-product.html",
    "/create-post.html",
    "/manage-products.html",
    "/manage-posts.html",
    "/add-project.html",
    "/manage-projects.html",
    "/request.html",
    "/manage-requests.html",
}
ALLOWED_STATIC_PREFIXES = ("/assets/",)


def is_allowed_static_path(path):
    normalized = posixpath.normpath(path)
    if normalized in ALLOWED_STATIC_FILES:
        return True
    return any(normalized.startswith(prefix) for prefix in ALLOWED_STATIC_PREFIXES)

# Cart snapshot for each PayPal order awaiting capture: paypal_order_id -> list
# of {"productId", "productName", "quantity", "unitPrice"}. In-memory only —
# this is a local dev server, and carts are short-lived between create and
# capture (typically seconds).
PENDING_CARTS = {}
PENDING_CARTS_LOCK = threading.Lock()

PAYPAL_API_BASE = {
    "sandbox": "https://api-m.sandbox.paypal.com",
    "live": "https://api-m.paypal.com",
}


def load_paypal_config():
    defaults = {"mode": "sandbox", "client_id": "", "secret": "", "currency": "USD"}
    try:
        with open(PAYPAL_CONFIG_PATH, "r", encoding="utf-8") as f:
            defaults.update(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return defaults


def paypal_get_token(config):
    base = PAYPAL_API_BASE[config["mode"]]
    auth = base64.b64encode(f"{config['client_id']}:{config['secret']}".encode()).decode()
    req = urllib.request.Request(
        base + "/v1/oauth2/token",
        data=b"grant_type=client_credentials",
        method="POST",
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())["access_token"]


def paypal_api(method, path, config, token, body=None):
    base = PAYPAL_API_BASE[config["mode"]]
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        base + path,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read()
        return json.loads(raw.decode()) if raw else {}


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn, key, value):
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def hash_password(password: str, salt: str):
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), 100000
    )
    return dk.hex()


def row_to_product(row):
    return {
        "id": row["id"],
        "name": row["name"],
        "price": row["price"],
        "category": row["category"],
        "status": row["status"],
        "description": row["description"],
        "image": row["image"],
        "shippingInfo": row["shipping_info"],
        "stockQuantity": row["stock_quantity"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_post(row):
    tags = row["tags"]
    try:
        tags = json.loads(tags) if tags else []
    except Exception:
        tags = []
    return {
        "id": row["id"],
        "title": row["title"],
        "tags": tags,
        "content": row["content"],
        "image": row["image"],
        "date": row["date"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_project(row):
    tags = row["tags"]
    try:
        tags = json.loads(tags) if tags else []
    except Exception:
        tags = []
    images = row["images"] if "images" in row.keys() else None
    try:
        images = json.loads(images) if images else []
    except Exception:
        images = []
    return {
        "id": row["id"],
        "title": row["title"],
        "description": row["description"],
        "content": row["content"],
        "image": row["image"],
        "images": images,
        "tags": tags,
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def row_to_request(row):
    return {
        "id": row["id"],
        "name": row["name"],
        "email": row["email"],
        "serviceType": row["service_type"],
        "description": row["description"],
        "budget": row["budget"],
        "timeline": row["timeline"],
        "photo": row["photo"],
        "status": row["status"],
        "createdAt": row["created_at"],
    }


def row_to_user(row):
    return {
        "id": row["id"],
        "username": row["username"],
        "displayName": row["display_name"] or row["username"],
    }


def group_orders(rows):
    """Collapse one-row-per-line-item order rows into one entry per PayPal
    order (i.e. per checkout/shipment), each carrying an "items" array.
    Shipping address, fulfillment status, and tracking number live on the
    shipment as a whole, not per line item — they physically ship together."""
    orders_by_paypal_id = {}
    ordered = []
    for row in rows:
        pid = row["paypal_order_id"]
        entry = orders_by_paypal_id.get(pid)
        if entry is None:
            entry = {
                "paypalOrderId": pid,
                "createdAt": row["created_at"],
                "currency": row["currency"],
                "payerEmail": row["payer_email"],
                "payerName": row["payer_name"],
                "status": row["status"],
                "fulfillmentStatus": row["fulfillment_status"],
                "trackingNumber": row["tracking_number"],
                "shipping": {
                    "name": row["shipping_name"],
                    "addressLine1": row["shipping_address_line1"],
                    "addressLine2": row["shipping_address_line2"],
                    "city": row["shipping_city"],
                    "state": row["shipping_state"],
                    "postalCode": row["shipping_postal_code"],
                    "country": row["shipping_country"],
                },
                "totalAmount": 0.0,
                "items": [],
            }
            orders_by_paypal_id[pid] = entry
            ordered.append(entry)
        entry["totalAmount"] += row["amount"] or 0
        entry["items"].append(
            {
                "id": row["id"],
                "productId": row["product_id"],
                "productName": row["product_name"],
                "amount": row["amount"],
                "productShippingInfo": row["product_shipping_info"],
            }
        )
    return ordered


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self):
        # Local dev server — never let the browser cache stale HTML/JS/API responses.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _send_json(self, data, status=200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header(
            "Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS"
        )
        self.send_header(
            "Access-Control-Allow-Headers", "Content-Type, Authorization"
        )
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _bearer_token(self):
        auth = self.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return None

    def _current_user(self):
        """Return user row if valid session token, else None."""
        token = self._bearer_token()
        if not token:
            return None
        now = int(time.time() * 1000)
        conn = get_db()
        row = conn.execute(
            """
            SELECT u.* FROM sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token = ? AND s.expires_at > ?
            """,
            (token, now),
        ).fetchone()
        conn.close()
        return row

    def _require_auth(self):
        user = self._current_user()
        if not user:
            self._send_json({"error": "Unauthorized"}, 401)
            return None
        return user

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header(
            "Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS"
        )
        self.send_header(
            "Access-Control-Allow-Headers", "Content-Type, Authorization"
        )
        self.end_headers()

    # ---------- GET ----------
    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/api/me":
            user = self._current_user()
            if not user:
                return self._send_json({"error": "Unauthorized"}, 401)
            return self._send_json({"user": row_to_user(user)})

        if path == "/api/paypal/config":
            config = load_paypal_config()
            return self._send_json(
                {
                    "clientId": config["client_id"],
                    "currency": config["currency"],
                    "mode": config["mode"],
                    "configured": bool(config["client_id"] and config["secret"]),
                }
            )

        if path == "/api/store-status":
            conn = get_db()
            sales_paused = get_setting(conn, "sales_paused", "0") == "1"
            conn.close()
            return self._send_json({"salesPaused": sales_paused})

        if path == "/api/orders":
            if not self._require_auth():
                return
            conn = get_db()
            rows = conn.execute(
                "SELECT * FROM orders ORDER BY created_at DESC"
            ).fetchall()
            conn.close()
            return self._send_json(group_orders(rows))

        if path == "/api/products":
            conn = get_db()
            rows = conn.execute(
                "SELECT * FROM products ORDER BY created_at DESC"
            ).fetchall()
            conn.close()
            return self._send_json([row_to_product(r) for r in rows])

        if path.startswith("/api/products/"):
            pid = path.split("/api/products/", 1)[1]
            conn = get_db()
            row = conn.execute(
                "SELECT * FROM products WHERE id = ?", (pid,)
            ).fetchone()
            conn.close()
            if not row:
                return self._send_json({"error": "Not found"}, 404)
            return self._send_json(row_to_product(row))

        if path == "/api/posts":
            conn = get_db()
            rows = conn.execute(
                "SELECT * FROM posts ORDER BY created_at DESC"
            ).fetchall()
            conn.close()
            return self._send_json([row_to_post(r) for r in rows])

        if path.startswith("/api/posts/"):
            pid = path.split("/api/posts/", 1)[1]
            conn = get_db()
            row = conn.execute(
                "SELECT * FROM posts WHERE id = ?", (pid,)
            ).fetchone()
            conn.close()
            if not row:
                return self._send_json({"error": "Not found"}, 404)
            return self._send_json(row_to_post(row))

        if path == "/api/projects":
            conn = get_db()
            rows = conn.execute(
                "SELECT * FROM projects ORDER BY created_at DESC"
            ).fetchall()
            conn.close()
            return self._send_json([row_to_project(r) for r in rows])

        if path.startswith("/api/projects/"):
            pid = path.split("/api/projects/", 1)[1]
            conn = get_db()
            row = conn.execute(
                "SELECT * FROM projects WHERE id = ?", (pid,)
            ).fetchone()
            conn.close()
            if not row:
                return self._send_json({"error": "Not found"}, 404)
            return self._send_json(row_to_project(row))

        if path == "/api/requests":
            if not self._require_auth():
                return
            conn = get_db()
            rows = conn.execute(
                "SELECT * FROM requests ORDER BY created_at DESC"
            ).fetchall()
            conn.close()
            return self._send_json([row_to_request(r) for r in rows])

        if not is_allowed_static_path(path):
            return self._send_json({"error": "Not found"}, 404)

        return super().do_GET()

    # ---------- POST ----------
    def do_POST(self):
        path = urlparse(self.path).path

        # --- Upload a product image (raw bytes body, not JSON) ---
        if path == "/api/upload":
            if not self._require_auth():
                return
            content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            ext = UPLOAD_CONTENT_TYPES.get(content_type)
            if not ext:
                return self._send_json(
                    {"error": "Unsupported image type. Use PNG, JPG, WEBP, or GIF."}, 400
                )
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0:
                return self._send_json({"error": "Empty upload"}, 400)
            if length > MAX_UPLOAD_BYTES:
                return self._send_json({"error": "Image too large (max 8MB)"}, 413)
            body = self.rfile.read(length)
            UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
            filename = f"upload-{int(time.time() * 1000)}-{secrets.token_hex(4)}.{ext}"
            (UPLOADS_DIR / filename).write_bytes(body)
            return self._send_json({"path": f"assets/uploads/{filename}"}, 201)

        # --- Upload a reference photo for a custom work request (public, no auth) ---
        if path == "/api/requests/upload":
            content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            ext = UPLOAD_CONTENT_TYPES.get(content_type)
            if not ext:
                return self._send_json(
                    {"error": "Unsupported image type. Use PNG, JPG, WEBP, or GIF."}, 400
                )
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0:
                return self._send_json({"error": "Empty upload"}, 400)
            if length > MAX_UPLOAD_BYTES:
                return self._send_json({"error": "Image too large (max 8MB)"}, 413)
            body = self.rfile.read(length)
            UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
            filename = f"request-{int(time.time() * 1000)}-{secrets.token_hex(4)}.{ext}"
            (UPLOADS_DIR / filename).write_bytes(body)
            return self._send_json({"path": f"assets/uploads/{filename}"}, 201)

        data = self._read_json()

        # --- PayPal: create order for a cart (amounts always looked up server-side) ---
        if path == "/api/paypal/orders":
            status_conn = get_db()
            sales_paused = get_setting(status_conn, "sales_paused", "0") == "1"
            status_conn.close()
            if sales_paused:
                return self._send_json(
                    {"error": "Online ordering is temporarily paused. Please check back soon."}, 503
                )

            config = load_paypal_config()
            if not config["client_id"] or not config["secret"]:
                return self._send_json(
                    {"error": "PayPal is not configured yet. Add your Client ID and Secret to paypal_config.json."},
                    503,
                )

            cart_items = data.get("items")
            if not isinstance(cart_items, list) or not cart_items:
                return self._send_json({"error": "Cart is empty"}, 400)

            conn = get_db()
            line_items = []
            total = 0.0
            for entry in cart_items:
                pid = entry.get("productId")
                try:
                    qty = int(entry.get("quantity", 1))
                except (TypeError, ValueError):
                    qty = 0
                if qty < 1:
                    conn.close()
                    return self._send_json({"error": "Invalid quantity in cart"}, 400)
                product = conn.execute(
                    "SELECT * FROM products WHERE id = ?", (pid,)
                ).fetchone()
                if not product:
                    conn.close()
                    return self._send_json({"error": f"Product not found: {pid}"}, 404)
                if product["status"] == "Sold Out":
                    conn.close()
                    return self._send_json(
                        {"error": f"“{product['name']}” is sold out"}, 400
                    )
                if product["stock_quantity"] is not None and qty > product["stock_quantity"]:
                    conn.close()
                    return self._send_json(
                        {
                            "error": f"Only {product['stock_quantity']} of “{product['name']}” "
                            f"left in stock"
                        },
                        400,
                    )
                unit_price = float(product["price"])
                line_items.append(
                    {
                        "productId": product["id"],
                        "productName": product["name"],
                        "quantity": qty,
                        "unitPrice": unit_price,
                    }
                )
                total += unit_price * qty
            conn.close()

            try:
                token = paypal_get_token(config)
                order = paypal_api(
                    "POST",
                    "/v2/checkout/orders",
                    config,
                    token,
                    {
                        "intent": "CAPTURE",
                        "application_context": {
                            "shipping_preference": "GET_FROM_FILE",
                        },
                        "purchase_units": [
                            {
                                "reference_id": "cart",
                                "amount": {
                                    "currency_code": config["currency"],
                                    "value": f"{total:.2f}",
                                    "breakdown": {
                                        "item_total": {
                                            "currency_code": config["currency"],
                                            "value": f"{total:.2f}",
                                        }
                                    },
                                },
                                "items": [
                                    {
                                        "name": li["productName"][:127],
                                        "sku": li["productId"],
                                        "quantity": str(li["quantity"]),
                                        "unit_amount": {
                                            "currency_code": config["currency"],
                                            "value": f"{li['unitPrice']:.2f}",
                                        },
                                    }
                                    for li in line_items
                                ],
                            }
                        ],
                    },
                )
            except urllib.error.HTTPError as e:
                print(f"PayPal create-order error: {e.code} {e.read().decode(errors='replace')}")
                return self._send_json({"error": "PayPal order creation failed"}, 502)
            except Exception as e:
                print(f"PayPal create-order error: {e}")
                return self._send_json({"error": "PayPal order creation failed"}, 502)

            with PENDING_CARTS_LOCK:
                PENDING_CARTS[order["id"]] = line_items

            return self._send_json({"id": order["id"]}, 201)

        # --- PayPal: capture an approved order ---
        if path.startswith("/api/paypal/orders/") and path.endswith("/capture"):
            config = load_paypal_config()
            if not config["client_id"] or not config["secret"]:
                return self._send_json({"error": "PayPal is not configured"}, 503)
            order_id = path.split("/api/paypal/orders/", 1)[1].rsplit("/capture", 1)[0]

            try:
                token = paypal_get_token(config)
                capture = paypal_api(
                    "POST", f"/v2/checkout/orders/{order_id}/capture", config, token, {}
                )
            except urllib.error.HTTPError as e:
                print(f"PayPal capture error: {e.code} {e.read().decode(errors='replace')}")
                return self._send_json({"error": "Payment could not be captured"}, 502)
            except Exception as e:
                print(f"PayPal capture error: {e}")
                return self._send_json({"error": "Payment could not be captured"}, 502)

            status = capture.get("status", "UNKNOWN")
            payer = capture.get("payer", {})
            payer_name = " ".join(
                filter(None, [
                    (payer.get("name") or {}).get("given_name"),
                    (payer.get("name") or {}).get("surname"),
                ])
            ) or None
            payer_email = payer.get("email_address")

            shipping = (capture.get("purchase_units") or [{}])[0].get("shipping") or {}
            shipping_address = shipping.get("address") or {}
            shipping_name = (shipping.get("name") or {}).get("full_name")
            shipping_address_line1 = shipping_address.get("address_line_1")
            shipping_address_line2 = shipping_address.get("address_line_2")
            shipping_city = shipping_address.get("admin_area_2")
            shipping_state = shipping_address.get("admin_area_1")
            shipping_postal_code = shipping_address.get("postal_code")
            shipping_country = shipping_address.get("country_code")

            with PENDING_CARTS_LOCK:
                line_items = PENDING_CARTS.pop(order_id, None)

            if not line_items:
                # Server restarted between create and capture, or a stale/unknown
                # order id — fall back to a single record from PayPal's own total
                # so the payment is never silently lost from order history.
                purchase_unit = (capture.get("purchase_units") or [{}])[0]
                captures = (purchase_unit.get("payments") or {}).get("captures") or [{}]
                amount_info = captures[0].get("amount", {})
                line_items = [
                    {
                        "productId": None,
                        "productName": "Cart order",
                        "quantity": 1,
                        "unitPrice": float(amount_info.get("value", 0) or 0),
                    }
                ]

            conn = get_db()
            now = int(time.time() * 1000)
            order_row_ids = []
            for idx, li in enumerate(line_items):
                order_row_id = f"order-{now}-{idx}"
                order_row_ids.append(order_row_id)
                product_shipping_info = None
                if li.get("productId"):
                    prod_row = conn.execute(
                        "SELECT shipping_info, stock_quantity FROM products WHERE id = ?",
                        (li["productId"],),
                    ).fetchone()
                    if prod_row:
                        product_shipping_info = prod_row["shipping_info"]
                        if status == "COMPLETED" and prod_row["stock_quantity"] is not None:
                            remaining = max(0, prod_row["stock_quantity"] - li["quantity"])
                            conn.execute(
                                "UPDATE products SET stock_quantity = ? WHERE id = ?",
                                (remaining, li["productId"]),
                            )
                            if remaining == 0:
                                conn.execute(
                                    "UPDATE products SET status = 'Sold Out' WHERE id = ?",
                                    (li["productId"],),
                                )
                conn.execute(
                    """INSERT INTO orders
                       (id, product_id, product_name, amount, currency, payer_email, payer_name, paypal_order_id, status, created_at,
                        shipping_name, shipping_address_line1, shipping_address_line2, shipping_city, shipping_state, shipping_postal_code, shipping_country, product_shipping_info)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        order_row_id,
                        li["productId"],
                        li["productName"],
                        li["unitPrice"] * li["quantity"],
                        config["currency"],
                        payer_email,
                        payer_name,
                        order_id,
                        status,
                        now,
                        shipping_name,
                        shipping_address_line1,
                        shipping_address_line2,
                        shipping_city,
                        shipping_state,
                        shipping_postal_code,
                        shipping_country,
                        product_shipping_info,
                    ),
                )
            conn.commit()
            conn.close()

            return self._send_json({"status": status, "orderIds": order_row_ids})

        # --- Login ---
        if path == "/api/login":
            username = (data.get("username") or "").strip()
            password = data.get("password") or ""
            if not username or not password:
                return self._send_json(
                    {"error": "Username and password required"}, 400
                )

            conn = get_db()
            user = conn.execute(
                "SELECT * FROM users WHERE username = ?", (username,)
            ).fetchone()
            if not user:
                conn.close()
                return self._send_json({"error": "Invalid credentials"}, 401)

            expected = hash_password(password, user["salt"])
            if not secrets.compare_digest(expected, user["password_hash"]):
                conn.close()
                return self._send_json({"error": "Invalid credentials"}, 401)

            # Create session token
            token = secrets.token_urlsafe(32)
            now = int(time.time() * 1000)
            expires = now + SESSION_DAYS * 24 * 60 * 60 * 1000
            conn.execute(
                "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
                (token, user["id"], now, expires),
            )
            conn.commit()
            conn.close()

            return self._send_json(
                {
                    "ok": True,
                    "token": token,
                    "user": row_to_user(user),
                    "expiresAt": expires,
                }
            )

        # --- Logout ---
        if path == "/api/logout":
            token = self._bearer_token()
            if token:
                conn = get_db()
                conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
                conn.commit()
                conn.close()
            return self._send_json({"ok": True})

        # --- Products (auth required for create) ---
        if path == "/api/products":
            if not self._require_auth():
                return
            pid = data.get("id") or f"prod-{int(time.time() * 1000)}"
            now = int(time.time() * 1000)
            stock_quantity = data.get("stockQuantity")
            stock_quantity = int(stock_quantity) if stock_quantity not in (None, "") else None
            conn = get_db()
            conn.execute(
                """INSERT INTO products
                   (id, name, price, category, status, description, image, shipping_info, stock_quantity, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    pid,
                    data["name"],
                    float(data["price"]),
                    data["category"],
                    data.get("status", "Available"),
                    data["description"],
                    data.get("image") or "",
                    data.get("shippingInfo") or None,
                    stock_quantity,
                    data.get("createdAt", now),
                    None,
                ),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM products WHERE id = ?", (pid,)
            ).fetchone()
            conn.close()
            return self._send_json(row_to_product(row), 201)

        # --- Posts (auth required for create) ---
        if path == "/api/posts":
            if not self._require_auth():
                return
            pid = data.get("id") or f"post-{int(time.time() * 1000)}"
            now = int(time.time() * 1000)
            tags = data.get("tags", [])
            if isinstance(tags, list):
                tags = json.dumps(tags)
            conn = get_db()
            conn.execute(
                """INSERT INTO posts
                   (id, title, tags, content, image, date, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    pid,
                    data["title"],
                    tags,
                    data["content"],
                    data.get("image") or None,
                    data.get("date", time.strftime("%Y-%m-%d")),
                    data.get("createdAt", now),
                    None,
                ),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM posts WHERE id = ?", (pid,)
            ).fetchone()
            conn.close()
            return self._send_json(row_to_post(row), 201)

        # --- Projects (auth required for create) ---
        if path == "/api/projects":
            if not self._require_auth():
                return
            pid = data.get("id") or f"project-{int(time.time() * 1000)}"
            now = int(time.time() * 1000)
            tags = data.get("tags", [])
            if isinstance(tags, list):
                tags = json.dumps(tags)
            images = data.get("images", [])
            if isinstance(images, list):
                images = json.dumps(images)
            conn = get_db()
            conn.execute(
                """INSERT INTO projects
                   (id, title, description, image, images, tags, content, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    pid,
                    data["title"],
                    data["description"],
                    data.get("image") or "",
                    images,
                    tags,
                    data.get("content") or None,
                    data.get("createdAt", now),
                    None,
                ),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM projects WHERE id = ?", (pid,)
            ).fetchone()
            conn.close()
            return self._send_json(row_to_project(row), 201)

        # --- Custom work requests (public — anyone can submit) ---
        if path == "/api/requests":
            name = (data.get("name") or "").strip()
            email = (data.get("email") or "").strip()
            service_type = (data.get("serviceType") or "").strip()
            description = (data.get("description") or "").strip()
            if not name or not email or not service_type or not description:
                return self._send_json(
                    {"error": "Name, email, service type, and description are required"}, 400
                )
            rid = f"request-{int(time.time() * 1000)}"
            now = int(time.time() * 1000)
            conn = get_db()
            conn.execute(
                """INSERT INTO requests
                   (id, name, email, service_type, description, budget, timeline, photo, status, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    rid,
                    name,
                    email,
                    service_type,
                    description,
                    (data.get("budget") or "").strip() or None,
                    (data.get("timeline") or "").strip() or None,
                    (data.get("photo") or "").strip() or None,
                    "New",
                    now,
                ),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM requests WHERE id = ?", (rid,)).fetchone()
            conn.close()
            return self._send_json(row_to_request(row), 201)

        self._send_json({"error": "Not found"}, 404)

    # ---------- PUT ----------
    def do_PUT(self):
        path = urlparse(self.path).path
        data = self._read_json()
        now = int(time.time() * 1000)

        if path == "/api/store-status":
            if not self._require_auth():
                return
            if "salesPaused" not in data:
                return self._send_json({"error": "salesPaused is required"}, 400)
            conn = get_db()
            set_setting(conn, "sales_paused", "1" if data["salesPaused"] else "0")
            conn.commit()
            conn.close()
            return self._send_json({"salesPaused": bool(data["salesPaused"])})

        if path == "/api/me":
            user = self._require_auth()
            if not user:
                return
            current_password = data.get("currentPassword") or ""
            expected = hash_password(current_password, user["salt"])
            if not current_password or not secrets.compare_digest(expected, user["password_hash"]):
                return self._send_json({"error": "Current password is incorrect"}, 401)

            new_username = (data.get("newUsername") or "").strip() or user["username"]
            new_password = data.get("newPassword") or ""
            new_display_name = data.get("newDisplayName")
            new_display_name = new_display_name.strip() if new_display_name else None
            if new_display_name is None:
                new_display_name = user["display_name"]

            conn = get_db()
            if new_username != user["username"]:
                clash = conn.execute(
                    "SELECT id FROM users WHERE username = ? AND id != ?",
                    (new_username, user["id"]),
                ).fetchone()
                if clash:
                    conn.close()
                    return self._send_json({"error": "Username already taken"}, 409)

            if new_password:
                salt = secrets.token_hex(16)
                password_hash = hash_password(new_password, salt)
                conn.execute(
                    "UPDATE users SET username=?, password_hash=?, salt=?, display_name=? WHERE id=?",
                    (new_username, password_hash, salt, new_display_name, user["id"]),
                )
            else:
                conn.execute(
                    "UPDATE users SET username=?, display_name=? WHERE id=?",
                    (new_username, new_display_name, user["id"]),
                )

            # Keep the current session alive, but log out this user's other sessions
            current_token = self._bearer_token()
            conn.execute(
                "DELETE FROM sessions WHERE user_id = ? AND token != ?",
                (user["id"], current_token),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
            conn.close()
            return self._send_json({"ok": True, "user": row_to_user(row)})

        if path.startswith("/api/products/"):
            if not self._require_auth():
                return
            pid = path.split("/api/products/", 1)[1]
            conn = get_db()
            existing = conn.execute(
                "SELECT id FROM products WHERE id = ?", (pid,)
            ).fetchone()
            if not existing:
                conn.close()
                return self._send_json({"error": "Not found"}, 404)
            stock_quantity = data.get("stockQuantity")
            stock_quantity = int(stock_quantity) if stock_quantity not in (None, "") else None
            conn.execute(
                """UPDATE products SET name=?, price=?, category=?, status=?,
                   description=?, image=?, shipping_info=?, stock_quantity=?, updated_at=? WHERE id=?""",
                (
                    data["name"],
                    float(data["price"]),
                    data["category"],
                    data.get("status", "Available"),
                    data["description"],
                    data.get("image") or "",
                    data.get("shippingInfo") or None,
                    stock_quantity,
                    now,
                    pid,
                ),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM products WHERE id = ?", (pid,)
            ).fetchone()
            conn.close()
            return self._send_json(row_to_product(row))

        if path.startswith("/api/posts/"):
            if not self._require_auth():
                return
            pid = path.split("/api/posts/", 1)[1]
            tags = data.get("tags", [])
            if isinstance(tags, list):
                tags = json.dumps(tags)
            conn = get_db()
            existing = conn.execute(
                "SELECT id FROM posts WHERE id = ?", (pid,)
            ).fetchone()
            if not existing:
                conn.close()
                return self._send_json({"error": "Not found"}, 404)
            conn.execute(
                "UPDATE posts SET title=?, tags=?, content=?, image=?, updated_at=? WHERE id=?",
                (data["title"], tags, data["content"], data.get("image") or None, now, pid),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM posts WHERE id = ?", (pid,)
            ).fetchone()
            conn.close()
            return self._send_json(row_to_post(row))

        if path.startswith("/api/projects/"):
            if not self._require_auth():
                return
            pid = path.split("/api/projects/", 1)[1]
            tags = data.get("tags", [])
            if isinstance(tags, list):
                tags = json.dumps(tags)
            images = data.get("images", [])
            if isinstance(images, list):
                images = json.dumps(images)
            conn = get_db()
            existing = conn.execute(
                "SELECT id FROM projects WHERE id = ?", (pid,)
            ).fetchone()
            if not existing:
                conn.close()
                return self._send_json({"error": "Not found"}, 404)
            conn.execute(
                "UPDATE projects SET title=?, description=?, image=?, images=?, tags=?, content=?, updated_at=? WHERE id=?",
                (
                    data["title"],
                    data["description"],
                    data.get("image") or "",
                    images,
                    tags,
                    data.get("content") or None,
                    now,
                    pid,
                ),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM projects WHERE id = ?", (pid,)
            ).fetchone()
            conn.close()
            return self._send_json(row_to_project(row))

        if path.startswith("/api/orders/group/"):
            if not self._require_auth():
                return
            paypal_order_id = path.split("/api/orders/group/", 1)[1]
            has_status = "fulfillmentStatus" in data
            has_tracking = "trackingNumber" in data
            if not has_status and not has_tracking:
                return self._send_json(
                    {"error": "Provide fulfillmentStatus and/or trackingNumber"}, 400
                )
            if has_status and data["fulfillmentStatus"] not in FULFILLMENT_STATUSES:
                return self._send_json(
                    {"error": f"fulfillmentStatus must be one of {sorted(FULFILLMENT_STATUSES)}"},
                    400,
                )
            conn = get_db()
            existing = conn.execute(
                "SELECT id FROM orders WHERE paypal_order_id = ?", (paypal_order_id,)
            ).fetchone()
            if not existing:
                conn.close()
                return self._send_json({"error": "Not found"}, 404)
            if has_status:
                conn.execute(
                    "UPDATE orders SET fulfillment_status = ? WHERE paypal_order_id = ?",
                    (data["fulfillmentStatus"], paypal_order_id),
                )
            if has_tracking:
                conn.execute(
                    "UPDATE orders SET tracking_number = ? WHERE paypal_order_id = ?",
                    (data["trackingNumber"].strip() or None, paypal_order_id),
                )
            conn.commit()
            rows = conn.execute(
                "SELECT * FROM orders WHERE paypal_order_id = ?", (paypal_order_id,)
            ).fetchall()
            conn.close()
            return self._send_json(group_orders(rows)[0])

        if path.startswith("/api/requests/"):
            if not self._require_auth():
                return
            rid = path.split("/api/requests/", 1)[1]
            new_status = data.get("status")
            if new_status not in REQUEST_STATUSES:
                return self._send_json(
                    {"error": f"status must be one of {sorted(REQUEST_STATUSES)}"}, 400
                )
            conn = get_db()
            existing = conn.execute(
                "SELECT id FROM requests WHERE id = ?", (rid,)
            ).fetchone()
            if not existing:
                conn.close()
                return self._send_json({"error": "Not found"}, 404)
            conn.execute("UPDATE requests SET status = ? WHERE id = ?", (new_status, rid))
            conn.commit()
            row = conn.execute("SELECT * FROM requests WHERE id = ?", (rid,)).fetchone()
            conn.close()
            return self._send_json(row_to_request(row))

        self._send_json({"error": "Not found"}, 404)

    # ---------- DELETE ----------
    def do_DELETE(self):
        path = urlparse(self.path).path

        if path.startswith("/api/products/"):
            if not self._require_auth():
                return
            pid = path.split("/api/products/", 1)[1]
            conn = get_db()
            conn.execute("DELETE FROM products WHERE id = ?", (pid,))
            conn.commit()
            conn.close()
            return self._send_json({"ok": True})

        if path.startswith("/api/posts/"):
            if not self._require_auth():
                return
            pid = path.split("/api/posts/", 1)[1]
            conn = get_db()
            conn.execute("DELETE FROM posts WHERE id = ?", (pid,))
            conn.commit()
            conn.close()
            return self._send_json({"ok": True})

        if path.startswith("/api/projects/"):
            if not self._require_auth():
                return
            pid = path.split("/api/projects/", 1)[1]
            conn = get_db()
            conn.execute("DELETE FROM projects WHERE id = ?", (pid,))
            conn.commit()
            conn.close()
            return self._send_json({"ok": True})

        if path.startswith("/api/orders/group/"):
            if not self._require_auth():
                return
            paypal_order_id = path.split("/api/orders/group/", 1)[1]
            conn = get_db()
            conn.execute("DELETE FROM orders WHERE paypal_order_id = ?", (paypal_order_id,))
            conn.commit()
            conn.close()
            return self._send_json({"ok": True})

        if path.startswith("/api/requests/"):
            if not self._require_auth():
                return
            rid = path.split("/api/requests/", 1)[1]
            conn = get_db()
            conn.execute("DELETE FROM requests WHERE id = ?", (rid,))
            conn.commit()
            conn.close()
            return self._send_json({"ok": True})

        self._send_json({"error": "Not found"}, 404)

    def log_message(self, fmt, *args):
        print(f"[{self.log_date_time_string()}] {args[0]}")


def main():
    if not DB_PATH.exists():
        print(f"ERROR: Database not found at {DB_PATH}")
        return

    # Ensure users table exists
    conn = get_db()
    conn.execute(
        """CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            display_name TEXT,
            created_at INTEGER NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS orders (
            id TEXT PRIMARY KEY,
            product_id TEXT,
            product_name TEXT,
            amount REAL,
            currency TEXT,
            payer_email TEXT,
            payer_name TEXT,
            paypal_order_id TEXT,
            status TEXT,
            created_at INTEGER NOT NULL
        )"""
    )
    order_cols = [r[1] for r in conn.execute("PRAGMA table_info(orders)").fetchall()]
    if "fulfillment_status" not in order_cols:
        conn.execute(
            "ALTER TABLE orders ADD COLUMN fulfillment_status TEXT NOT NULL DEFAULT 'Pending'"
        )
    if "shipping_name" not in order_cols:
        conn.execute("ALTER TABLE orders ADD COLUMN shipping_name TEXT")
        conn.execute("ALTER TABLE orders ADD COLUMN shipping_address_line1 TEXT")
        conn.execute("ALTER TABLE orders ADD COLUMN shipping_address_line2 TEXT")
        conn.execute("ALTER TABLE orders ADD COLUMN shipping_city TEXT")
        conn.execute("ALTER TABLE orders ADD COLUMN shipping_state TEXT")
        conn.execute("ALTER TABLE orders ADD COLUMN shipping_postal_code TEXT")
        conn.execute("ALTER TABLE orders ADD COLUMN shipping_country TEXT")
        conn.execute("ALTER TABLE orders ADD COLUMN product_shipping_info TEXT")
    if "tracking_number" not in order_cols:
        conn.execute("ALTER TABLE orders ADD COLUMN tracking_number TEXT")
    product_cols = [r[1] for r in conn.execute("PRAGMA table_info(products)").fetchall()]
    if "shipping_info" not in product_cols:
        conn.execute("ALTER TABLE products ADD COLUMN shipping_info TEXT")
    if "stock_quantity" not in product_cols:
        conn.execute("ALTER TABLE products ADD COLUMN stock_quantity INTEGER")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS requests (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            service_type TEXT NOT NULL,
            description TEXT NOT NULL,
            budget TEXT,
            timeline TEXT,
            photo TEXT,
            status TEXT NOT NULL DEFAULT 'New',
            created_at INTEGER NOT NULL
        )"""
    )
    request_cols = [r[1] for r in conn.execute("PRAGMA table_info(requests)").fetchall()]
    if "photo" not in request_cols:
        conn.execute("ALTER TABLE requests ADD COLUMN photo TEXT")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            image TEXT NOT NULL,
            tags TEXT NOT NULL DEFAULT '[]',
            created_at INTEGER NOT NULL,
            updated_at INTEGER
        )"""
    )
    project_cols = [r[1] for r in conn.execute("PRAGMA table_info(projects)").fetchall()]
    if "content" not in project_cols:
        conn.execute("ALTER TABLE projects ADD COLUMN content TEXT")
    if "images" not in project_cols:
        conn.execute("ALTER TABLE projects ADD COLUMN images TEXT NOT NULL DEFAULT '[]'")
    post_cols = [r[1] for r in conn.execute("PRAGMA table_info(posts)").fetchall()]
    if post_cols and "image" not in post_cols:
        conn.execute("ALTER TABLE posts ADD COLUMN image TEXT")
    if conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0:
        seed_now = int(time.time() * 1000)
        conn.execute(
            """INSERT INTO projects (id, title, description, image, tags, content, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                "project-1",
                "Lightweight Suspension Overhaul",
                "Replaced stock arms and towers with CF-nylon topology-optimized parts. "
                "Reduced unsprung weight while increasing roll stiffness.",
                "assets/suspension.png",
                json.dumps(["FEA", "PA6-CF", "1/10 Buggy"]),
                "The stock suspension arms on my 1/10 buggy were solid plastic — heavy, and "
                "not particularly stiff where it mattered.\n\n"
                "I ran a quick topology optimization pass in Fusion 360 with load cases for "
                "bump, cornering, and braking, then printed the result in PA6-CF (carbon-fiber "
                "reinforced nylon).\n\n"
                "Results after a full race weekend: noticeably crisper turn-in, no cracking, "
                "and about 18% less unsprung weight per corner versus the stock arms.",
                seed_now,
                None,
            ),
        )
        conn.execute(
            """INSERT INTO projects (id, title, description, image, tags, content, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                "project-2",
                "Hybrid Diff & Motor Mount",
                "Combined CNC aluminum and printed housings for a stronger, serviceable "
                "drivetrain that still fits the original chassis envelope.",
                "assets/parts.png",
                json.dumps(["Hybrid Mfg", "CAD", "Drivetrain"]),
                "Pure-printed drivetrain housings kept stripping under load, so this build "
                "mixes CNC aluminum inserts at the high-stress interfaces with printed "
                "housings everywhere else.\n\n"
                "The aluminum carries the bearing loads and motor mount screws; the printed "
                "shell handles everything else and keeps the whole assembly serviceable — "
                "no more replacing an entire metal housing over one worn bearing seat.\n\n"
                "Fits the stock chassis envelope with no cutting required.",
                seed_now - 1,
                None,
            ),
        )
    conn.commit()
    conn.close()

    if not PAYPAL_CONFIG_PATH.exists():
        with open(PAYPAL_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump({"mode": "sandbox", "client_id": "", "secret": "", "currency": "USD"}, f, indent=2)

    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Rabithole server running at http://localhost:{PORT}")
    print(f"Database: {DB_PATH}")
    print("Default login - username: admin  password: rabithole")
    paypal_config = load_paypal_config()
    if not paypal_config["client_id"] or not paypal_config["secret"]:
        print("PayPal is NOT configured - edit paypal_config.json with your Client ID/Secret to enable checkout.")
    else:
        print(f"PayPal checkout enabled ({paypal_config['mode']} mode)")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()


if __name__ == "__main__":
    main()
