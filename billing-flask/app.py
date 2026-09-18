import io
import csv
import logging
import os
from logging.handlers import RotatingFileHandler
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from functools import wraps

from flask import Flask, abort, flash, jsonify, redirect, render_template, request, send_file, session, url_for, Response
from dotenv import load_dotenv
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_migrate import Migrate
from sqlalchemy import func, text

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from models import AuditLog, Category, Customer, InvoiceCounter, Payment, Product, Sale, StockMovement, User, db
from models import Purchase, PurchaseItem, Refund
from pdf_invoice import build_invoice_pdf
from config import BASE_DIR, config_for_environment

load_dotenv(os.path.join(BASE_DIR, ".env"))
load_dotenv(os.path.join(os.path.dirname(BASE_DIR), ".env"))
APP_NAME = os.environ.get("APP_NAME", "Billing Pro")

migrate = Migrate()
limiter = Limiter(key_func=get_remote_address)


def configure_logging(app):
    os.makedirs(app.config["LOG_DIR"], exist_ok=True)
    handler = RotatingFileHandler(os.path.join(app.config["LOG_DIR"], "billing.log"), maxBytes=2_000_000, backupCount=5)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    handler.setLevel(logging.INFO)
    app.logger.addHandler(handler)
    app.logger.setLevel(logging.INFO)


def create_app(config_class=None):
    application = Flask(__name__)
    application.config.from_object(config_class or config_for_environment())
    db.init_app(application)
    migrate.init_app(application, db)
    limiter.init_app(application)
    configure_logging(application)
    from api import api_bp
    application.register_blueprint(api_bp)
    return application


app = create_app()


@app.context_processor
def inject_brand():
    return {"app_name": APP_NAME}


def login_required(roles=None):
    allowed = {roles} if isinstance(roles, str) else set(roles or [])

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = db.session.get(User, session.get("user_id"))
            if not user or not user.active:
                session.clear()
                return redirect(url_for("login", next=request.path))
            if allowed and user.role not in allowed:
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


def current_user():
    return db.session.get(User, session.get("user_id"))


def cart_items():
    return session.setdefault("cart", [])


def money(value, default=Decimal("0")):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return default


def record_audit(action, module, record_id="", description=""):
    user = current_user()
    if user:
        db.session.add(AuditLog(user_email=user.email, user_id=user.id, action=action, module=module, record_id=str(record_id), target_type=module, target_id=str(record_id), description=description, details=description, ip_address=request.remote_addr))


@app.route("/")
def index():
    if not session.get("user_id"):
        return redirect(url_for("login"))
    return redirect(url_for("admin_dashboard" if session.get("role") in {"admin", "manager"} else "invoice_page"))


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("5 per minute", methods=["POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email, active=True, is_deleted=False).first()
        if user and user.locked_until and user.locked_until > datetime.utcnow():
            app.logger.warning("login_locked email=%s ip=%s", email, request.remote_addr)
            flash("This account is temporarily locked. Try again later.", "error")
        elif user and user.check_password(password):
            user.failed_login_attempts = 0
            user.locked_until = None
            db.session.commit()
            app.logger.info("login_success email=%s ip=%s", email, request.remote_addr)
            session.clear()
            session.update(user_id=user.id, role=user.role, email=user.email, cart=[])
            return redirect(request.args.get("next") or url_for("index"))
        else:
            if user:
                user.failed_login_attempts += 1
                if user.failed_login_attempts >= int(os.environ.get("LOGIN_MAX_ATTEMPTS", "5")):
                    user.locked_until = datetime.utcnow() + timedelta(minutes=int(os.environ.get("LOGIN_LOCKOUT_MINUTES", "15")))
                db.session.commit()
            app.logger.warning("login_failure email=%s ip=%s", email, request.remote_addr)
            flash("The email or password is incorrect.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


def password_serializer():
    return URLSafeTimedSerializer(app.config["SECRET_KEY"], salt="caddey-password-reset")


@app.route("/reset-password/request", methods=["GET", "POST"])
def request_password_reset():
    reset_link = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = User.query.filter_by(email=email, active=True, is_deleted=False).first()
        if user:
            token = password_serializer().dumps(user.email)
            reset_link = url_for("reset_password", token=token, _external=True)
            app.logger.info("password_reset_requested email=%s", email)
        flash("If that account exists, a reset link has been generated.", "success")
    return render_template("reset_password_request.html", reset_link=reset_link)


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    try:
        email = password_serializer().loads(token, max_age=3600)
    except (BadSignature, SignatureExpired):
        return render_template("error.html", code=400, message="This password reset link is invalid or expired."), 400
    user = User.query.filter_by(email=email, active=True, is_deleted=False).first_or_404()
    if request.method == "POST":
        password = request.form.get("password", "")
        if len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
        else:
            user.set_password(password)
            user.failed_login_attempts = 0
            user.locked_until = None
            db.session.commit()
            flash("Password updated. You can sign in now.", "success")
            return redirect(url_for("login"))
    return render_template("reset_password.html")


@app.route("/admin")
@login_required({"admin", "manager"})
def admin_dashboard():
    today = date.today().strftime("%Y/%m/%d")
    sales_today = Sale.query.filter_by(date=today).all()
    revenue = sum((money(s.final_price) for s in sales_today), Decimal("0"))
    cost = sum((money(s.unit_price) * s.quantity for s in sales_today), Decimal("0"))
    products = Product.query.filter_by(active=True, is_deleted=False).order_by(Product.detail).all()
    stats = {"sales": revenue, "orders": len({s.invoice_no for s in sales_today}), "products": len(products), "low_stock": sum(p.quantity <= p.reorder_level for p in products), "profit": revenue - cost}
    return render_template("admin/dashboard.html", products=products, users=User.query.filter_by(is_deleted=False).order_by(User.email).all(), categories=Category.query.order_by(Category.name).all(), customers=Customer.query.order_by(Customer.name).all(), sales=sales_today, stats=stats)


@app.route("/admin/products/add", methods=["POST"])
@login_required({"admin", "manager"})
def admin_add_product():
    product_id = request.form.get("product_id", "").strip()
    detail = request.form.get("detail", "").strip()
    quantity_text = request.form.get("quantity", "0").strip()
    selling_price = money(request.form.get("selling_price", request.form.get("unit_price", "0")), Decimal("-1"))
    if not product_id or not detail:
        flash("Product code and name are required.", "error")
    elif Product.query.filter_by(product_id=product_id).first():
        flash("That product code already exists.", "error")
    elif not quantity_text.isdigit() or selling_price < 0:
        flash("Quantity and price must be zero or greater.", "error")
    else:
        quantity = int(quantity_text)
        product = Product(product_id=product_id, detail=detail, company=request.form.get("company", "General").strip() or "General", sku=request.form.get("sku") or product_id, barcode=request.form.get("barcode") or None, quantity=quantity, selling_price=selling_price, purchase_price=money(request.form.get("purchase_price", 0)), tax_rate=money(request.form.get("tax_rate", 0)), reorder_level=int(request.form.get("reorder_level", 5) or 5))
        db.session.add(product)
        db.session.flush()
        if quantity:
            db.session.add(StockMovement(product_id=product.id, movement_type="opening", quantity=quantity, created_by=session.get("email")))
        record_audit("create", "products", product_id, "Product created")
        db.session.commit()
        flash("Product added.", "success")
    return redirect(url_for("admin_dashboard") + "#products")


@app.route("/admin/products/update", methods=["POST"])
@login_required({"admin", "manager"})
def admin_update_product():
    product = Product.query.filter_by(product_id=request.form.get("product_id", "").strip()).first()
    if not product:
        flash("Product not found.", "error")
    else:
        old_quantity = product.quantity
        product.detail = request.form.get("detail", product.detail).strip() or product.detail
        product.company = request.form.get("company", product.company).strip() or product.company
        if request.form.get("quantity", "").strip():
            product.quantity = max(0, int(request.form["quantity"]))
        if request.form.get("selling_price", "").strip():
            product.selling_price = money(request.form["selling_price"])
        if product.quantity != old_quantity:
            db.session.add(StockMovement(product_id=product.id, movement_type="adjustment", quantity=product.quantity - old_quantity, created_by=session.get("email")))
        record_audit("update", "products", product.product_id, "Product updated")
        db.session.commit()
        flash("Product updated.", "success")
    return redirect(url_for("admin_dashboard") + "#products")


@app.route("/admin/products/delete", methods=["POST"])
@login_required({"admin"})
def admin_delete_product():
    product = Product.query.filter_by(product_id=request.form.get("product_id", "").strip()).first()
    if product:
        product.active = False
        product.is_deleted = True
        product.deleted_at = datetime.utcnow()
        record_audit("archive", "products", product.product_id, "Product archived")
        db.session.commit()
        flash("Product archived.", "success")
    else:
        flash("Product not found.", "error")
    return redirect(url_for("admin_dashboard") + "#products")


@app.route("/admin/products/search")
@login_required({"admin", "manager", "cashier"})
def admin_search_product():
    term = request.args.get("product_id", "").strip()
    product = Product.query.filter(Product.active.is_(True), Product.is_deleted.is_(False), (Product.product_id == term) | (Product.sku == term) | (Product.barcode == term) | (Product.detail.ilike(f"%{term}%"))).first()
    return jsonify({"found": bool(product), **(product.to_dict() if product else {"message": "No matching product found."})})


@app.route("/admin/cashiers/add", methods=["POST"])
@login_required({"admin"})
def admin_add_cashier():
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    if not email or len(password) < 8:
        flash("Use a valid email and a password of at least 8 characters.", "error")
    elif User.query.filter_by(email=email).first():
        flash("That user already exists.", "error")
    else:
        user = User(email=email, name=request.form.get("name", "Cashier"), role=request.form.get("role", "cashier"))
        user.set_password(password)
        db.session.add(user)
        record_audit("create", "users", email, "User created")
        db.session.commit()
        flash("User added.", "success")
    return redirect(url_for("admin_dashboard") + "#users")


@app.route("/admin/cashiers/delete", methods=["POST"])
@login_required({"admin"})
def admin_delete_cashier():
    user = User.query.filter_by(email=request.form.get("email", "").strip().lower()).first()
    if user and user.id != session.get("user_id"):
        user.active = False
        user.is_deleted = True
        user.deleted_at = datetime.utcnow()
        record_audit("deactivate", "users", user.email, "User deactivated")
        db.session.commit()
        flash("User deactivated.", "success")
    else:
        flash("User could not be deactivated.", "error")
    return redirect(url_for("admin_dashboard") + "#users")


@app.route("/admin/cashiers/search")
@login_required({"admin"})
def admin_search_cashier():
    user = User.query.filter_by(email=request.args.get("email", "").strip().lower(), active=True, is_deleted=False).first()
    return jsonify({"found": bool(user), "email": user.email if user else "", "message": "No active user found." if not user else ""})


@app.route("/admin/stock")
@login_required({"admin", "manager"})
def admin_stock():
    products = Product.query.filter_by(active=True, is_deleted=False).order_by(Product.quantity, Product.detail).all()
    return render_template("admin/stock.html", products=products, companies=sorted({p.company for p in products}), selected="All")


@app.route("/admin/sales")
@login_required({"admin", "manager"})
def admin_sales():
    sales = Sale.query.order_by(Sale.created_at.desc()).limit(250).all()
    return render_template("admin/sales.html", sales=sales, companies=sorted({s.company for s in sales if s.company}), selected_company="All", selected_date="")


@app.route("/admin/audit-log")
@login_required("admin")
def audit_log():
    entries = AuditLog.query.order_by(AuditLog.created_at.desc()).limit(500).all()
    return render_template("admin/audit_log.html", entries=entries)


@app.route("/admin/sales/<int:sale_id>/refund", methods=["POST"])
@login_required("admin")
def refund_sale(sale_id):
    sale = db.get_or_404(Sale, sale_id)
    try:
        requested = int(request.form.get("quantity", "0"))
    except ValueError:
        requested = 0
    refunded = db.session.query(func.coalesce(func.sum(Refund.refunded_quantity), 0)).filter(Refund.original_sale_id == sale.id).scalar() or 0
    remaining = sale.quantity - int(refunded)
    if requested < 1 or requested > remaining:
        flash(f"Refund quantity must be between 1 and {remaining}.", "error")
    else:
        product = Product.query.filter_by(product_id=sale.product_id).first()
        if product:
            product.quantity += requested
        amount = money(sale.unit_price) * requested
        db.session.add(Refund(original_sale_id=sale.id, refunded_quantity=requested, refund_amount=amount, reason=request.form.get("reason", "Customer refund").strip() or "Customer refund", refunded_by=session.get("user_id")))
        record_audit("create", "refunds", sale.id, f"Refunded {requested} unit(s) from sale {sale.invoice_no}")
        db.session.commit()
        app.logger.info("refund_created sale_id=%s quantity=%s user=%s", sale.id, requested, session.get("email"))
        flash("Refund recorded and stock restored.", "success")
    return redirect(url_for("admin_sales"))


@app.route("/admin/purchases/add", methods=["POST"])
@login_required({"admin", "manager"})
def record_purchase():
    product = Product.query.filter_by(product_id=request.form.get("product_id", "").strip(), is_deleted=False).first()
    try:
        quantity = int(request.form.get("quantity", "0"))
        cost_price = money(request.form.get("cost_price", "0"), Decimal("-1"))
    except (TypeError, ValueError):
        quantity, cost_price = 0, Decimal("-1")
    if not product or quantity < 1 or cost_price < 0:
        flash("Choose a product and enter a valid quantity and cost price.", "error")
    else:
        supplier_name = request.form.get("supplier", "General supplier").strip() or "General supplier"
        supplier = Supplier.query.filter_by(name=supplier_name).first() or Supplier(name=supplier_name)
        db.session.add(supplier)
        db.session.flush()
        purchase = Purchase(supplier_id=supplier.id, created_by=session.get("user_id"))
        db.session.add(purchase)
        db.session.flush()
        db.session.add(PurchaseItem(purchase_id=purchase.id, product_id=product.id, quantity=quantity, cost_price=cost_price))
        product.quantity += quantity
        product.purchase_price = cost_price
        db.session.add(StockMovement(product_id=product.id, movement_type="purchase", quantity=quantity, reference=str(purchase.id), created_by=session.get("email")))
        record_audit("create", "purchases", purchase.id, f"Purchased {quantity} unit(s) of {product.product_id}")
        db.session.commit()
        app.logger.info("purchase_created purchase_id=%s product=%s quantity=%s", purchase.id, product.product_id, quantity)
        flash("Purchase recorded and stock increased.", "success")
    return redirect(url_for("admin_stock"))


@app.route("/admin/customers/add", methods=["POST"])
@login_required({"admin", "manager", "cashier"})
def add_customer():
    name = request.form.get("name", "").strip()
    if not name:
        flash("Customer name is required.", "error")
    else:
        db.session.add(Customer(name=name, phone=request.form.get("phone", "").strip(), email=request.form.get("email", "").strip()))
        db.session.commit()
        flash("Customer added.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/invoice")
@login_required({"admin", "manager", "cashier"})
def invoice_page():
    items = cart_items()
    subtotal = sum((money(item["unit_price"]) * item["quantity"] for item in items), Decimal("0"))
    tax = sum((money(item.get("tax", 0)) for item in items), Decimal("0"))
    return render_template("cashier/invoice.html", cart=items, products=Product.query.filter_by(active=True).order_by(Product.detail).all(), customers=Customer.query.filter_by(active=True).order_by(Customer.name).all(), subtotal=subtotal, tax=tax, total=subtotal + tax)


@app.route("/invoice/search")
@login_required({"admin", "manager", "cashier"})
def invoice_search_product():
    return admin_search_product()


@app.route("/invoice/add", methods=["POST"])
@login_required({"admin", "manager", "cashier"})
def invoice_add_item():
    term = request.form.get("product_id", "").strip()
    product = Product.query.filter(Product.active.is_(True), (Product.product_id == term) | (Product.sku == term) | (Product.barcode == term)).first()
    quantity = int(request.form.get("quantity", 0) or 0)
    current_quantity = sum(item["quantity"] for item in cart_items() if item["product_id"] == (product.product_id if product else ""))
    if not product or quantity < 1:
        flash("Choose a valid product and quantity.", "error")
    elif current_quantity + quantity > product.quantity:
        flash(f"Only {product.quantity} units are in stock.", "error")
    else:
        item = next((item for item in cart_items() if item["product_id"] == product.product_id), None)
        if item:
            item["quantity"] += quantity
        else:
            unit_price = money(request.form.get("unit_price") or product.selling_price)
            tax = unit_price * quantity * money(product.tax_rate) / 100
            cart_items().append({"product_id": product.product_id, "detail": product.detail, "company": product.company, "unit_price": str(unit_price), "quantity": quantity, "tax_rate": str(product.tax_rate or 0), "tax": str(tax)})
        session.modified = True
        flash("Item added to cart.", "success")
    return redirect(url_for("invoice_page"))


@app.route("/invoice/remove", methods=["POST"])
@login_required({"admin", "manager", "cashier"})
def invoice_remove_item():
    pid = request.form.get("product_id", "")
    session["cart"] = [item for item in cart_items() if item["product_id"] != pid]
    return redirect(url_for("invoice_page"))


@app.route("/invoice/clear", methods=["POST"])
@login_required({"admin", "manager", "cashier"})
def invoice_clear():
    session["cart"] = []
    return redirect(url_for("invoice_page"))


@app.route("/invoice/checkout", methods=["POST"])
@login_required({"admin", "manager", "cashier"})
def invoice_checkout():
    items = cart_items()
    if not items:
        flash("Add at least one item before checkout.", "error")
        return redirect(url_for("invoice_page"))
    payment_method = request.form.get("payment_method", "cash")
    customer_name = request.form.get("customer_name", "Walk-in Customer").strip() or "Walk-in Customer"
    try:
        invoice_no = InvoiceCounter.next()
        total = Decimal("0")
        for item in items:
            product = Product.query.filter_by(product_id=item["product_id"], active=True).first()
            if not product or product.quantity < item["quantity"]:
                raise ValueError(f"Insufficient stock for {item['detail']}.")
            unit_price = money(item["unit_price"])
            tax = unit_price * item["quantity"] * money(product.tax_rate) / 100
            line_total = unit_price * item["quantity"] + tax
            total += line_total
            product.quantity -= item["quantity"]
            db.session.add(Sale(invoice_no=invoice_no, date=date.today().strftime("%Y/%m/%d"), product_id=product.product_id, detail=product.detail, company=product.company, unit_price=unit_price, quantity=item["quantity"], tax=tax, final_price=line_total, cashier_name=session.get("email"), customer_name=customer_name, payment_method=payment_method, status="paid" if payment_method != "credit" else "credit"))
            db.session.add(StockMovement(product_id=product.id, movement_type="sale", quantity=-item["quantity"], reference=str(invoice_no), created_by=session.get("email")))
        db.session.add(Payment(invoice_no=invoice_no, amount=total, method=payment_method, reference=request.form.get("reference", "").strip(), created_by=session.get("email")))
        record_audit("create", "sales", invoice_no, f"Invoice {invoice_no} created")
        db.session.commit()
        session["cart"] = []
        total = sum((money(item["unit_price"]) * item["quantity"] + money(item.get("tax", 0)) for item in items), Decimal("0"))
        return send_file(io.BytesIO(build_invoice_pdf(items, total, invoice_no, customer_name)), mimetype="application/pdf", as_attachment=True, download_name=f"billing-pro-{invoice_no}.pdf")
    except (ValueError, InvalidOperation) as error:
        db.session.rollback()
        flash(str(error), "error")
        return redirect(url_for("invoice_page"))


@app.route("/reports")
@login_required({"admin", "manager"})
def reports():
    start = date.today() - timedelta(days=29)
    sales = Sale.query.filter(Sale.created_at >= datetime.combine(start, datetime.min.time())).all()
    revenue = sum((money(s.final_price) for s in sales), Decimal("0"))
    products = Product.query.filter_by(active=True).all()
    stats = {"sales": revenue, "orders": len({s.invoice_no for s in sales}), "products": len(products), "low_stock": sum(p.quantity <= p.reorder_level for p in products), "profit": revenue - sum((money(s.unit_price) * s.quantity for s in sales), Decimal("0"))}
    return render_template("admin/dashboard.html", products=products, users=[], categories=[], customers=[], sales=sales, stats=stats, report_mode=True)


@app.route("/healthz")
def healthz():
    try:
        db.session.execute(text("SELECT 1"))
        return jsonify({"status": "ok", "database": "ok"}), 200
    except Exception:
        app.logger.exception("healthcheck_failed")
        return jsonify({"status": "error", "database": "unavailable"}), 503


@app.route("/admin/stock/low")
@login_required({"admin", "manager"})
def low_stock():
    products = Product.query.filter(Product.active.is_(True), Product.is_deleted.is_(False), Product.quantity <= Product.reorder_threshold).order_by(Product.quantity).all()
    return jsonify({"count": len(products), "products": [p.to_dict() for p in products]})


@app.route("/admin/reports/revenue")
@login_required({"admin", "manager"})
def revenue_report():
    period = request.args.get("period", "daily")
    if period == "monthly":
        bucket = func.strftime("%Y-%m", Sale.created_at)
    elif period == "weekly":
        bucket = func.strftime("%Y-W%W", Sale.created_at)
    else:
        bucket = func.strftime("%Y-%m-%d", Sale.created_at)
    rows = db.session.query(bucket.label("period"), func.sum(Sale.final_price).label("revenue"), func.count(func.distinct(Sale.invoice_no)).label("orders")).group_by(bucket).order_by(bucket).all()
    return jsonify({"period": period, "data": [{"period": row.period, "revenue": float(row.revenue or 0), "orders": row.orders} for row in rows]})


@app.route("/admin/reports/top-products")
@login_required({"admin", "manager"})
def top_products_report():
    rows = db.session.query(Sale.product_id, Sale.detail, func.sum(Sale.quantity).label("quantity"), func.sum(Sale.final_price).label("revenue")).group_by(Sale.product_id, Sale.detail).order_by(func.sum(Sale.quantity).desc()).limit(10).all()
    return jsonify({"data": [{"product_id": row.product_id, "detail": row.detail, "quantity": row.quantity, "revenue": float(row.revenue or 0)} for row in rows]})


@app.route("/admin/reports/by-cashier")
@login_required({"admin", "manager"})
def cashier_report():
    rows = db.session.query(Sale.cashier_name, func.count(func.distinct(Sale.invoice_no)).label("orders"), func.sum(Sale.final_price).label("revenue")).group_by(Sale.cashier_name).order_by(func.sum(Sale.final_price).desc()).all()
    return jsonify({"data": [{"cashier": row.cashier_name, "orders": row.orders, "revenue": float(row.revenue or 0)} for row in rows]})


@app.route("/admin/reports/stock-valuation")
@login_required({"admin", "manager"})
def stock_valuation_report():
    value = db.session.query(func.sum(Product.quantity * Product.purchase_price)).filter(Product.active.is_(True), Product.is_deleted.is_(False)).scalar() or 0
    return jsonify({"stock_valuation": float(value), "currency": "INR"})


def csv_response(filename, headers, rows):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    writer.writerows(rows)
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename={filename}"})


@app.route("/admin/stock/export.csv")
@login_required({"admin", "manager"})
def export_stock():
    products = Product.query.filter_by(active=True, is_deleted=False).order_by(Product.detail).all()
    return csv_response("stock.csv", ["Product ID", "Detail", "Company", "Quantity", "Selling Price"], [[p.product_id, p.detail, p.company, p.quantity, p.selling_price] for p in products])


@app.route("/admin/sales/export.csv")
@login_required({"admin", "manager"})
def export_sales():
    sales = Sale.query.order_by(Sale.created_at.desc()).all()
    return csv_response("sales.csv", ["Invoice", "Date", "Product ID", "Detail", "Company", "Quantity", "Final Price", "Cashier"], [[s.invoice_no, s.date, s.product_id, s.detail, s.company, s.quantity, s.final_price, s.cashier_name] for s in sales])


@app.errorhandler(403)
def forbidden(_error):
    return render_template("error.html", code=403, message="You do not have permission to access this area."), 403


@app.errorhandler(404)
def not_found(_error):
    return render_template("error.html", code=404, message="The page you requested could not be found."), 404


@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    app.logger.exception("unhandled_exception: %s", error)
    return render_template("error.html", code=500, message="Something went wrong. Please try again."), 500


def seed_data():
    os.makedirs(os.path.join(BASE_DIR, "instance"), exist_ok=True)
    db.create_all()
    if not User.query.filter_by(email="admin@billingpro.local").first():
        admin = User(email="admin@billingpro.local", name="Administrator", role="admin")
        admin.set_password(os.environ.get("ADMIN_PASSWORD", "admin12345"))
        db.session.add(admin)
    if not User.query.filter_by(email="cashier@billingpro.local").first():
        cashier = User(email="cashier@billingpro.local", name="Front counter", role="cashier")
        cashier.set_password(os.environ.get("CASHIER_PASSWORD", "cashier123"))
        db.session.add(cashier)
    if not Category.query.first():
        db.session.add_all([Category(name="Electronics"), Category(name="Stationery"), Category(name="Accessories")])
    if not Product.query.first():
        db.session.add_all([Product(product_id="P001", sku="P001", detail="Wireless Mouse", company="Accessories", quantity=45, selling_price=Decimal("18.00"), purchase_price=Decimal("10.00"), reorder_level=8), Product(product_id="P002", sku="P002", detail="A4 Paper Ream", company="Stationery", quantity=60, selling_price=Decimal("7.50"), purchase_price=Decimal("4.50"), reorder_level=10), Product(product_id="P003", sku="P003", detail="Ball Point Pen", company="Stationery", quantity=150, selling_price=Decimal("1.25"), purchase_price=Decimal("0.45"), reorder_level=20)])
    if not Customer.query.first():
        db.session.add(Customer(name="Walk-in Customer"))
    if not InvoiceCounter.query.first():
        db.session.add(InvoiceCounter(next_invoice_no=1001))
    db.session.commit()


if __name__ == "__main__":
    with app.app_context():
        seed_data()
    app.run(debug=os.environ.get("FLASK_DEBUG", "0") == "1")
