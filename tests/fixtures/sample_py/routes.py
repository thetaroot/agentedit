from controller import AuthController


def handle_login(user_id, password):
    controller = AuthController()
    return controller.login(user_id, password)


def handle_refresh(session_id):
    controller = AuthController()
    return controller.refresh(session_id)
