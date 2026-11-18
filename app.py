import os, json, sqlite3, uuid, re
from datetime import datetime
from functools import wraps

import numpy as np
from PIL import Image, ImageOps
from flask import Flask, render_template, request, redirect, url_for, flash, session, g, abort, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, "lost_found.db")
UPLOAD_DIR = os.path.join(BASE_DIR, "static", "uploads")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
MAX_IMAGE_SIZE = 8 * 1024 * 1024
MATCH_THRESHOLD = 0.62

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-this-secret-key-before-deployment")
app.config["MAX_CONTENT_LENGTH"] = MAX_IMAGE_SIZE
os.makedirs(UPLOAD_DIR, exist_ok=True)

_model = None
_model_error = None

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db

@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()

def init_db():
    db = sqlite3.connect(DB_PATH)
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT NOT NULL UNIQUE,
        student_id TEXT NOT NULL,
        department TEXT DEFAULT '',
        password_hash TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        item_type TEXT NOT NULL CHECK(item_type IN ('lost','found')),
        title TEXT NOT NULL,
        category TEXT NOT NULL,
        description TEXT NOT NULL,
        location TEXT NOT NULL,
        event_date TEXT NOT NULL,
        image_filename TEXT NOT NULL,
        embedding TEXT,
        status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','matched','resolved')),
        created_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        item_id INTEGER,
        message TEXT NOT NULL,
        is_read INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY(item_id) REFERENCES items(id) ON DELETE SET NULL
    );
    CREATE TABLE IF NOT EXISTS matches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        lost_item_id INTEGER NOT NULL,
        found_item_id INTEGER NOT NULL,
        similarity REAL NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(lost_item_id, found_item_id),
        FOREIGN KEY(lost_item_id) REFERENCES items(id) ON DELETE CASCADE,
        FOREIGN KEY(found_item_id) REFERENCES items(id) ON DELETE CASCADE
    );
    """)
    db.commit()
    db.close()

def load_model():
    global _model, _model_error
    if _model is not None:
        return _model
    if _model_error:
        raise RuntimeError(_model_error)
    try:
        # The ImageNet weights are downloaded automatically on first launch.
        from tensorflow.keras.applications import MobileNetV2
        from tensorflow.keras.applications.mobilenet_v2 import preprocess_input
        from tensorflow.keras.models import Model
        base = MobileNetV2(weights="imagenet", include_top=False, pooling="avg",
                           input_shape=(224, 224, 3))
        _model = (Model(inputs=base.input, outputs=base.output), preprocess_input)
        return _model
    except Exception as exc:
        _model_error = ("Could not load MobileNetV2. Check your internet connection on first run "
                        "and verify TensorFlow is installed. Details: " + str(exc))
        raise RuntimeError(_model_error)

def image_embedding(image_path):
    model, preprocess_input = load_model()
    image = Image.open(image_path).convert("RGB")
    image = ImageOps.fit(image, (224, 224))
    arr = np.asarray(image, dtype=np.float32)
    arr = np.expand_dims(arr, axis=0)
    arr = preprocess_input(arr)
    vector = model.predict(arr, verbose=0)[0].astype(np.float32)
    norm = np.linalg.norm(vector)
    if norm:
        vector = vector / norm
    return vector.tolist()

def cosine_similarity(a, b):
    va, vb = np.asarray(a, dtype=np.float32), np.asarray(b, dtype=np.float32)
    denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
    return float(np.dot(va, vb) / denom) if denom else 0.0

def current_user():
    if "user_id" not in session:
        return None
    return get_db().execute("SELECT id,name,email,student_id,department FROM users WHERE id=?",
                            (session["user_id"],)).fetchone()

@app.context_processor
def inject_globals():
    user = current_user()
    unread = 0
    if user:
        unread = get_db().execute("SELECT COUNT(*) FROM notifications WHERE user_id=? AND is_read=0",
                                  (user["id"],)).fetchone()[0]
    return {"current_user": user, "unread_notifications": unread}

def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not current_user():
            flash("Please log in to continue.", "info")
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapped

def notify(user_id, item_id, message):
    get_db().execute("INSERT INTO notifications(user_id,item_id,message,created_at) VALUES(?,?,?,?)",
                     (user_id, item_id, message, datetime.now().isoformat(timespec="seconds")))

def run_matching(new_item_id):
    db = get_db()
    new_item = db.execute("SELECT * FROM items WHERE id=?", (new_item_id,)).fetchone()
    if not new_item or not new_item["embedding"]:
        return
    opposite = "found" if new_item["item_type"] == "lost" else "lost"
    candidates = db.execute("""
        SELECT * FROM items WHERE item_type=? AND status!='resolved'
        AND embedding IS NOT NULL AND user_id != ?
    """, (opposite, new_item["user_id"])).fetchall()
    for candidate in candidates:
        try:
            score = cosine_similarity(json.loads(new_item["embedding"]), json.loads(candidate["embedding"]))
        except Exception:
            continue
        if score < MATCH_THRESHOLD:
            continue
        lost_id = new_item_id if new_item["item_type"] == "lost" else candidate["id"]
        found_id = new_item_id if new_item["item_type"] == "found" else candidate["id"]
        cur = db.execute("INSERT OR IGNORE INTO matches(lost_item_id,found_item_id,similarity,created_at) VALUES(?,?,?,?)",
                         (lost_id, found_id, score, datetime.now().isoformat(timespec="seconds")))
        if cur.rowcount:
            lost = db.execute("SELECT title,user_id FROM items WHERE id=?", (lost_id,)).fetchone()
            found = db.execute("SELECT title,user_id FROM items WHERE id=?", (found_id,)).fetchone()
            pct = round(score * 100)
            notify(lost["user_id"], found_id,
                   f"Possible match found for your lost item “{lost['title']}” ({pct}% visual similarity).")
            notify(found["user_id"], lost_id,
                   f"Your found item “{found['title']}” may match a lost report ({pct}% visual similarity).")
    db.commit()

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route("/")
def index():
    db = get_db()
    latest = db.execute("""
        SELECT items.*, users.name AS owner_name FROM items
        JOIN users ON users.id=items.user_id
        WHERE items.status!='resolved' ORDER BY items.created_at DESC LIMIT 6
    """).fetchall()
    stats = {
        "lost": db.execute("SELECT COUNT(*) FROM items WHERE item_type='lost' AND status!='resolved'").fetchone()[0],
        "found": db.execute("SELECT COUNT(*) FROM items WHERE item_type='found' AND status!='resolved'").fetchone()[0],
        "matches": db.execute("SELECT COUNT(*) FROM matches").fetchone()[0],
    }
    return render_template("index.html", latest=latest, stats=stats)

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        student_id = request.form.get("student_id", "").strip()
        department = request.form.get("department", "").strip()
        password = request.form.get("password", "")
        if not all([name, email, student_id, password]):
            flash("Please complete all required fields.", "error")
        elif len(password) < 8:
            flash("Password must contain at least 8 characters.", "error")
        elif not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            flash("Enter a valid email address.", "error")
        else:
            try:
                db = get_db()
                db.execute("""INSERT INTO users(name,email,student_id,department,password_hash,created_at)
                              VALUES(?,?,?,?,?,?)""",
                           (name, email, student_id, department, generate_password_hash(password),
                            datetime.now().isoformat(timespec="seconds")))
                db.commit()
                flash("Account created. You can now log in.", "success")
                return redirect(url_for("login"))
            except sqlite3.IntegrityError:
                flash("An account with that email already exists.", "error")
    return render_template("auth.html", mode="register")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = get_db().execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            flash(f"Welcome back, {user['name'].split()[0]}.", "success")
            return redirect(url_for("dashboard"))
        flash("Incorrect email or password.", "error")
    return render_template("auth.html", mode="login")

@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("index"))

@app.route("/dashboard")
@login_required
def dashboard():
    db = get_db()
    user = current_user()
    items = db.execute("SELECT * FROM items WHERE user_id=? ORDER BY created_at DESC LIMIT 8",
                       (user["id"],)).fetchall()
    matches = db.execute("""
        SELECT m.*, l.title AS lost_title, l.image_filename AS lost_image,
               f.title AS found_title, f.image_filename AS found_image,
               CASE WHEN l.user_id=? THEN f.id ELSE l.id END AS other_item_id,
               CASE WHEN l.user_id=? THEN f.title ELSE l.title END AS other_title,
               CASE WHEN l.user_id=? THEN f.image_filename ELSE l.image_filename END AS other_image
        FROM matches m JOIN items l ON l.id=m.lost_item_id
        JOIN items f ON f.id=m.found_item_id
        WHERE l.user_id=? OR f.user_id=? ORDER BY m.created_at DESC LIMIT 5
    """, (user["id"], user["id"], user["id"], user["id"], user["id"])).fetchall()
    return render_template("dashboard.html", items=items, matches=matches)

@app.route("/items/new/<item_type>", methods=["GET", "POST"])
@login_required
def new_item(item_type):
    if item_type not in ("lost", "found"):
        abort(404)
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        category = request.form.get("category", "").strip()
        description = request.form.get("description", "").strip()
        location = request.form.get("location", "").strip()
        event_date = request.form.get("event_date", "").strip()
        image = request.files.get("image")
        if not all([title, category, description, location, event_date, image]):
            flash("Please complete all fields and select an image.", "error")
            return render_template("item_form.html", item_type=item_type)
        if not image.filename or not allowed_file(image.filename):
            flash("Upload a JPG, JPEG, PNG or WEBP image.", "error")
            return render_template("item_form.html", item_type=item_type)
        filename = f"{uuid.uuid4().hex}.jpg"
        path = os.path.join(UPLOAD_DIR, filename)
        try:
            # Validate and normalize every accepted format to a JPEG on disk.
            img = Image.open(image.stream)
            img.verify()
            image.stream.seek(0)
            img = Image.open(image.stream).convert("RGB")
            img.thumbnail((1600, 1600))
            img.save(path, "JPEG", quality=88, optimize=True)
            embedding = image_embedding(path)
        except Exception as exc:
            if os.path.exists(path):
                os.remove(path)
            flash("Image processing failed. " + str(exc)[:220], "error")
            return render_template("item_form.html", item_type=item_type)
        db = get_db()
        cur = db.execute("""INSERT INTO items(user_id,item_type,title,category,description,location,event_date,
                            image_filename,embedding,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,'active',?)""",
                         (current_user()["id"], item_type, title, category, description, location,
                          event_date, filename, json.dumps(embedding),
                          datetime.now().isoformat(timespec="seconds")))
        db.commit()
        run_matching(cur.lastrowid)
        flash("Your item was posted. We are checking for visually similar reports.", "success")
        return redirect(url_for("item_detail", item_id=cur.lastrowid))
    return render_template("item_form.html", item_type=item_type)

@app.route("/items")
def items():
    item_type = request.args.get("type", "all")
    category = request.args.get("category", "").strip()
    query = request.args.get("q", "").strip()
    sql = """SELECT items.*, users.name AS owner_name FROM items
             JOIN users ON users.id=items.user_id WHERE items.status!='resolved'"""
    params = []
    if item_type in ("lost", "found"):
        sql += " AND items.item_type=?"; params.append(item_type)
    if category:
        sql += " AND items.category=?"; params.append(category)
    if query:
        sql += " AND (items.title LIKE ? OR items.description LIKE ? OR items.location LIKE ?)"
        params.extend([f"%{query}%"] * 3)
    sql += " ORDER BY items.created_at DESC"
    rows = get_db().execute(sql, params).fetchall()
    return render_template("items.html", items=rows, item_type=item_type, category=category, query=query)

@app.route("/items/<int:item_id>")
def item_detail(item_id):
    item = get_db().execute("""SELECT items.*, users.name AS owner_name, users.email AS owner_email,
                               users.department AS owner_department FROM items
                               JOIN users ON users.id=items.user_id WHERE items.id=?""",
                             (item_id,)).fetchone()
    if not item:
        abort(404)
    matches = get_db().execute("""
        SELECT m.similarity, CASE WHEN l.id=? THEN f.id ELSE l.id END AS other_id,
               CASE WHEN l.id=? THEN f.title ELSE l.title END AS other_title,
               CASE WHEN l.id=? THEN f.image_filename ELSE l.image_filename END AS other_image,
               CASE WHEN l.id=? THEN f.item_type ELSE l.item_type END AS other_type
        FROM matches m JOIN items l ON l.id=m.lost_item_id JOIN items f ON f.id=m.found_item_id
        WHERE l.id=? OR f.id=? ORDER BY m.similarity DESC
    """, (item_id,item_id,item_id,item_id,item_id,item_id)).fetchall()
    return render_template("item_detail.html", item=item, matches=matches)

@app.route("/items/<int:item_id>/resolve", methods=["POST"])
@login_required
def resolve_item(item_id):
    db = get_db()
    item = db.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
    if not item:
        abort(404)
    if item["user_id"] != current_user()["id"]:
        abort(403)
    db.execute("UPDATE items SET status='resolved' WHERE id=?", (item_id,))
    db.commit()
    flash("Item marked as resolved.", "success")
    return redirect(url_for("dashboard"))

@app.route("/notifications")
@login_required
def notifications():
    rows = get_db().execute("""SELECT n.*, i.title AS item_title FROM notifications n
                               LEFT JOIN items i ON i.id=n.item_id
                               WHERE n.user_id=? ORDER BY n.created_at DESC""",
                             (current_user()["id"],)).fetchall()
    get_db().execute("UPDATE notifications SET is_read=1 WHERE user_id=?", (current_user()["id"],))
    get_db().commit()
    return render_template("notifications.html", notifications=rows)

@app.route("/api/notifications/unread")
@login_required
def unread_notifications_api():
    count = get_db().execute("SELECT COUNT(*) FROM notifications WHERE user_id=? AND is_read=0",
                             (current_user()["id"],)).fetchone()[0]
    return jsonify({"count": count})

@app.errorhandler(413)
def too_large(error):
    flash("Image is too large. Maximum upload size is 8 MB.", "error")
    return redirect(request.referrer or url_for("index"))

@app.errorhandler(404)
def not_found(error):
    return render_template("error.html", code=404, message="The page or item could not be found."), 404

@app.errorhandler(403)
def forbidden(error):
    return render_template("error.html", code=403, message="You do not have permission to do that."), 403

with app.app_context():
    init_db()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
