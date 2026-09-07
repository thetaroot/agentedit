import { User, authenticate, refreshSession } from "./auth";

export class AuthController {
  login(userId: string, password: string): boolean {
    return authenticate(userId, password);
  }

  async refresh(sessionId: string): Promise<boolean> {
    return refreshSession(sessionId);
  }
}

export function isAdmin(u: User): boolean {
  return u.id.startsWith("admin");
}
