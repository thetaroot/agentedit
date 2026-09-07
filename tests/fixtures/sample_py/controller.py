from auth import authenticate, refresh_session


class AuthController:
    def login(self, user_id, password):
        return authenticate(user_id, password)

    def refresh(self, session_id):
        return refresh_session(session_id)


def is_admin(user_id):
    return str(user_id).startswith("admin")
