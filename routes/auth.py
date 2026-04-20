import re
from flask import Blueprint, request, jsonify
from flask_jwt_extended import create_access_token
from extensions import db
from models.user import User

auth_bp = Blueprint('auth', __name__, url_prefix='/api/auth')


@auth_bp.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'}), 200


@auth_bp.route('/users', methods=['GET'])
def get_users():
    users = User.query.all()
    return jsonify([u.to_dict() for u in users]), 200

# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(
    r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$'
)


def _validate_username(username: str):
    """Return (ok, error_message). Called after trim."""
    if len(username) > 20:
        return False, 'Provide a shorter username'
    if len(username) < 5 or ' ' in username:
        return False, 'Please enter a valid username'
    return True, None


def _validate_email(email: str):
    """Return (ok, error_message). Called after trim + lower."""
    if len(email) > 254:
        return False, 'Provide a shorter email address'
    if len(email) < 5 or not _EMAIL_RE.match(email):
        return False, 'Please enter a valid email address'
    return True, None


def _validate_password(password: str):
    """Return (ok, error_message).

    Rules:
      - 8–64 characters
      - no spaces
      - at least one uppercase letter
      - at least one lowercase letter
      - at least one digit
    """
    if len(password) < 8 or len(password) > 64:
        return False, 'Password must be between 8 and 64 characters'
    if ' ' in password:
        return False, 'Password must not contain spaces'
    if not re.search(r'[A-Z]', password):
        return False, 'Password must contain at least one uppercase letter'
    if not re.search(r'[a-z]', password):
        return False, 'Password must contain at least one lowercase letter'
    if not re.search(r'[0-9]', password):
        return False, 'Password must contain at least one digit'
    return True, None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@auth_bp.route('/register', methods=['POST'])
def register():
    data = request.get_json(silent=True) or {}

    username_raw = data.get('username', '').strip()
    email_raw = data.get('email', '').strip().lower()
    password = data.get('password', '')
    confirm_password = data.get('confirmPassword', '')
    accept_terms = data.get('acceptTerms', False)

    # ── 1. Required fields ──────────────────────────────────────────────────
    if not all([username_raw, email_raw, password, confirm_password]):
        return jsonify({'error': 'Missing fields'}), 400

    # ── 2. Terms ─────────────────────────────────────────────────────────────
    if not accept_terms:
        return jsonify({'field': 'acceptTerms', 'error': 'You must accept the Terms & Conditions'}), 400

    # ── 3. Format validations ────────────────────────────────────────────────
    ok, err = _validate_username(username_raw)
    if not ok:
        return jsonify({'field': 'username', 'error': err}), 422

    ok, err = _validate_email(email_raw)
    if not ok:
        return jsonify({'field': 'email', 'error': err}), 422

    ok, err = _validate_password(password)
    if not ok:
        return jsonify({'field': 'password', 'error': err}), 422

    if password != confirm_password:
        return jsonify({'field': 'confirmPassword', 'error': 'Passwords do not match'}), 422

    # ── 4. Uniqueness checks ─────────────────────────────────────────────────
    if User.query.filter(db.func.lower(User.username) == username_raw.lower()).first():
        return jsonify({'field': 'username', 'error': 'Username already exists'}), 409

    if User.query.filter_by(email=email_raw).first():
        return jsonify({'field': 'email', 'error': 'An account with this email already exists'}), 409

    # ── 5. Create user ───────────────────────────────────────────────────────
    user = User(
        username=username_raw,
        email=email_raw,
    )
    user.set_password(password)

    db.session.add(user)
    db.session.commit()

    return jsonify({
        'message': 'Account created successfully',
        'user': user.to_dict(),
    }), 201


@auth_bp.route('/login', methods=['POST'])
def login():
    data = request.get_json(silent=True) or {}

    email = data.get('email', '').strip().lower()
    password = data.get('password', '')

    if not email or not password:
        return jsonify({'error': 'Missing fields'}), 400

    user = User.query.filter_by(email=email).first()

    if not user or not user.check_password(password):
        return jsonify({'error': 'Invalid email or password'}), 401

    token = create_access_token(identity=str(user.user_id))

    return jsonify({
        'message': 'Login successful',
        'token': token,
        'user': user.to_dict(),
    }), 200
