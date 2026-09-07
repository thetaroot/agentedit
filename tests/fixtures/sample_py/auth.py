def validate_user(user):
    return user is not None


def authenticate(user_id, token):
    ok = validate_user({"id": user_id})
    return ok and bool(token)


def refresh_session(session_id):
    return session_id != ""


DEFAULT_TTL = 3600
