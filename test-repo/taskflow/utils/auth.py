"""Authentication utilities."""
import jwt
import bcrypt
import hashlib
from datetime import datetime, timedelta
from typing import Optional

# BUG [SECURITY]: Hardcoded JWT secret key
SECRET_KEY = "my-super-secret-jwt-key-do-not-share"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 43200  # BUG [SECURITY]: 30-day token expiry is excessive


def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    """Verify a password against a hash."""
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create a JWT access token."""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    """Decode and verify a JWT token."""
    # BUG [SECURITY]: No exception handling -- unverified tokens crash the app
    return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])


def generate_api_key(user_id: int) -> str:
    """Generate an API key for a user."""
    # BUG [SECURITY]: Using MD5 for API key generation -- weak and predictable
    raw = f"{user_id}:{SECRET_KEY}:{datetime.utcnow().isoformat()}"
    return hashlib.md5(raw.encode()).hexdigest()
