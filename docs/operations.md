# Running the store (owner guide)

Everyday tasks, mostly done from the **Admin** page (`/admin.html`) after
signing in at `/login.html`.

## Owner accounts

From the server (or locally) in the project folder:

```bash
python manage_users.py list
python manage_users.py add --username bob --display-name "Bob"
python manage_users.py update --username admin --new-username owner
python manage_users.py update --username owner --new-password      # prompts
python manage_users.py delete --username bob
```

Leave out `--password` / `--new-password` values to be prompted, so passwords
don't end up in shell history. You can also change your own username, display
name and password from **Account** (`/account.html`).

Locked out after too many tries? The limit is 8 failed logins per IP in 15
minutes; wait it out, or restart the server to clear it.

## Products

- **Add:** Admin → Add Product. New products start as **Draft** (not visible).
- **Publish:** Manage Products → set it to Published.
- **Stock:** set a number to limit sales, or leave it blank for unlimited.
  When stock hits 0 after a sale, the product switches to **Sold Out** by itself.
- **Backordered:** shoppers can request a Sold Out item. After 3 requests it
  shows as Backordered. Changing the status back to Available
  (restocking) resets the count.
- Images: PNG/JPG/WEBP/GIF, up to 8 MB. Image descriptions (alt text) are up to 500 characters.

## Pausing sales

Admin → pause sales. Checkout is blocked site-wide and a banner shows on the
shop. Products stay visible. Turn it back on the same way.

## Orders

**Orders** (`/orders.html`) lists every PayPal checkout with the buyer, the
shipping address and the items.

1. Pack the order.
2. Enter the tracking number.
3. Set the status: Pending → Shipped → Delivered (or Cancelled).

Refunds are done in your **PayPal dashboard**; the site doesn't issue refunds.
Mark the order Cancelled afterwards.

## Custom work requests

**Custom Requests** (`/manage-requests.html`) lists submissions from the
request form. Reply to the customer by email, then set the status to In Progress,
Completed or Declined.

## Blog and projects

- Posts and projects also start as **Draft**; publish them when ready.
- Blog categories are created and deleted from Manage Posts or the post editor.
- Project galleries can have a caption on each photo.

## Homepage Quick Stats

Admin → Quick Stats (bottom of the page):

- **On/off:** show or hide the stats row.
- **Custom:** type all four values yourself.
- **Live:** tile 1 counts completed sales and tile 3 counts projects
  automatically; tiles 2 and 4 keep your text.
- **Reset** restores the original values.

## PayPal

`paypal_config.json` on the server (never commit it):

```json
{ "mode": "live", "client_id": "…", "secret": "…", "currency": "USD" }
```

- Use `"mode": "sandbox"` with sandbox keys from
  https://developer.paypal.com/dashboard/applications to test with fake money.
- Restart the server after editing (or let the next deploy restart it).
- Your local copy should stay on **sandbox**.

## Backups

See [database.md → Backups](database.md#backups). Back up `db/rabithole.db`
and `assets/uploads/` from the server.
